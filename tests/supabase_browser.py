"""Isolated Supabase settings UI acceptance in local Chrome."""

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


with tempfile.TemporaryDirectory(prefix="supabase-ui-") as temporary:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    app = create_app(Path(temporary), base)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="critical", access_log=False))
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
            page.get_by_label("管理员密码", exact=True).fill("supabase-ui-password")
            page.get_by_label("再次输入密码", exact=True).fill("supabase-ui-password")
            page.get_by_role("button", name="创建工作室", exact=True).click()
            page.get_by_role("button", name="设置", exact=True).click()
            expect(page.get_by_role("heading", name="Supabase 分析数据")).to_be_visible()
            expect(page.get_by_text("待补传 0 次读取")).to_be_visible()
            page.get_by_label("Supabase 项目地址").fill("https://example.supabase.co")
            page.get_by_label("Secret key").fill("sb_secret_test_key_only_in_isolated_browser")
            page.get_by_role("button", name="保存 Supabase 连接").click()
            expect(page.get_by_role("button", name="移除连接")).to_be_visible()
            assert page.get_by_label("Secret key").input_value() == ""
            page.screenshot(path="/private/tmp/persona-supabase-settings-desktop.png", full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(300)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path="/private/tmp/persona-supabase-settings-mobile.png", full_page=True)
            assert not errors, errors
            browser.close()
            print("PASS Supabase local settings: save, key cleared, desktop/mobile")
    finally:
        server.should_exit = True
        thread.join(timeout=5)
