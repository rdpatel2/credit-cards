"""Atomic user-selected ZIP/JSONL downloads of bounded service export chunks."""
from collections import Counter
from datetime import datetime,timezone
import json
from pathlib import Path
import re
import tempfile
import time
import zipfile
from .connected_client import ConnectionError

DATASETS={'identity','snapshots','activity','invitations','connections','accounts',
          'investment_accounts','investment_securities','investment_holdings','transactions','consent_sessions'}


def validate_manifest(value):
    if not isinstance(value,dict) or value.get('schemaVersion')!=1 or type(value.get('schemaVersion')) is not int:raise ValueError('The service export has an unsupported schema.')
    if not isinstance(value.get('id'),str) or not re.fullmatch('[a-f0-9]{32}',value['id']):raise ValueError('The service export has invalid identity metadata.')
    if type(value.get('chunkCount')) is not int or not 1<=value['chunkCount']<=100000:raise ValueError('The export chunk count is invalid.')
    if not isinstance(value.get('counts'),dict) or not value['counts'] or set(value['counts'])-DATASETS or any(type(count) is not int or not 0<count<=10000000 for count in value['counts'].values()):raise ValueError('The service export has invalid dataset counts.')
    if not isinstance(value.get('expires'),(int,float)) or isinstance(value['expires'],bool) or not datetime.now(timezone.utc).timestamp()<value['expires']:raise ValueError('The prepared export expired; create a fresh export.')
    created=datetime.fromisoformat(value['created'].replace('Z','+00:00'))
    if created.tzinfo is None:raise ValueError('The export source time needs a time zone.')
    return value


def download_export(client,manifest,path,profile,progress=None,canceled=None,pause=time.sleep):
    validate_manifest(manifest);path=Path(path).expanduser().resolve();profile=Path(profile).resolve()
    if path.suffix.lower()!='.zip' or path==profile or profile in path.parents:raise ValueError('Choose a ZIP archive outside the active desktop profile; existing application data cannot be overwritten.')
    if not path.parent.is_dir():raise ValueError('Choose an existing destination directory.')
    counts=Counter();total=0;handle=tempfile.NamedTemporaryFile(dir=path.parent,suffix='.zip',delete=False);temporary=Path(handle.name);handle.close()
    try:
        with tempfile.TemporaryDirectory(prefix='finance-compass-export-data-') as directory:
            streams={}
            try:
                for sequence in range(manifest['chunkCount']):
                    if canceled and canceled():raise ValueError('Export download canceled; no archive was saved. The prepared service export remains available until expiry.')
                    if progress:progress(f"Downloading export chunk {sequence+1} of {manifest['chunkCount']}…")
                    # Leave space for metadata requests in the service's peer
                    # limit. Retry the same immutable chunk on a limit only.
                    if sequence and manifest['chunkCount']>100:pause(.65)
                    for attempt in range(3):
                        try:
                            chunk=client.call('GET',f"/service/v1/privacy/exports/{manifest['id']}/chunks/{sequence}");break
                        except ConnectionError as exc:
                            if exc.status!=429 or attempt==2:raise
                            if progress:progress('Service request limit reached; waiting up to a minute to resume the same export chunk…')
                            for second in range(60):
                                if canceled and canceled():raise ValueError('Export download canceled; no archive was saved.')
                                if manifest['expires']<=time.time():raise ValueError('Prepared export expired; no archive was saved. Prepare a fresh copy.')
                                pause(1)
                    if chunk.get('id')!=manifest['id'] or type(chunk.get('sequence')) is not int or chunk['sequence']!=sequence or not isinstance(chunk.get('records'),list):raise ValueError('The service returned a mismatched export chunk; no archive was saved.')
                    for row in chunk['records']:
                        if not isinstance(row,dict) or set(row)!={'dataset','value'} or row['dataset'] not in manifest['counts'] or not isinstance(row['value'],dict):raise ValueError('The export contains unsupported records; no partial archive is assumed complete.')
                        dataset=row['dataset'];counts[dataset]+=1
                        if counts[dataset]>manifest['counts'][dataset]:raise ValueError('Export record counts exceed the reviewed manifest; no archive was saved.')
                        encoded=json.dumps(row['value'],ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()+b'\n';total+=len(encoded)
                        if total>1000000000:raise ValueError('Export exceeds 1 GB; no archive was saved.')
                        if dataset not in streams:streams[dataset]=open(Path(directory)/(dataset+'.jsonl'),'wb')
                        streams[dataset].write(encoded)
            finally:
                for stream in streams.values():stream.close()
            if dict(counts)!=manifest['counts']:raise ValueError('Export record counts do not match the manifest. No archive was saved; retry the prepared snapshot before it expires.')
            with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
                for dataset in sorted(streams):archive.write(Path(directory)/(dataset+'.jsonl'),dataset+'.jsonl')
        if canceled and canceled():raise ValueError('Export download canceled; no archive was saved.')
        temporary.replace(path)
    finally:temporary.unlink(missing_ok=True)
    removed=False
    try:removed=client.call('DELETE',f"/service/v1/privacy/exports/{manifest['id']}").get('removed') is True
    except (ConnectionError,OSError):pass # A saved archive must not be reported as failed cleanup.
    return dict(saved=str(path),records=sum(counts.values()),bytes=total,serverSnapshotRemoved=removed,expires=manifest['expires'])
