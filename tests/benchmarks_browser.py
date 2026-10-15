"""Check that an open benchmark deconstruction survives app refreshes in local Chrome."""

import os
import re
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


with tempfile.TemporaryDirectory(prefix="benchmark-doc-ui-") as temporary:
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
            page.get_by_label("管理员密码", exact=True).fill("benchmark-doc-password")
            page.get_by_label("再次输入密码", exact=True).fill("benchmark-doc-password")
            page.get_by_role("button", name="创建工作室", exact=True).click()

            now = int(time.time())
            with app.state.store.db(True) as db:
                db.execute("INSERT INTO benchmark_accounts(handle,label,note,added_at) "
                           "VALUES('example','例子','',?)", (now,))
                db.execute("INSERT INTO benchmark_posts "
                           "(post_id,handle,author,text,text_source,url,posted_at,kind,"
                           "metrics_json,engagement_json,is_viral,captured_at,source,coverage_gap) "
                           "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           ("1234567890123456789", "example", "example", "一个具体观察",
                            "tweet_text", "https://x.com/example/status/1234567890123456789",
                            now - 86400, "original", '{"likes":630}', "{}", 1, now,
                            "search", ""))
                db.execute("INSERT INTO viral_deconstructions "
                           "(id,post_id,handle,thesis,hook,structure_json,evidence,design_doc,engine,created) "
                           "VALUES(?,?,?,?,?,?,?,?,?,?)",
                           ("example-doc", "1234567890123456789", "example", "观察", "开头",
                            "{}", "", "测试拆解：这段内容应该保持可见。", "test", now))
            assert "example" in [row["handle"] for row in app.state.store.rows(
                "SELECT handle FROM benchmark_accounts")]

            page.get_by_role("button", name="对标账号", exact=True).click()
            account = page.get_by_role("button", name=re.compile("@example"))
            expect(account).to_be_visible(timeout=20000)
            account.click()
            view = page.get_by_role("button", name="查看拆解", exact=True)
            expect(view).to_be_visible()
            view.click()
            doc = page.locator("pre.bench-doc")
            expect(doc).to_contain_text("测试拆解：这段内容应该保持可见。")
            # The action above calls the app-wide refresh. The old page would
            # reload the account and clear openDoc immediately afterward.
            page.wait_for_timeout(2500)
            expect(doc).to_contain_text("测试拆解：这段内容应该保持可见。")
            assert not errors, errors
            browser.close()
            print("PASS local Chrome benchmark deconstruction remains visible")
    finally:
        server.should_exit = True
        thread.join(timeout=5)
