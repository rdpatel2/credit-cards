"""Portable backups of user data, with validated restore and a recovery copy."""
from datetime import datetime, timezone
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import uuid
from zipfile import ZipFile, ZIP_DEFLATED

from .investments import validate_workspace

FILES = ('profile.json', 'transactions.sqlite3', 'investments-workspace.json')


def create_backup(directory, destination):
    directory, destination = Path(directory), Path(destination)
    if destination.resolve() in [(directory / name).resolve() for name in FILES]:
        raise ValueError('Choose a separate backup file.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        staging = Path(temp)
        for name in FILES:
            source = directory / name
            if not source.exists():
                continue
            if name.endswith('sqlite3'):
                with closing(sqlite3.connect(source)) as db, closing(sqlite3.connect(staging / name)) as copy:
                    db.backup(copy)
            else:
                (staging / name).write_bytes(source.read_bytes())
        names = [name for name in FILES if (staging / name).exists()]
        with tempfile.NamedTemporaryFile(dir=destination.parent, suffix='.zip', delete=False) as handle:
            archive = Path(handle.name)
        try:
            with ZipFile(archive, 'w', ZIP_DEFLATED) as output:
                output.writestr('manifest.json', json.dumps({'format': 'finance-compass', 'version': 1, 'created': datetime.now(timezone.utc).isoformat(), 'files': names}))
                for name in names:
                    output.write(staging / name, name)
            archive.replace(destination)
        finally:
            archive.unlink(missing_ok=True)


def read_backup(path):
    with ZipFile(path) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)) or set(names) - set(FILES) - {'manifest.json'} or sum(i.file_size for i in infos) > 250_000_000:
            raise ValueError('This is not a supported Finance Compass backup.')
        manifest = json.loads(archive.read('manifest.json'))
        if manifest.get('format') != 'finance-compass' or manifest.get('version') != 1 or set(manifest.get('files', [])) != set(names) - {'manifest.json'}:
            raise ValueError('The backup manifest is invalid.')
        data = {name: archive.read(name) for name in FILES if name in names}
    if not {'profile.json', 'transactions.sqlite3'} <= set(data):
        raise ValueError('The backup is missing your profile or transaction history.')
    profile = json.loads(data['profile.json'])
    if not isinstance(profile, dict) or not isinstance(profile.get('wallet', []), list) or not all(isinstance(x, str) for x in profile.get('wallet', [])):
        raise ValueError('The saved profile is invalid.')
    for key in ('spend', 'coverage', 'card_point_values', 'card_settings'):
        if not isinstance(profile.get(key, {}), dict):
            raise ValueError(f'The saved {key} settings are invalid.')
    from .engine import recommend
    from .catalog import CatalogStore, default_profile, validate_catalog
    merged = {**default_profile(), **profile}
    custom = merged.get('custom_cards', [])
    if not isinstance(custom, list):
        raise ValueError('The saved custom cards are invalid.')
    if custom:
        validate_catalog({'cards': custom})
    try:
        # Exercise the same calculations used at startup before replacing files.
        recommend(CatalogStore().load()['cards'] + custom, merged)
    except (TypeError, KeyError, ValueError, OverflowError) as exc:
        raise ValueError('The saved reward settings cannot be loaded.') from exc
    if 'investments-workspace.json' in data:
        validate_workspace(json.loads(data['investments-workspace.json']))
    with tempfile.TemporaryDirectory() as temp:
        db_path = Path(temp) / 'check.sqlite3'
        db_path.write_bytes(data['transactions.sqlite3'])
        with closing(sqlite3.connect(db_path)) as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('The transaction database is damaged.')
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'transactions', 'statements', 'accounts', 'cash_entries', 'cash_postings', 'budget_targets'} <= tables:
                raise ValueError('The backup is missing finance tables.')
            if db.execute('PRAGMA foreign_key_check').fetchone():
                raise ValueError('The backup has broken transaction links.')
            from .planning import validate_planning_tables
            validate_planning_tables(db)
            from .shared_sync import validate_tables as validate_service_copies
            validate_service_copies(db)
            from .institution_balances import validate_tables as validate_institution_balances
            validate_institution_balances(db)
            from .cash_forecast import validate_table
            validate_table(db)
            from .recurring import validate_table as validate_recurring
            validate_recurring(db)
            from .retirement import validate_table as validate_retirement
            validate_retirement(db)
            from .automations import validate_table as validate_automations
            validate_automations(db)
    return manifest, data


def restore_backup(directory, path):
    directory = Path(directory)
    _, data = read_backup(path)
    recovery = directory / ('before-restore-' + uuid.uuid4().hex[:10] + '.zip')
    create_backup(directory, recovery)
    old = {name: (directory / name).read_bytes() if (directory / name).exists() else None for name in FILES}
    try:
        for name in FILES:
            if name in data:
                temporary = directory / (name + '.restore')
                temporary.write_bytes(data[name])
                temporary.replace(directory / name)
            else:
                (directory / name).unlink(missing_ok=True)
        # Export-folder consent is device-specific. Restore data and history,
        # but pause jobs before a new app session can check due schedules.
        from .automations import pause_restored_jobs
        with sqlite3.connect(directory / 'transactions.sqlite3') as db:
            pause_restored_jobs(db)
    except Exception:
        for name, content in old.items():
            if content is not None:
                (directory / name).write_bytes(content)
            else:
                (directory / name).unlink(missing_ok=True)
        raise
    return recovery
