import os
from pathlib import Path
import shutil
import subprocess
import time
ROOT = Path(__file__).resolve().parents[1]
folder = Path.home() / 'Library/LaunchAgents'
folder.mkdir(parents=True, exist_ok=True)
for service in ('admin', 'hermes', 'shadow'):
    label = f'local.x-persona.{service}'
    target = folder / f'{label}.plist'
    source = ROOT / '.local/launchd' / target.name
    if target.exists() and str(ROOT) not in target.read_text():
        raise SystemExit(f'Refusing to replace unrelated service: {label}')
    subprocess.run(['launchctl', 'bootout', f'gui/{os.getuid()}/{label}'], capture_output=True)
    shutil.copyfile(source, target)
    target.chmod(0o600)
    for attempt in range(10):
        result = subprocess.run(['launchctl', 'bootstrap', f'gui/{os.getuid()}', str(target)], capture_output=True)
        if result.returncode == 0:
            break
        time.sleep(1)
    else:
        raise SystemExit(f'Unable to bootstrap {label}; exit={result.returncode}')
    print(label + ': registered')
