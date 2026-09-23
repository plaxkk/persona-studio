"""Create an isolated macOS profile using the upstream installer's renderers."""
import json
import os
from pathlib import Path
import plistlib
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import installer
from credential_helper import write_env

os.umask(0o077)
STATE = ROOT / '.local/state'
STATE.mkdir(parents=True, exist_ok=True)
for name in ('logs', 'launchd', 'inputs'):
    (ROOT / '.local' / name).mkdir(exist_ok=True)
cfg = installer.InstallConfig(
    profile='x-persona', openclaw_version='latest', deployment_mode='local',
    install_root=str(ROOT), state_dir=str(STATE), home_dir=str(STATE),
    workspace_dir=str(STATE / 'workspace'), gateway_port=18790, admin_port=18880,
    owner_telegram_id='', owner_username='', telegram_bot_token_env='TELEGRAM_BOT_TOKEN',
    telegram_bot_username='', model_provider='pending', model_id='pending',
    model_base_url='', model_api_key_env='MODEL_API_KEY')
cfg.automation.update(shadow_mode=True, pause_all=True)
plan = installer.build_plan(cfg)
# No credentials yet: runnable gateway with no channel or external model provider.
config = plan['files'][str(STATE / 'openclaw.json')]
config.pop('env', None)
config.pop('mcp', None)
config.pop('models', None)
config['agents']['defaults'].pop('model', None)
config['channels'] = {}
config['tools'] = {'deny': ['*']}
config['logging'] = {'level': 'info', 'file': str(ROOT / '.local/logs/gateway.log'), 'redactSensitive': 'tools'}
for path, value in plan['files'].items():
    p = Path(path)
    if p.exists():
        continue
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2))
    p.chmod(0o600)
(STATE / 'deployment-plan.json').write_text(json.dumps({k: v for k, v in plan.items() if k != 'files'}, indent=2))
if not (STATE / '.env').exists():
    shutil.copyfile(STATE / '.env.example', STATE / '.env')
    (STATE / '.env').chmod(0o600)
node_dir = str(Path(shutil.which('node')).parent)
for service in ('admin', 'gateway'):
    label = 'local.x-persona.' + service
    payload = {
        'Label': label,
        'ProgramArguments': [str(ROOT / '.venv/bin/python'), str(ROOT / 'deployment/service.py'), service],
        'WorkingDirectory': str(ROOT),
        'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 15,
        'EnvironmentVariables': {'PATH': f'{ROOT}/.runtime/node_modules/.bin:{node_dir}:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'},
        'StandardOutPath': str(ROOT / '.local/logs' / (service + '.stdout.log')),
        'StandardErrorPath': str(ROOT / '.local/logs' / (service + '.stderr.log')),
        'Umask': 63,
    }
    with (ROOT / '.local/launchd' / (label + '.plist')).open('wb') as f:
        plistlib.dump(payload, f)
print('Local profile and launchd definitions prepared; credentials were not displayed.')
