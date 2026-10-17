import json
import time
import zipfile
import pytest
from card_compass.privacy_export import download_export
from card_compass.connected_client import ConnectionError


def manifest():
    return dict(schemaVersion=1,id='a'*32,created='2026-10-11T12:00:00Z',expires=time.time()+900,chunkCount=2,counts=dict(identity=1,snapshots=1))


class Client:
    def __init__(self):self.calls=[];self.fail_cleanup=False;self.broken=False
    def call(self,method,path):
        self.calls.append((method,path))
        if method=='DELETE':
            if self.fail_cleanup:raise ConnectionError(503,'Controlled cleanup failure')
            return dict(removed=True)
        sequence=int(path.rsplit('/',1)[1]);dataset='identity' if sequence==0 else 'snapshots'
        return dict(id='a'*32,sequence=sequence,records=[] if self.broken and sequence==1 else [dict(dataset=dataset,value=dict(name='Selected source',amountCents=12345))])


def test_atomic_complete_archive_and_failed_cleanup_still_saved(tmp_path):
    client=Client();client.fail_cleanup=True;profile=tmp_path/'profile';profile.mkdir();path=tmp_path/'reviewed.zip'
    result=download_export(client,manifest(),path,profile)
    assert result['saved']==str(path) and result['records']==2 and not result['serverSnapshotRemoved']
    with zipfile.ZipFile(path) as archive:
        assert set(archive.namelist())=={'manifest.json','identity.jsonl','snapshots.jsonl'}
        assert json.loads(archive.read('snapshots.jsonl'))['amountCents']==12345
    assert not list(profile.iterdir()) and client.calls[-1][0]=='DELETE'


def test_interrupted_counts_preserve_existing_archive_and_no_cleanup(tmp_path):
    client=Client();client.broken=True;profile=tmp_path/'profile';profile.mkdir();path=tmp_path/'reviewed.zip';path.write_bytes(b'Existing user archive')
    with pytest.raises(ValueError,match='counts do not match'):download_export(client,manifest(),path,profile)
    assert path.read_bytes()==b'Existing user archive' and len(list(tmp_path.iterdir()))==2
    assert all(method=='GET' for method,path in client.calls)


def test_active_profile_expiry_schema_and_chunk_identity_rejected(tmp_path):
    profile=tmp_path/'profile';profile.mkdir();client=Client()
    with pytest.raises(ValueError,match='outside'):download_export(client,manifest(),profile/'private.zip',profile)
    with pytest.raises(ValueError,match='expired'):download_export(client,dict(manifest(),expires=time.time()-1),tmp_path/'file.zip',profile)
    with pytest.raises(ValueError,match='schema'):download_export(client,dict(manifest(),schemaVersion=True),tmp_path/'file.zip',profile)
    assert not client.calls
    client.call=lambda method,path:dict(id='b'*32,sequence=0,records=[])
    with pytest.raises(ValueError,match='mismatched'):download_export(client,manifest(),tmp_path/'file.zip',profile)
    assert not (tmp_path/'file.zip').exists()


def test_rate_limit_resumes_same_chunk_and_cancellation_preserves_archive(tmp_path):
    client=Client();normal=client.call;attempts=[];pauses=[];progress=[]
    def call(method,path):
        attempts.append(path)
        if len(attempts)==1:raise ConnectionError(429,'Wait a minute')
        return normal(method,path)
    client.call=call;profile=tmp_path/'profile';profile.mkdir();path=tmp_path/'reviewed.zip'
    download_export(client,manifest(),path,profile,progress=progress.append,pause=pauses.append)
    assert len(pauses)==60 and attempts[0]==attempts[1] and any('limit' in message for message in progress)
    before=path.read_bytes()
    with pytest.raises(ValueError,match='canceled'):download_export(client,manifest(),path,profile,canceled=lambda:True)
    assert path.read_bytes()==before
