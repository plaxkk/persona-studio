"""Disposable desktop website regression, simulated extension + model only."""
import json, mimetypes, tempfile
from pathlib import Path
from urllib.parse import urlparse
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright, expect
from studio.api import create_app
from studio.desktop import desktop_route
from studio import codex_persona

ROOT=Path(__file__).resolve().parents[1]
ORIGIN='https://persona-studio-plaxkk.vercel.app'
codex_persona.preflight=lambda:dict(installed=True,ready=True,model='fixture',reasoning='medium',message='模拟登录')
with tempfile.TemporaryDirectory() as tmp:
 app=create_app(Path(tmp), "http://testserver")
 with TestClient(app) as client:
  client.post('/api/v1/auth/setup',json={'password':'fixture-password-only'})
  csrf=client.get('/api/v1/auth/session').json()['csrf']
  with app.state.store.db() as c:
   c.execute("UPDATE engines SET status='ready',config=? WHERE id='codex'",(json.dumps({'model':'fixture','auth':'chatgpt','reasoning':'medium'}),))
  app.state.store.set('engine','codex')
  with sync_playwright() as p:
   chrome=sorted((Path.home()/'Library/Caches/ms-playwright').glob('chromium-*/chrome-mac-x64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing'))[-1]
   browser=p.chromium.launch(executable_path=str(chrome),headless=True)
   page=browser.new_page(viewport={'width':1440,'height':1000})
   errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
   version=[None];requests=[]
   def bridge(route):
    message=route.request.post_data_json
    if message['type']=='desktop.status':
     return route.fulfill(json={'ok':True, **({'extensionVersion':version[0]} if version[0] else {})})
    path,method=message['path'],message.get('method','GET')
    if not version[0] and path.startswith('/engines/codex/'):
     return route.fulfill(json={'ok':False,'code':'forbidden','message':'此操作只能在本机设置中完成。'})
    assert desktop_route('/api/v1'+path,method)
    requests.append((method,path))
    result=client.request(method,'/api/v1'+path,headers={'X-CSRF-Token':csrf},json=message.get('body'))
    route.fulfill(json={'ok':True,'status':result.status_code,'data':result.json()})
   def website(route):
    file=ROOT/'.vercel-dist'/((urlparse(route.request.url).path.lstrip('/')) or 'index.html')
    route.fulfill(body=file.read_bytes(),content_type=mimetypes.guess_type(str(file))[0] or 'application/octet-stream')
   page.route(ORIGIN+'/**',website)
   page.route(ORIGIN+'/fixture-bridge',bridge)
   page.add_init_script("Object.defineProperty(window,'chrome',{value:{runtime:{sendMessage(_id,message,done){fetch('/fixture-bridge',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(message)}).then(r=>r.json()).then(done)}}}})")
   page.goto(ORIGIN+'/#settings/engines')
   link=page.locator('.desktop-strip .extension-download')
   expect(link).to_be_visible()
   card=page.locator('article.engine-card').filter(has=page.get_by_role('heading',name='Codex',exact=True))
   card.get_by_role('button',name='测试连接',exact=True).click()
   expect(page.get_by_role('heading',name='安装 / 更新连接助手',exact=True)).to_be_visible()
   expect(page.get_by_text('连接助手需要更新至 1.3.4。',exact=False)).to_be_visible()
   assert not any(path=='/engines/codex/verify' for _,path in requests)
   page.screenshot(path='/private/tmp/extension-upgrade-desktop.png',full_page=True)
   version[0]='1.3.4'
   page.get_by_role('button',name='我已更新，重新检测').click()
   expect(page.get_by_text('当前版本：1.3.4',exact=False)).to_be_visible()
   page.get_by_role('button',name='关闭更新指南').click()
   card.get_by_role('button',name='测试连接',exact=True).click()
   expect(page.get_by_text('已开始 Codex 真实连接测试',exact=True)).to_be_visible()
   assert ('POST','/engines/codex/verify') in requests
   page.set_viewport_size({'width':390,'height':844})
   expect(link).to_be_visible()
   page.get_by_role('button',name='安装 / 更新指南',exact=True).click()
   expect(page.get_by_role('button',name='关闭更新指南')).to_be_visible()
   assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
   page.screenshot(path='/private/tmp/extension-upgrade-mobile.png',full_page=True)
   assert not errors,errors
   browser.close()
   print('PASS legacy error -> visible update guide; version recheck -> Codex request accepted; persistent desktop/mobile download; no page errors')
