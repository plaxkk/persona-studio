"""Isolated browser acceptance for the complete inspiration-to-final workflow."""
import asyncio
import contextlib
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from studio.api import create_app
from studio.engines import EngineError, EngineResult


class FixtureEngine:
    def __init__(self):
        self.requests = []
        self.fail = False

    async def execute(self, request, config, task_id):
        self.requests.append(request)
        await asyncio.sleep(0.35)
        if self.fail:
            raise EngineError('engine_failed')
        if '只输出推文正文' in request.text:
            text = '今天给提醒工具补上了失败重试。写出来不难，让它在我忘记的时候还记得，才难。'
            if '自嘲' in request.text:
                text = '给提醒工具补上失败重试。代码记得重试，我却总忘记喝水。'
        else:
            text = '三个角度：可靠性、注意力、使用习惯。你遇到过什么具体的失败？'
        return EngineResult(text, 'hermes')


with tempfile.TemporaryDirectory(prefix='creation-ui-') as tmp:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    app = create_app(Path(tmp), base)
    engine = FixtureEngine()
    app.state.jobs.engines.adapters['hermes'] = engine
    with app.state.store.db() as c:
        c.execute("UPDATE engines SET status='ready' WHERE id='hermes'")

    async def worker():
        while True:
            job = app.state.jobs.claim()
            if job:
                await app.state.jobs.run(job)
            await asyncio.sleep(.1)

    @contextlib.asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(worker())
        yield
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    app.router.lifespan_context = lifespan
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='critical', access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(.1)
    try:
        with sync_playwright() as p:
            bundled = Path(p.chromium.executable_path)
            executable = str(bundled if bundled.exists() else Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'))
            browser = p.chromium.launch(executable_path=executable, headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(base)
            page.get_by_label('管理员密码', exact=True).fill('creation-ui-password')
            page.get_by_label('再次输入密码', exact=True).fill('creation-ui-password')
            page.get_by_role('button', name='创建工作室', exact=True).click()
            page.get_by_role('button', name='写推文', exact=True).click()
            idea = '今天给提醒工具加上失败重试'
            page.get_by_label('灵感、观察或工作片段').fill(idea)
            page.get_by_role('button', name='仅暂存灵感', exact=True).click()
            card = page.locator('.creation-card').filter(has_text=idea)
            expect(card).to_be_visible()
            assert not engine.requests
            page.reload()
            page.get_by_role('button', name='写推文', exact=True).click()
            page.locator('.creation-card').filter(has_text=idea).click()
            page.get_by_role('button', name='帮我找三个角度', exact=True).click()
            expect(page.locator('.creation-message.assistant')).to_have_count(1, timeout=15000)
            prompt = page.get_by_label('继续讨论或提出修改意见')
            prompt.fill('选可靠性角度，我确实遇到过服务超时')
            page.get_by_role('button', name='生成候选稿', exact=True).click()
            candidate = page.get_by_label('推文正文', exact=True)
            expect(candidate).to_have_value('今天给提醒工具补上了失败重试。写出来不难，让它在我忘记的时候还记得，才难。', timeout=15000)
            assert len(engine.requests[-1].history) == 2
            assert not app.state.store.rows('SELECT * FROM drafts')
            manual = '我手动补充：遇到的是网络超时，不能保证每次成功。'
            candidate.fill(manual)
            expect(page.locator('.creation-preview small')).to_contain_text('已保存', timeout=10000)
            assert app.state.store.rows('SELECT candidate FROM creations')[0]['candidate'] == manual
            page.get_by_role('button', name='内容库', exact=True).click()
            page.get_by_role('button', name='写推文', exact=True).click()
            expect(page.get_by_label('推文正文', exact=True)).to_have_value(manual)
            page.reload()
            page.get_by_role('button', name='写推文', exact=True).click()
            expect(page.get_by_label('推文正文', exact=True)).to_have_value(manual)
            engine.fail = True
            page.get_by_label('继续讨论或提出修改意见').fill('加一点自嘲，不要编数据')
            page.get_by_role('button', name='按讨论修改候选稿', exact=True).click()
            expect(page.get_by_role('button', name='恢复本轮要求', exact=True)).to_be_visible(timeout=15000)
            expect(page.get_by_label('推文正文', exact=True)).to_have_value(manual)
            engine.fail = False
            page.get_by_role('button', name='恢复本轮要求', exact=True).click()
            expect(page.get_by_label('继续讨论或提出修改意见')).to_have_value('加一点自嘲，不要编数据')
            page.get_by_role('button', name='按讨论修改候选稿', exact=True).click()
            expect(page.get_by_label('推文正文', exact=True)).to_have_value('给提醒工具补上失败重试。代码记得重试，我却总忘记喝水。', timeout=15000)
            assert engine.requests[-1].context['writing_workspace']['current_candidate'] == manual
            assert len(engine.requests[-1].history) == 4
            page.set_viewport_size({'width': 390, 'height': 844})
            page.wait_for_timeout(350)  # Let the responsive navigation transition settle.
            page.evaluate('window.scrollTo(0, 0)')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path='/private/tmp/creation-mobile.png', full_page=True)
            page.set_viewport_size({'width': 1440, 'height': 1000})
            page.wait_for_timeout(350)
            page.evaluate('window.scrollTo(0, 0)')
            page.screenshot(path='/private/tmp/creation-desktop.png', full_page=True)
            page.get_by_role('button', name='确认定稿', exact=True).click()
            assert not app.state.store.rows('SELECT * FROM drafts')
            page.get_by_role('button', name='保存为最终稿', exact=True).click()
            page.get_by_role('button', name='到内容库查看定稿', exact=True).click()
            final = page.get_by_label('草稿正文', exact=True)
            expect(final).to_have_value('给提醒工具补上失败重试。代码记得重试，我却总忘记喝水。')
            assert len(app.state.store.rows('SELECT * FROM drafts')) == 1
            final.fill('定稿在内容库中修改后的正文')
            page.get_by_role('button', name='回看灵感与创作对话', exact=True).click()
            expect(page.locator('.creation-final-text')).to_have_text('定稿在内容库中修改后的正文')
            expect(page.locator('.creation-message.assistant')).to_have_count(3)
            expect(page.get_by_label('草稿正文', exact=True)).to_have_count(0)
            assert not errors, errors
            browser.close()
            print('PASS capture without AI, reload/resume, multiple turns, candidate editing, failure/retry, context continuity, explicit finalization, content-library handoff and current final preview; desktop/mobile; no page errors')
    finally:
        server.should_exit = True
        thread.join(timeout=5)
