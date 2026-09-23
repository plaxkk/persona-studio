"""Import chain through real extension, synthetic X pages and simulated Codex only."""

import asyncio
import mimetypes
import json
from studio.security import Secrets
import shutil
from deployment.install_browser_connector import install
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
    def __init__(self, ident):
        self.id = ident

    async def execute(self, request, config, task_id):
        return EngineResult("模拟本机 Agent 回复：已收到你的想法。", self.id)


async def main():
    with tempfile.TemporaryDirectory(prefix="desktop-e2e-") as folder:
        app = create_app(Path(folder) / "state", "http://127.0.0.1:18482")
        with app.state.store.db() as c:
            c.execute("UPDATE engines SET status='ready'")
        for engine in ["hermes", "openclaw"]:
            app.state.jobs.engines.adapters[engine] = FixtureEngine(engine)
        app.state.store.set("worker_heartbeat", __import__("time").time())
        app.state.store.set("x_username", "example")
        from studio.codex_persona import CodexPersonaRunner
        from studio.persona_mcp import dispatch
        import studio.persona_routes as routes
        import studio.codex_persona as runner_module

        def fake_preflight():
            return {
                "ready": True,
                "installed": True,
                "model": "fixture",
                "reasoning": "medium",
                "message": "模拟模型，仅用于验收",
            }

        routes.preflight = runner_module.preflight = fake_preflight
        importer = CodexPersonaRunner(app.state.store)

        async def fake_execute(job, prompt, schema, browsing):
            if browsing:
                await asyncio.to_thread(
                    dispatch,
                    importer.imports,
                    job["id"],
                    job["generation"],
                    "browse",
                    {"action": "snapshot"},
                )
                dispatch(importer.imports, job["id"], job["generation"], "finish", {})
                return {"finished": True}
            ids = [r["id"] for r in importer.imports.sources(job["id"])]
            return {
                "persona": {
                    k: "模拟人格 " + k
                    for k in ("name", "identity", "voice", "interests", "boundaries")
                },
                "observations": [
                    {
                        "type": "observation",
                        "statement": "中文短句",
                        "source_ids": ids[:2],
                    }
                ],
                "limitations": "20 条模拟资料，不代表真实账号",
                "examples": ["新仿写一", "新仿写二", "新仿写三"],
            }

        importer.execute = fake_execute

        async def import_worker():
            while True:
                await importer.tick()
                await asyncio.sleep(0.1)

        importer_task = asyncio.create_task(import_worker())
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=18482,
                log_level="critical",
                access_log=False,
            )
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        while not server.started:
            await asyncio.sleep(0.05)

        async def worker():
            while True:
                job = app.state.jobs.claim()
                if job:
                    await app.state.jobs.run(job)
                await asyncio.sleep(0.1)

        worker_task = asyncio.create_task(worker())
        async with async_playwright() as p:
            default = Path(p.chromium.executable_path)
            installed = sorted(
                (Path.home() / "Library/Caches/ms-playwright").glob(
                    "chromium-*/chrome-mac-x64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
                )
            )
            executable = str(default if default.exists() else installed[-1])
            # Register a distinct test host, never production credentials/state.
            extdir = Path(folder) / "extension"
            shutil.copytree("browser-extension", extdir)
            bridgefile = extdir / "persona-browser.js"
            bridgefile.write_text(
                bridgefile.read_text().replace(
                    '} catch {\n          data = { state: "waiting_browser" };',
                    '} catch (e) {\n          globalThis.personaFixtureError=e.message; data = { state: "waiting_browser" };',
                )
            )
            popupfile = extdir / "popup.js"
            popupfile.write_text(
                popupfile.read_text().replace(
                    "ai.personastudio.connector", "ai.personastudio.connector_test"
                )
            )
            native_paths = install(
                Path(folder) / "state",
                18482,
                home=Path(folder) / "host-home",
                host_name="ai.personastudio.connector_test",
            )
            ext = str(extdir)
            # Chromium resolves user-level hosts relative to its custom user-data-dir.
            hostdir = Path(folder) / "chrome/NativeMessagingHosts"
            hostdir.mkdir(parents=True)
            shutil.copyfile(native_paths[0], hostdir / native_paths[0].name)
            context = await p.chromium.launch_persistent_context(
                str(Path(folder) / "chrome"),
                headless=True,
                executable_path=executable,
                args=[f"--disable-extensions-except={ext}", f"--load-extension={ext}"],
            )
            network = []
            context.on("request", lambda r: network.append((r.method, r.url)))

            async def website(route):
                path = urlparse(route.request.url).path.lstrip("/") or "index.html"
                file = (Path(".vercel-dist") / path).resolve()
                if (
                    not file.is_relative_to(Path(".vercel-dist").resolve())
                    or not file.is_file()
                ):
                    return await route.fulfill(status=404, body="Not found")
                await route.fulfill(
                    body=file.read_bytes(),
                    content_type=mimetypes.guess_type(str(file))[0]
                    or "application/octet-stream",
                )

            await context.route(CLOUD_ORIGIN + "/**", website)
            fixture = '<meta charset="utf-8"><style>[data-testid=tweetText]{white-space:pre-wrap}</style><nav><a data-testid="AppTabBar_Profile_Link" href="/example">本人</a></nav><main><div data-testid="UserName">模拟本人</div><div data-testid="UserDescription">模拟简介</div>'
            for i in range(20):
                fixture += f'<article data-testid="tweet"><a href="/example/status/{90071992547409930 + i}"><time datetime="2026-09-23"></time></a>{"<div>Replying to @other</div>" if i % 2 else ""}<div data-testid="tweetText">第 {i} 条中文😀\n第二行</div></article>'
            fixture += "</main>"
            await context.route(
                "https://x.com/**",
                lambda r: r.fulfill(
                    body=fixture, content_type="text/html; charset=utf-8"
                ),
            )
            try:
                await context.request.post(
                    "http://127.0.0.1:18482/api/v1/auth/setup",
                    data={"password": "fixture-desktop-password"},
                )
                page = await context.new_page()
                await page.goto(CLOUD_ORIGIN)
                await expect(
                    page.get_by_role("heading", name="把你的电脑接入人格工作室")
                ).to_be_visible()
                popup = await context.new_page()
                await popup.goto(f"chrome-extension://{EXTENSION_ID}/popup.html")
                await popup.locator("#origin").fill("http://127.0.0.1:18482")
                assert await popup.locator("input[type=password]").count() == 0
                probe = await popup.evaluate(
                    "async()=>{try{const r=await chrome.runtime.sendNativeMessage('ai.personastudio.connector_test',{type:'authorize',local:'http://127.0.0.1:18482'});return {ok:r.ok,message:r.message}}catch(e){return {error:e.message}}}"
                )
                assert probe.get("ok"), probe
                async with context.expect_page() as opened:
                    await popup.locator("#desktop-allow").click()
                web = await opened.value
                await web.goto(CLOUD_ORIGIN)
                await expect(
                    web.get_by_text("已连接这台电脑 · Agent 与数据在本机运行")
                ).to_be_visible(timeout=15000)
                await web.get_by_role("button", name="我的人格", exact=True).click()
                await web.get_by_label("采集目标").select_option("20")
                await web.get_by_role("checkbox").check()
                for _ in range(60):
                    if importer.imports.connected():
                        break
                    await asyncio.sleep(0.25)
                assert importer.imports.connected(), "extension browser bridge offline"
                await web.get_by_role("button", name="重新检查", exact=True).click()
                await expect(
                    web.get_by_role("button", name="开始读取我的 X")
                ).to_be_enabled(timeout=15000)
                await web.get_by_role("button", name="开始读取我的 X").click()
                await expect(
                    web.get_by_role("heading", name="人设预览 · 尚未自动应用")
                ).to_be_visible(timeout=30000)
                assert app.state.store.get("persona")["version"] == 1
                imp = importer.imports.get(
                    app.state.store.rows("SELECT id FROM persona_imports")[0]["id"]
                )
                assert imp["counts"] == {
                    "read": 20,
                    "usable": 20,
                    "post": 10,
                    "reply": 10,
                }, imp
                await web.reload()
                await web.get_by_role("button", name="我的人格", exact=True).click()
                await expect(
                    web.get_by_role("heading", name="人设预览 · 尚未自动应用")
                ).to_be_visible()
                await web.set_viewport_size({"width": 390, "height": 844})
                await web.screenshot(
                    path="/private/tmp/persona-import-mobile.png", full_page=True
                )
                await web.set_viewport_size({"width": 1440, "height": 1000})
                await web.screenshot(
                    path="/private/tmp/persona-import-desktop.png", full_page=True
                )
                await web.get_by_role("button", name="应用人设", exact=True).click()
                await expect(
                    web.get_by_role("heading", name="已应用", exact=True)
                ).to_be_visible()
                assert app.state.store.get("persona")["version"] == 2
                assert (
                    len(
                        app.state.store.rows(
                            "SELECT id FROM memories WHERE category='style'"
                        )
                    )
                    == 20
                )
                assert not any(
                    method != "GET" and url.startswith("https://x.com")
                    for method, url in network
                )
                print(
                    "PASS extension → local queue → synthetic X evidence → simulated model → persisted preview → explicit apply; 20 records (10+10)"
                )
                importer_task.cancel()
            except Exception:
                for pg in context.pages:
                    if pg.url.startswith("https://x.com"):
                        print(
                            "fixture X URL",
                            pg.url,
                            "DOM",
                            (await pg.locator("body").inner_text())[:500],
                        )
                for sw in context.service_workers:
                    print(
                        "fixture extension error",
                        await sw.evaluate('globalThis.personaFixtureError || "none"'),
                    )
                raise
            finally:
                await context.close()
                worker_task.cancel()
                server.should_exit = True
                thread.join(timeout=5)
                for path in native_paths:
                    path.unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(main())
