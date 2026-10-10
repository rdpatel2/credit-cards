# Native finance workspace; Python and Qt only.
from PyInstaller.utils.hooks import collect_data_files

datas = collect_data_files('card_compass') + collect_data_files('exchange_calendars') + collect_data_files('tzdata')
a = Analysis(['run_app.py'], pathex=[], binaries=[], datas=datas,
             hiddenimports=[],
             hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='FinanceCompass',
          debug=False, strip=False, upx=True, console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=True, name='FinanceCompass')
