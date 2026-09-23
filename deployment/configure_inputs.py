"""Collect nonsecret routing only, using the project's credential writer."""
import os
from pathlib import Path
import subprocess
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from credential_helper import load_env, write_env, extract_cookie_editor_values
path = ROOT / '.local/state/.env'
_, current = load_env(path)
updates = {}
for key, label in [('OWNER_TELEGRAM_ID', 'Owner Telegram 数字 ID'), ('MODEL_ID', '模型 ID'),
                   ('MODEL_BASE_URL', '模型 API Base URL（含 /v1）'), ('X_USERNAME', 'X 用户名（不含 @）')]:
    value = input(f'{label}（留空保留）: ').strip()
    if not value:
        continue
    if key == 'OWNER_TELEGRAM_ID' and not value.isdecimal():
        raise SystemExit('Owner ID 必须是数字，未保存。')
    if key == 'MODEL_BASE_URL' and not value.startswith('https://'):
        raise SystemExit('模型地址必须使用 HTTPS，未保存。')
    updates[key] = value
cookie_path = input('可选：你手动导出的 Cookie-Editor JSON 文件路径（留空跳过）: ').strip()
if cookie_path:
    updates.update(extract_cookie_editor_values(Path(cookie_path).expanduser()))
write_env(path, updates)
print('已安全保存。继续保持 shadow + pause；请回到 Codex 完成联网验收。')
