"""Real extension + desktop web build; only isolated state and simulated engines."""
import asyncio
import mimetypes
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlparse
import uvicorn
from playwright.async_api import async_playwright, expect
from studio.api import create_app
from studio.desktop import CLOUD_ORIGIN
from studio.browser_identity import EXTENSION_ID
from studio.engines import EngineResult

class FixtureEngine:
    def __init__(self, ident):self.id=ident
    async def execute(self, request, config, task_id):return EngineResult('模拟本机 Agent 回复：已收到你的想法。',self.id)

async def main():
    with tempfile.TemporaryDirectory(prefix='desktop-e2e-') as folder:
        app=create_app(Path(folder)/'state','http://127.0.0.1:18482')
        with app.state.store.db() as c:c.execute("UPDATE engines SET status='ready'")
        for engine in ['hermes','openclaw']:app.state.jobs.engines.adapters[engine]=FixtureEngine(engine)
        app.state.store.set('worker_heartbeat',__import__('time').time())
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=18482,log_level='critical',access_log=False))
        thread=threading.Thread(target=server.run,daemon=True);thread.start()
        while not server.started:await asyncio.sleep(.05)
        async def worker():
            while True:
                job=app.state.jobs.claim()
                if job:await app.state.jobs.run(job)
                await asyncio.sleep(.1)
        worker_task=asyncio.create_task(worker())
        async with async_playwright() as p:
            default=Path(p.chromium.executable_path)
            installed=sorted((Path.home()/'Library/Caches/ms-playwright').glob('chromium-*/chrome-mac-x64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing'))
            executable=str(default if default.exists() else installed[-1])
            ext=str(Path('browser-extension').resolve())
            context=await p.chromium.launch_persistent_context(str(Path(folder)/'chrome'),headless=True,executable_path=executable,args=[f'--disable-extensions-except={ext}',f'--load-extension={ext}'])
            network=[]
            context.on('request',lambda r:network.append((r.method,r.url)))
            async def website(route):
                path=urlparse(route.request.url).path.lstrip('/') or 'index.html'
                file=(Path('.vercel-dist')/path).resolve()
                if not file.is_relative_to(Path('.vercel-dist').resolve()) or not file.is_file():
                    return await route.fulfill(status=404,body='Not found')
                await route.fulfill(body=file.read_bytes(),content_type=mimetypes.guess_type(str(file))[0] or 'application/octet-stream')
            await context.route(CLOUD_ORIGIN+'/**',website)
            try:
                await context.request.post('http://127.0.0.1:18482/api/v1/auth/setup',data={'password':'fixture-desktop-password'})
                page=await context.new_page();await page.goto(CLOUD_ORIGIN)
                await expect(page.get_by_role('heading',name='把你的电脑接入人格工作室')).to_be_visible()
                popup=await context.new_page();await popup.goto(f'chrome-extension://{EXTENSION_ID}/popup.html')
                await popup.locator('#origin').fill('http://127.0.0.1:18482')
                await popup.locator('#password').fill('fixture-desktop-password')
                async with context.expect_page() as opened:await popup.locator('#desktop-allow').click()
                web=await opened.value
                await web.goto(CLOUD_ORIGIN)
                await expect(web.get_by_text('已连接这台电脑 · Agent 与数据在本机运行')).to_be_visible(timeout=15000)
                await web.get_by_role('button',name='写推文',exact=True).click()
                text='这是模拟桌面草稿\n中文 🌱 & 特殊字符'
                await web.get_by_label('给角色一个方向').fill(text)
                await web.get_by_role('button',name='自己写，保存草稿').click()
                await expect(web.get_by_label('草稿正文')).to_have_value(text)
                assert app.state.store.rows('SELECT text FROM drafts')[0]['text']==text
                await web.get_by_role('button',name='我的人格',exact=True).click()
                await web.get_by_label('试聊消息').fill('这是模拟试聊')
                await web.get_by_label('发送试聊').click()
                await expect(web.get_by_text('模拟本机 Agent 回复：已收到你的想法。',exact=True)).to_be_visible(timeout=15000)
                await web.get_by_role('button',name='设置',exact=True).click()
                await expect(web.get_by_role('heading',name='账号连接保留在本机')).to_be_visible()
                assert await web.locator('input[type=password]').count()==0
                await web.get_by_role('button',name='写作引擎',exact=True).click()
                await web.get_by_role('button',name='切换到此引擎').click()
                for _ in range(50):
                    if app.state.store.get('engine')=='openclaw':break
                    await asyncio.sleep(.1)
                assert app.state.store.get('engine')=='openclaw'
                await web.get_by_role('button',name='工作室',exact=True).click()
                await web.get_by_role('button',name='开始运行',exact=True).click()
                await web.get_by_role('button',name='暂停运行',exact=True).click()
                await expect(web.get_by_role('button',name='开始运行',exact=True)).to_be_visible()
                assert app.state.store.get('paused') is True
                # Transport rejects credential management and path traversal before fetching.
                async def rpc(message):
                    return await web.evaluate('([id,message])=>new Promise(resolve=>chrome.runtime.sendMessage(id,message,resolve))',[EXTENSION_ID,message])
                denied=await rpc({'type':'desktop.api','path':'/connections/x','method':'PUT','body':{}})
                assert denied['code']=='forbidden'
                assert (await rpc({'type':'desktop.api','path':'/../.env','method':'GET'}))['code']=='forbidden'
                assert (await rpc({'type':'desktop.api','path':'/auth/session','method':'GET'}))['data'].get('token') is None
                await web.screenshot(path='/private/tmp/persona-desktop-cloud.png',full_page=True)
                await web.set_viewport_size({'width':390,'height':844})
                await web.wait_for_timeout(300)
                assert await web.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                await web.screenshot(path='/private/tmp/persona-desktop-cloud-mobile.png',full_page=True)
                # Unexpected disconnect preserves the compose editor in memory.
                await web.set_viewport_size({'width':1280,'height':900})
                await web.get_by_role('button',name='写推文',exact=True).click()
                await web.get_by_label('给角色一个方向').fill('断线后要保留的想法')
                await web.evaluate("window.dispatchEvent(new Event('desktop-disconnected'))")
                await expect(web.get_by_role('heading',name='把你的电脑接入人格工作室')).to_be_visible()
                server.should_exit=True;thread.join(timeout=5)
                await web.get_by_role('button',name='我已授权，连接这台电脑').click()
                await expect(web.get_by_role('status')).to_contain_text('本机工作室未连接')
                server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=18482,log_level='critical',access_log=False))
                thread=threading.Thread(target=server.run,daemon=True);thread.start()
                while not server.started:await asyncio.sleep(.05)
                await web.get_by_role('button',name='我已授权，连接这台电脑').click()
                await expect(web.get_by_label('给角色一个方向')).to_have_value('断线后要保留的想法')
                # Revocation is enforced in the backend and extension session.
                await web.get_by_role('button',name='断开',exact=True).click()
                await expect(web.get_by_role('heading',name='把你的电脑接入人格工作室')).to_be_visible()
                assert app.state.store.rows('SELECT * FROM desktop_tokens')==[]
                assert not any(url.startswith(CLOUD_ORIGIN+'/api/') for _,url in network)
                assert all(method=='GET' for method,url in network if url.startswith(CLOUD_ORIGIN))
                print('PASS desktop web + real Chromium extension: authorization, local drafts, simulated local Agent chat, engine switch, pause, denied sensitive routes, reconnect preserves editing, revocation, no cloud API traffic, desktop/mobile layouts.')
            finally:
                await context.close();worker_task.cancel();server.should_exit=True;thread.join(timeout=5)
if __name__=='__main__':asyncio.run(main())
