"""Isolated metrics UI acceptance in local Chrome; no real X data or production state."""
import os
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


with tempfile.TemporaryDirectory(prefix='metrics-ui-') as temporary:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    app = create_app(Path(temporary), base)
    store = app.state.store
    store.set('x_username', 'owner')
    draft_id = store.draft('一条模拟原创草稿')
    now = int(time.time())
    with store.db(True) as db:
        db.execute("UPDATE drafts SET status='confirmed',result_url=? WHERE id=?",
                   ('https://x.com/owner/status/123',draft_id))
        db.execute("INSERT INTO draft_publications(draft_id,tweet_id,url,author,user_confirmed,user_confirmed_at,platform_observed_at,published_at,observation_source,publication_kind) VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (draft_id,'123','https://x.com/owner/status/123','owner',1,now-100,now-10,now-3*86400,'browser','post'))
        db.execute("INSERT INTO post_metric_snapshots(tweet_id,observed_at,published_at,age_seconds,target_window,window_status,source,views,likes,replies,reposts) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   ('123',now-10,now-3*86400,3*86400-10,'24h','late','browser',230,12,None,3))
        db.execute("INSERT INTO profile_snapshots(account,observed_at,day,followers,bio,pinned_tweet_id,source) VALUES(?,?,?,?,?,?,?)",
                   ('owner',now,time.strftime('%Y-%m-%d',time.localtime(now)),987,'写产品和代码','123','browser'))
    server = uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='critical',access_log=False))
    thread = threading.Thread(target=server.run,daemon=True)
    thread.start()
    for _ in range(100):
        if server.started: break
        time.sleep(.1)
    try:
        with sync_playwright() as playwright:
            binary = next((path for path in [os.environ.get('STUDIO_CHROME'), playwright.chromium.executable_path,
                          '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'] if path and Path(path).is_file()), None)
            if not binary: raise RuntimeError('未找到 Playwright 或本机 Chrome')
            browser = playwright.chromium.launch(executable_path=binary,headless=True)
            page = browser.new_page(viewport={'width':1440,'height':1000})
            errors = []
            page.on('pageerror',lambda error: errors.append(str(error)))
            page.goto(base)
            page.get_by_label('管理员密码',exact=True).fill('metrics-ui-password')
            page.get_by_label('再次输入密码',exact=True).fill('metrics-ui-password')
            page.get_by_role('button',name='创建工作室',exact=True).click()
            expect(page.get_by_role('heading',name='涨粉目标')).to_be_visible()
            expect(page.get_by_text('987 位粉丝')).to_be_visible()
            page.get_by_label('目标粉丝数').fill('1000')
            page.get_by_role('button',name='保存目标').click()
            expect(page.get_by_text('目标剩余：13')).to_be_visible()
            page.get_by_role('button',name='内容库',exact=True).click()
            page.get_by_role('button',name='用户确认完成',exact=True).click()
            expect(page.get_by_text('平台已读取到帖子')).to_be_visible()
            expect(page.get_by_text('24h（补读）',exact=False)).to_be_visible()
            expect(page.get_by_text('回复 暂无数据',exact=False)).to_be_visible()
            page.screenshot(path='/private/tmp/persona-metrics-desktop.png',full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            page.wait_for_timeout(300)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path='/private/tmp/persona-metrics-mobile.png',full_page=True)
            assert not errors, errors
            browser.close()
            print('PASS metrics UI: goal, verified state, late snapshot, missing metric, desktop/mobile')
    finally:
        server.should_exit = True
        thread.join(timeout=5)
