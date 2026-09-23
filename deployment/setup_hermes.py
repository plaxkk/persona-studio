"""Apply the upstream installer layout, then isolate the Hermes runtime."""
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from credential_helper import load_env, write_env

os.umask(0o077)
subprocess.run([sys.executable, str(ROOT / 'deployment/setup_local.py')], check=True)
state = ROOT / '.local/state'
hermes_home = ROOT / '.local/hermes'
hermes_home.mkdir(exist_ok=True)
hermes_bin = Path(shutil.which('hermes')).resolve()
hermes_python = hermes_bin.parent / 'python3'
hermes_source = hermes_bin.parents[2]
subprocess.run([sys.executable, str(ROOT / 'scripts/credential_helper.py'), '--env', str(state / '.env'), '--generate-admin-token'], check=True)
write_env(state / '.env', {'HERMES_PYTHON': str(hermes_python), 'HERMES_SOURCE': str(hermes_source),
    'PERSONA_PATH': str(hermes_home / 'skills/x-persona')})
# .env is only read by our service loader, never sourced in a shell.
os.environ['FACTORY_STATE_DIR'] = str(state)
import admin_server
admin_server.init_db()
with admin_server.db() as conn:
    for key, value in {'shadow_mode': True, 'pause_all': True, 'daily_posts': 2,
        'likes_per_day': 5, 'reposts_per_day': 2, 'quotes_per_day': 1,
        'follows_per_day': 1, 'max_replies_per_hour': 2, 'browse_interval_min_minutes': 30}.items():
        admin_server.set_config(conn, key, value)
factory = json.loads((state / 'factory.json').read_text())
factory['runtime'] = 'hermes'
factory['automation'].update(shadow_mode=True, pause_all=True, telegram_bridge=True)
(state / 'factory.json').write_text(json.dumps(factory, indent=2))
# Empty runtime config prevents accidental inheritance from the user's Hermes profile.
(hermes_home / 'config.yaml').write_text('toolsets: []\nmcp_servers: {}\n')
for service in ('admin', 'hermes', 'shadow'):
    label = f'local.x-persona.{service}'
    data = {'Label': label,
        'ProgramArguments': [str(ROOT / '.venv/bin/python'), str(ROOT / 'deployment/service.py'), service],
        'WorkingDirectory': str(ROOT), 'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 30,
        'EnvironmentVariables': {'PATH': f'{Path(shutil.which("node")).parent}:/usr/local/bin:/usr/bin:/bin'},
        'StandardOutPath': str(ROOT / f'.local/logs/{service}.stdout.log'),
        'StandardErrorPath': str(ROOT / f'.local/logs/{service}.stderr.log'), 'Umask': 63}
    if service == 'shadow':
        data['KeepAlive'] = False
        data['StartInterval'] = 1800
    with (ROOT / f'.local/launchd/{label}.plist').open('wb') as f:
        plistlib.dump(data, f)
print('Hermes profile prepared. shadow=true pause=true. Live activation locked.')
