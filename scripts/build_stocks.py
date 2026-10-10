"""Build Folio's self-contained Node server for the desktop investments module."""
from pathlib import Path
import shutil
import subprocess

root = Path(__file__).resolve().parents[1] / 'stocks'
subprocess.run([shutil.which('npm') or 'npm', 'run', 'build'], cwd=root, check=True)
output = root / '.next' / 'standalone'
shutil.copytree(root / '.next' / 'static', output / '.next' / 'static', dirs_exist_ok=True)
if (root / 'public').exists():
    shutil.copytree(root / 'public', output / 'public', dirs_exist_ok=True)
print('Investments ready. Launch python -m card_compass.')
