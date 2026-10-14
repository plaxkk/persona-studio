"""Isolated historical Analytics UI check in local Chrome."""

import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from studio.api import create_app


with tempfile.TemporaryDirectory(prefix="analytics-history-ui-") as temporary:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    app = create_app(Path(temporary), base)
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="critical", access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    try:
        with sync_playwright() as playwright:
            binary = next((path for path in [os.environ.get("STUDIO_CHROME"),
                          "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                          playwright.chromium.executable_path]
                           if path and Path(path).is_file()), None)
            if not binary:
                raise RuntimeError("未找到本机 Chrome 或 Playwright Chromium")
            browser = playwright.chromium.launch(executable_path=binary, headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base)
            page.get_by_label("管理员密码", exact=True).fill("analytics-ui-password")
            page.get_by_label("再次输入密码", exact=True).fill("analytics-ui-password")
            page.get_by_role("button", name="创建工作室", exact=True).click()
            app.state.store.set("paused", False)
            app.state.store.set("x_username", "kkplayit")
            with app.state.store.db(True) as db:
                db.execute("INSERT INTO persona_browser_presence VALUES(1,'1.3.12',?)",
                           (int(time.time()),))
            page.reload()
            expect(page.get_by_role("heading", name="Subscription 帖子数据")).to_be_visible()
            page.get_by_label("历史起始日期").fill("2026-06-01")
            page.get_by_label("历史结束日期").fill("2026-07-15")
            page.get_by_role("button", name="分批补采所选范围").click()
            expect(page.get_by_text("已安排历史分批补采")).to_be_visible()
            rows = app.state.store.rows("SELECT id FROM tasks WHERE kind='analytics_content' "
                                        "AND payload LIKE '%from_date%'")
            assert len(rows) == 2
            page.screenshot(path="/private/tmp/persona-analytics-history-desktop.png",
                            full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(300)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path="/private/tmp/persona-analytics-history-mobile.png",
                            full_page=True)
            assert not errors, errors
            browser.close()
            print("PASS local Chrome historical Analytics queue desktop/mobile")
    finally:
        server.should_exit = True
        thread.join(timeout=5)
