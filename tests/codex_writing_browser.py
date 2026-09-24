"""Disposable UI acceptance: verify and switch writing chains, desktop/mobile."""
import asyncio, contextlib, json, socket, sys, tempfile, threading, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio.api import create_app
from studio.engines import CodexEngine, EngineResult
from studio import codex_persona
from studio.security import Secrets
from playwright.sync_api import sync_playwright, expect
import uvicorn

ROOT=Path(__file__).resolve().parents[1]
codex_persona.preflight=lambda: dict(installed=True,ready=True,model='gpt-6-astra',reasoning='medium',message='模拟本机 ChatGPT 登录已检测')
async def execute(self, request, config, task_id):
    return EngineResult('模拟连接成功','codex')
CodexEngine.execute=execute
async def loop(app):
    while True:
        task=app.state.jobs.claim()
        if task: await app.state.jobs.run(task)
        await asyncio.sleep(.1)

with tempfile.TemporaryDirectory(prefix='codex-writing-ui-') as tmp:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    base=f'http://127.0.0.1:{port}'
    app=create_app(Path(tmp),base)
    Secrets(app.state.store.root).update({'ENGINE_HERMES_API_KEY':'synthetic-test-key'})
    with app.state.store.db() as c:
        c.execute("UPDATE engines SET status='ready',config=? WHERE id='hermes'",(json.dumps({'model':'example-model','base_url':'https://example.com'}),))
    @contextlib.asynccontextmanager
    async def lifespan(app):
        task=asyncio.create_task(loop(app))
        yield
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError): await task
    app.router.lifespan_context=lifespan
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='critical',access_log=False))
    threading.Thread(target=server.run,daemon=True).start()
    for _ in range(100):
        if server.started: break
        time.sleep(.1)
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless=True,args=['--disable-gpu'])
            page=browser.new_page(viewport={'width':1440,'height':1100})
            errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(base+'/#settings/engines')
            page.get_by_label('管理员密码',exact=True).fill('browser-test-password')
            page.get_by_label('再次输入密码',exact=True).fill('browser-test-password')
            page.get_by_role('button',name='创建工作室',exact=True).click()
            card=page.locator('article.engine-card').filter(has=page.get_by_role('heading',name='Codex',exact=True))
            expect(card).to_be_visible()
            expect(card.locator('input')).to_have_count(0)
            card.get_by_role('button',name='测试连接',exact=True).click()
            button=card.get_by_role('button',name='切换到 Codex',exact=True)
            expect(button).to_be_enabled(timeout=20000)
            button.click()
            expect(card.get_by_text('正在使用',exact=True)).to_be_visible(timeout=15000)
            assert app.state.store.get('engine')=='codex'
            page.screenshot(path=str(ROOT/'.local/codex-writing-desktop.png'),full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            page.screenshot(path=str(ROOT/'.local/codex-writing-mobile.png'),full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.set_viewport_size({'width':1440,'height':1100})
            hermes=page.locator('article.engine-card').filter(has=page.get_by_role('heading',name='Hermes',exact=True))
            hermes.get_by_role('button',name='切换到此引擎',exact=True).click()
            expect(hermes.get_by_text('正在使用',exact=True)).to_be_visible(timeout=15000)
            assert app.state.store.get('engine')=='hermes'
            assert not errors,errors
            browser.close()
            print('PASS Codex verification and bidirectional engine switching; desktop/mobile screenshots; no page errors')
    finally:
        server.should_exit=True
