import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from credential_helper import load_env
from playwright.sync_api import sync_playwright
_, values=load_env(ROOT/'.local/state/.env')
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless=True)
    page=browser.new_page(viewport={'width':1440,'height':1000})
    errors=[]
    page.on('pageerror',lambda error:errors.append(type(error).__name__))
    page.add_init_script('sessionStorage.setItem("factory_admin_token", '+json.dumps(values['FACTORY_ADMIN_TOKEN'])+');')
    page.goto('http://127.0.0.1:18880',wait_until='networkidle')
    page.get_by_text('Pause all',exact=True).wait_for()
    page.screenshot(path=str(ROOT/'.local/web-admin.png'),full_page=True)
    assert not errors, errors
    print(json.dumps({'web_render':'passed','page_errors':len(errors),'screenshot':str(ROOT/'.local/web-admin.png')}))
    browser.close()
