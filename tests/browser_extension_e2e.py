"""Opt-in isolated Chromium E2E. Fake X cookies/profile only; no real browser data."""

import asyncio
import json
import tempfile
import threading
import os
from pathlib import Path
import uvicorn
from playwright.async_api import async_playwright, expect
from studio.api import create_app
from studio.browser_identity import EXTENSION_ID
from studio.xreader import XReader, ReadError


async def main():
    async def fixture_verify(self, expected=""):
        if self.secrets.get("X_AUTH_TOKEN") == "fixture-expired":
            raise ReadError("needs_login")
        assert self.secrets.get("X_AUTH_TOKEN") in [
            "fixture-auth",
            "fixture-auth-renewed",
        ]
        assert self.secrets.get("X_CT0") == "fixture-csrf"
        return {
            "id": "1234567890123456789",
            "username": "fixture_owner",
            "name": "Fixture",
        }

    XReader.verify = fixture_verify
    with tempfile.TemporaryDirectory(prefix="persona-browser-e2e-") as root:
        app = create_app(Path(root) / "state", "http://127.0.0.1:18481")
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=18481,
                log_level="critical",
                access_log=False,
            )
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.05)
        async with async_playwright() as p:
            extension = str(Path("browser-extension").resolve())
            default_browser = Path(p.chromium.executable_path)
            installed = sorted(
                (Path.home() / "Library/Caches/ms-playwright").glob(
                    "chromium-*/chrome-mac-x64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
                )
            )
            executable = os.environ.get("STUDIO_TEST_CHROMIUM") or str(
                default_browser
                if default_browser.exists()
                else installed[-1]
                if installed
                else Path(
                    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
                )
            )
            context = await p.chromium.launch_persistent_context(
                str(Path(root) / "profile"),
                headless=True,
                executable_path=executable,
                args=[
                    f"--disable-extensions-except={extension}",
                    f"--load-extension={extension}",
                ],
            )
            await context.route(
                "https://x.com/**",
                lambda route: route.fulfill(
                    status=200,
                    content_type="text/html",
                    body="<h1>Fixture X login — simulated</h1>",
                ),
            )
            try:
                worker = (
                    context.service_workers[0]
                    if context.service_workers
                    else await context.wait_for_event("serviceworker")
                )
                assert EXTENSION_ID in worker.url
                r = await context.request.post(
                    "http://127.0.0.1:18481/api/v1/auth/setup",
                    data={"password": "fixture-browser-password"},
                )
                assert r.status == 200
                popup = await context.new_page()
                await popup.goto(f"chrome-extension://{EXTENSION_ID}/popup.html")
                await popup.locator("#origin").fill("http://127.0.0.1:18481")
                async with context.expect_page() as opened:
                    await popup.locator("#allow").click()
                panel = await opened.value
                await panel.wait_for_load_state()
                # No X cookies: extension should open an ordinary login page.
                for _ in range(80):
                    if any("x.com/i/flow/login" in page.url for page in context.pages):
                        break
                    await asyncio.sleep(0.1)
                assert any(
                    "x.com/i/flow/login" in page.url for page in context.pages
                ), "login guide did not open"
                assert app.state.store.get("x_status") == "not_connected"
                # Simulate completion of login in this throwaway profile.
                await context.add_cookies(
                    [
                        {
                            "name": "auth_token",
                            "value": "fixture-auth",
                            "domain": ".x.com",
                            "path": "/",
                            "secure": True,
                            "httpOnly": True,
                        },
                        {
                            "name": "ct0",
                            "value": "fixture-csrf",
                            "domain": ".x.com",
                            "path": "/",
                            "secure": True,
                        },
                    ]
                )
                for _ in range(100):
                    if app.state.store.get("x_status") == "ready":
                        break
                    await asyncio.sleep(0.1)
                assert app.state.store.get("x_status") == "ready", json.dumps(
                    app.state.store.rows("SELECT status,message FROM browser_pairs")
                )
                await panel.get_by_role("button", name="设置", exact=True).click()
                await panel.get_by_text(
                    "已从浏览器连接 @fixture_owner，无需填写 cookie。"
                ).wait_for()
                await expect(panel.get_by_label("X 用户名", exact=True)).to_have_value(
                    "fixture_owner"
                )
                assert (
                    await panel.get_by_label("auth_token", exact=True).input_value()
                    == ""
                )
                assert await panel.get_by_label("ct0", exact=True).input_value() == ""
                await panel.screenshot(
                    path="/private/tmp/persona-browser-desktop.png", full_page=True
                )
                await panel.set_viewport_size({"width": 390, "height": 844})
                await panel.screenshot(
                    path="/private/tmp/persona-browser-mobile.png", full_page=True
                )
                # A reopened panel with valid cookies connects without opening X again.
                logins = sum("x.com/" in page.url for page in context.pages)
                await panel.reload()
                for _ in range(100):
                    rows = app.state.store.rows(
                        "SELECT * FROM browser_pairs WHERE status='connected'"
                    )
                    if len(rows) >= 2:
                        break
                    await asyncio.sleep(0.1)
                assert len(rows) >= 2
                assert sum("x.com/" in page.url for page in context.pages) == logins
                # Stale cookies: guide login and renew the one-use pairing after cookies change.
                await context.add_cookies(
                    [
                        {
                            "name": "auth_token",
                            "value": "fixture-expired",
                            "domain": ".x.com",
                            "path": "/",
                            "secure": True,
                            "httpOnly": True,
                        }
                    ]
                )
                await panel.reload()
                for _ in range(100):
                    if sum("x.com/" in page.url for page in context.pages) > logins:
                        break
                    await asyncio.sleep(0.1)
                assert sum("x.com/" in page.url for page in context.pages) > logins
                await context.add_cookies(
                    [
                        {
                            "name": "auth_token",
                            "value": "fixture-auth-renewed",
                            "domain": ".x.com",
                            "path": "/",
                            "secure": True,
                            "httpOnly": True,
                        },
                        {
                            "name": "ct0",
                            "value": "fixture-csrf",
                            "domain": ".x.com",
                            "path": "/",
                            "secure": True,
                        },
                    ]
                )
                for _ in range(150):
                    renewed = app.state.store.rows(
                        "SELECT * FROM browser_pairs WHERE status='connected'"
                    )
                    if len(renewed) >= 3:
                        break
                    await asyncio.sleep(0.1)
                assert len(renewed) >= 3, str(
                    app.state.store.rows("SELECT status,message FROM browser_pairs")
                )
                # Revocation disables automatic reads on the next panel open.
                revoke = await context.new_page()
                await revoke.goto(f"chrome-extension://{EXTENSION_ID}/popup.html")
                await revoke.locator("#revoke").click()
                before = len(app.state.store.rows("SELECT * FROM browser_pairs"))
                await panel.reload()
                await panel.wait_for_timeout(1800)
                assert (
                    len(app.state.store.rows("SELECT * FROM browser_pairs")) == before
                )
                print(
                    "PASS: isolated Chromium real extension: login guide, HttpOnly cookie import, identity autofill, blank secret fields, reopening, expired-login renewal, revocation, desktop/mobile screenshots. X identity is simulated."
                )
            finally:
                await context.close()
                server.should_exit = True
                thread.join(timeout=5)


if __name__ == "__main__":
    asyncio.run(main())
