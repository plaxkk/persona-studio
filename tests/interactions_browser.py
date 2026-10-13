"""Isolated inbox acceptance with local Chrome and synthetic posts only."""
import json
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


with tempfile.TemporaryDirectory(prefix="inbox-ui-") as temporary:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    app = create_app(Path(temporary), base)
    store = app.state.store
    store.set("x_status", "ready")
    now = int(time.time())
    with store.db(True) as db:
        for ident in range(9000, 9230):
            text = "旅行 签证 的真实经历请教" if ident == 9200 else "Codex 代码讨论：如何做上下文读取？"
            context = {"reader": "browser", "source": "timeline", "published_at": now,
                       "detail_checked": ident != 9229, "parent_needed": False,
                       "quote_needed": False, "media_pending": False}
            db.execute("INSERT INTO posts VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (str(ident), f"author{ident}", text, f"https://x.com/author{ident}/status/{ident}",
                        "timeline", json.dumps(context, ensure_ascii=False), "new", "", now, now))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="critical", access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started: break
        time.sleep(.1)
    try:
        with sync_playwright() as p:
            binary = next((path for path in [os.environ.get("STUDIO_CHROME"), p.chromium.executable_path,
                          "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"] if path and Path(path).is_file()), None)
            if not binary: raise RuntimeError("未找到 Playwright 或本机 Chrome")
            browser = p.chromium.launch(executable_path=binary, headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base)
            page.get_by_label("管理员密码", exact=True).fill("inbox-ui-password")
            page.get_by_label("再次输入密码", exact=True).fill("inbox-ui-password")
            page.get_by_role("button", name="创建工作室", exact=True).click()
            page.get_by_role("button", name="互动收件箱", exact=True).click()
            expect(page.get_by_role("heading", name="今日互动清单")).to_be_visible(timeout=20000)
            expect(page.locator(".post-card")).to_have_count(25)
            expect(page.get_by_text("当前筛选 30 条", exact=False)).to_be_visible()
            page.get_by_role("tab", name="待补上下文").click()
            expect(page.locator(".inbox-context.pending")).to_have_count(1)
            page.get_by_role("tab", name="全部记录").click()
            expect(page.get_by_text("当前筛选 230 条", exact=False)).to_be_visible()
            page.get_by_role("button", name="下一页").click()
            expect(page.get_by_text("2 / 10")).to_be_visible()
            page.get_by_label("搜索互动").fill("签证")
            expect(page.locator(".post-card")).to_have_count(1)
            page.get_by_role("tab", name="旅行需求", exact=False).click()
            expect(page.get_by_text("当前筛选 1 条", exact=False)).to_be_visible()
            page.get_by_label("搜索互动").fill("")
            page.get_by_role("tab", name="全部主题").click()
            page.get_by_role("tab", name="待补上下文").click()
            expect(page.locator(".inbox-context.pending")).to_have_count(1)
            expect(page.locator(".post-card")).to_have_count(1)
            expect(page.get_by_role("button", name="准备回复")).to_be_disabled()
            page.locator(".inbox-review summary").click()
            page.get_by_label("人工核对的上下文").fill("已在 X 查看原帖，提问者想知道多轮对话时上下文丢失后该如何恢复。")
            page.get_by_role("button", name="保存审核记录").click()
            expect(page.locator(".post-card")).to_have_count(0)
            page.get_by_role("tab", name="全部记录").click()
            page.get_by_label("搜索互动").fill("author9229")
            expect(page.locator(".inbox-context.ready")).to_have_count(1)
            page.screenshot(path="/private/tmp/persona-inbox-desktop.png", full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(350)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path="/private/tmp/persona-inbox-mobile.png", full_page=True)
            assert not errors, errors
            browser.close()
            print("PASS inbox: 230 records, pagination, search, topic filter, context gate/manual review, desktop/mobile")
    finally:
        server.should_exit = True
        thread.join(timeout=5)
