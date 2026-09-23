"""Owner-only Telegram transport using Hermes and the factory pause/audit API."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from telegram_bridge import TelegramAPI, split_reply
from automation_runner import api_json


def owner_allowed(message, owner):
    return (bool(owner) and owner.isdecimal() and
            message.get('chat', {}).get('type') == 'private' and
            str(message.get('chat', {}).get('id')) == owner and
            str(message.get('from', {}).get('id')) == owner and
            not message.get('from', {}).get('is_bot', False))


def admin(path, payload=None):
    return api_json('http://127.0.0.1:18880', path, payload, os.environ['FACTORY_ADMIN_TOKEN'])


def audit(action, reason):
    admin('/api/audit', {'action': action, 'reason': reason, 'sent': False, 'shadow': True, 'risk': 'low'})


def main():
    os.umask(0o077)
    required = ['TELEGRAM_BOT_TOKEN', 'OWNER_TELEGRAM_ID', 'MODEL_API_KEY', 'MODEL_ID', 'MODEL_BASE_URL']
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        # Launchd keeps this gated process alive without repeatedly contacting Telegram.
        print('WAITING_FOR_INPUT: ' + ', '.join(missing), flush=True)
        while True:
            time.sleep(60)
    owner = os.environ['OWNER_TELEGRAM_ID']
    if not owner.isdecimal():
        raise SystemExit('OWNER_TELEGRAM_ID must be numeric')
    api = TelegramAPI(os.environ['TELEGRAM_BOT_TOKEN'], timeout=45)
    # Never silently remove another application's webhook.
    if api.call('getWebhookInfo', {}).get('result', {}).get('url'):
        raise SystemExit('Existing Telegram webhook; owner migration required')
    state = ROOT / '.local/state/telegram-offset.json'
    offset = json.loads(state.read_text()).get('offset') if state.exists() else None
    history = []
    while True:
        try:
            for update in api.get_updates(offset, 25):
                offset = int(update['update_id']) + 1
                message = update.get('message') or {}
                if not owner_allowed(message, owner):
                    audit('telegram_rejected', 'non_owner_or_group')
                elif text := message.get('text', '').strip():
                    if text == '/pause':
                        admin('/api/config/feature', {'key': 'pause_all', 'enabled': True})
                        audit('pause', 'owner_command')
                        api.send_message(owner, '已暂停。')
                    elif text == '/status':
                        features = admin('/api/config')['features']
                        api.send_message(owner, f"shadow={features['shadow_mode']}, pause={features['pause_all']}")
                    elif admin('/api/config')['features']['pause_all']:
                        audit('telegram_skipped', 'pause_all')
                    else:
                        child_env = {k: v for k, v in os.environ.items() if k not in
                                     ('TELEGRAM_BOT_TOKEN', 'X_AUTH_TOKEN', 'X_CT0', 'FACTORY_ADMIN_TOKEN')}
                        proc = subprocess.run([os.environ['HERMES_PYTHON'], str(ROOT / 'deployment/hermes_worker.py')],
                            input=json.dumps({'text': text, 'history': history[-12:]}), text=True,
                            capture_output=True, timeout=180, cwd=ROOT / '.local/hermes', env=child_env)
                        if proc.returncode:
                            audit('telegram_error', 'hermes_worker_failed')
                        else:
                            reply = json.loads(proc.stdout)['reply']
                            # Recheck pause after inference, before any outbound message.
                            if not admin('/api/config')['features']['pause_all']:
                                for part in split_reply(reply):
                                    api.send_message(owner, part, message.get('message_id'))
                                history.extend([{'role': 'user', 'content': text}, {'role': 'assistant', 'content': reply}])
                                history = history[-12:]
                                audit('telegram_reply', 'owner_private_hermes')
                state.write_text(json.dumps({'offset': offset}))
        except Exception as exc:
            print('bridge_error=' + type(exc).__name__, flush=True)
            time.sleep(5)


if __name__ == '__main__':
    main()
