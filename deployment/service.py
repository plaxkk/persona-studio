"""Service loader: secrets stay out of argv, plist, and printed diagnostics."""
import os
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from credential_helper import load_env
os.umask(0o077)
STATE = ROOT / '.local/state'
_, values = load_env(STATE / '.env')
# Do not inherit personal provider/channel credentials from a terminal or launchd.
env = {k: os.environ[k] for k in ('PATH', 'LANG', 'TMPDIR', 'SYSTEMROOT') if k in os.environ}
env.update({k: v for k, v in values.items() if v})
env.update(FACTORY_STATE_DIR=str(STATE), FACTORY_PROFILE='x-persona-hermes',
           HERMES_HOME=str(ROOT / '.local/hermes'), PYTHONUTF8='1', PYTHONIOENCODING='utf-8',
           FACTORY_LIVE_ENABLED='0')
if env.get('HERMES_SOURCE'):
    env['PYTHONPATH'] = env['HERMES_SOURCE']
if not env.get('FACTORY_ADMIN_TOKEN'):
    raise SystemExit('Admin token missing; use credential_helper.py.')
service = sys.argv[1]
if service == 'admin':
    command = [sys.executable, str(ROOT / 'scripts/admin_server.py'), '--host', '127.0.0.1', '--port', '18880', '--state-dir', str(STATE)]
elif service == 'hermes':
    command = [sys.executable, str(ROOT / 'deployment/hermes_bridge.py')]
elif service == 'shadow':
    command = [sys.executable, str(ROOT / 'deployment/shadow_tick.py')]
else:
    raise SystemExit('Unknown service')
os.execve(command[0], command, env)
