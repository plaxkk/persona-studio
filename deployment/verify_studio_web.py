"""Browser acceptance in disposable state; all generated content is explicitly simulated."""

import asyncio, contextlib, json, os, socket, sys, tempfile, threading, time, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from studio.api import create_app
from studio.engines import EngineResult
from playwright.sync_api import sync_playwright, expect
import uvicorn

os.umask(0o077)


class FakeEngine:
    async def execute(self, request, config, task_id):
        return EngineResult(
            "【模拟验收】中文回复\n换行、emoji 🌱 与 & ? # +。", "hermes"
        )


async def loop(app):
    while True:
        app.state.store.set("worker_heartbeat", int(time.time()))
        task = app.state.jobs.claim()
        if task:
            await app.state.jobs.run(task)
        await asyncio.sleep(0.1)


with tempfile.TemporaryDirectory(prefix="studio-browser-") as folder:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    base = f"http://127.0.0.1:{port}"
    app = create_app(Path(folder), base)
    store = app.state.store
    with store.db() as c:
        c.execute(
            "UPDATE engines SET status='ready',config=?",
            (
                json.dumps(
                    {"model": "simulated-model", "base_url": "http://127.0.0.1/mock"}
                ),
            ),
        )
    app.state.jobs.engines.adapters = {
        name: FakeEngine() for name in ["hermes", "openclaw"]
    }

    @contextlib.asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(loop(app))
        yield
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    app.router.lifespan_context = lifespan
    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="critical", access_log=False
        )
    )
    threading.Thread(target=server.run, daemon=True).start()
    for i in range(100):
        if server.started:
            break
        time.sleep(0.1)
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=os.environ.get(
                "STUDIO_CHROME",
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            ),
            headless=True,
            args=["--disable-gpu"],
        )
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base)
        page.get_by_label("管理员密码", exact=True).fill("browser-test-password")
        page.get_by_label("再次输入密码", exact=True).fill("browser-test-password")
        page.get_by_role("button", name="创建工作室", exact=True).click()
        page.get_by_role("heading", name="让表达，有自己的样子。").wait_for()
        page.screenshot(path=str(ROOT / ".local/studio-home-test.png"), full_page=True)

        def nav(name):
            if page.viewport_size["width"] < 800:
                page.get_by_role("button", name="打开导航", exact=True).click()
            page.locator("nav").get_by_role("button", name=name, exact=True).click()

        nav("我的人格")
        page.get_by_label("角色名字", exact=True).fill("模拟验收角色")
        page.get_by_role("button", name="保存人格", exact=True).click()
        page.get_by_label("试聊消息").fill("你好，这只是模拟验收")
        page.get_by_role("button", name="发送试聊", exact=True).click()
        page.get_by_text("【模拟验收】中文回复", exact=False).wait_for(timeout=15000)
        nav("工作室")
        page.get_by_role("button", name="开始运行", exact=True).click()
        page.get_by_role("button", name="暂停运行", exact=True).wait_for()
        nav("写推文")
        text = "【模拟验收】中文\n换行 🥰 & ? # + / ="
        page.get_by_label("灵感、观察或工作片段").fill(text)
        page.get_by_role("button", name="保存并开始创作", exact=True).click()
        page.get_by_label("推文正文", exact=True).fill(text)
        page.get_by_role("button", name="确认定稿", exact=True).click()
        page.get_by_role("button", name="保存为最终稿", exact=True).click()
        page.get_by_role("button", name="到内容库查看定稿", exact=True).click()
        editor = page.get_by_label("草稿正文", exact=True).first
        editor.wait_for()
        assert editor.input_value() == text
        editor.fill(text + "手工编辑")
        nav("工作室")
        nav("内容库")
        assert (
            page.get_by_label("草稿正文", exact=True).first.input_value()
            == text + "手工编辑"
        )
        page.evaluate(
            "window.originalOpen=window.open;navigator.clipboard.writeText=async()=>{throw new Error('denied')};window.open=()=>null"
        )
        page.get_by_role("button", name="复制并去 X 发布", exact=True).first.click()
        page.get_by_label("手动复制文案", exact=True).wait_for()
        assert (
            page.get_by_label("手动复制文案", exact=True).input_value()
            == text + "手工编辑"
        )
        assert (
            page.get_by_role("link", name="打开 X 发布页").get_attribute("href")
            == "https://x.com/compose/post"
        )
        assert store.rows("SELECT status FROM drafts")[0]["status"] == "draft"
        page.evaluate(
            "navigator.clipboard.writeText=async text=>{window.copiedText=text}"
        )
        page.get_by_role("button", name="复制并去 X 发布", exact=True).first.click()
        assert page.evaluate("window.copiedText") == text + "手工编辑"

        page.get_by_role("button", name="我已在 X 完成", exact=True).click()
        page.get_by_label("结果链接").fill(
            "https://x.com/test/status/9999999999999999999"
        )
        page.get_by_role("button", name="确认完成", exact=True).click()
        nav("内容库")
        page.get_by_role("button", name="用户确认完成", exact=True).click()
        page.get_by_text("用户确认完成 · 未经平台验证", exact=False).wait_for()
        # Safe reply association with a large string ID; never contact X.
        now = int(time.time())
        with store.db() as c:
            c.execute(
                "INSERT INTO posts VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    "9999999999999999999",
                    "fixture",
                    "【模拟验收】原帖上下文",
                    "https://x.com/fixture/status/9999999999999999999",
                    "reply",
                    "{}",
                    "new",
                    "",
                    now,
                    now,
                ),
            )
        store.draft("【模拟验收】回复文本", "reply", "9999999999999999999")
        nav("互动收件箱")
        page.get_by_role("button", name="复制并去回复", exact=True).wait_for(
            timeout=10000
        )
        page.get_by_role("button", name="复制并去回复", exact=True).click()
        page.get_by_role("link", name="打开原帖").wait_for()
        assert (
            page.get_by_role("link", name="打开原帖")
            .get_attribute("href")
            .endswith("/fixture/status/9999999999999999999")
        )
        context.route(
            "https://x.com/**",
            lambda route: route.fulfill(
                status=200, body="<h1>模拟 X 登录页</h1>", content_type="text/html; charset=utf-8"
            ),
        )
        page.evaluate("() => {window.open=window.originalOpen}")
        with page.expect_popup() as popup_info:
            page.get_by_role("button", name="复制并去回复", exact=True).click()
        popup = popup_info.value
        try:
            popup.wait_for_url(
                "https://x.com/fixture/status/9999999999999999999", timeout=8000
            )
        except Exception:
            print(
                {
                    "popup_url": popup.url,
                    "toast": page.locator(".toast").all_text_contents(),
                    "errors": errors,
                    "copied": page.evaluate("window.copiedText"),
                },
                flush=True,
            )
            raise
        popup.get_by_role("heading", name="模拟 X 登录页").wait_for()
        popup.close()
        assert page.evaluate("window.copiedText") == "【模拟验收】回复文本"
        assert (
            store.rows("SELECT status FROM drafts WHERE kind='reply'")[0]["status"]
            == "draft"
        )
        page.get_by_role("button", name="换个写法", exact=True).click()
        expect(page.get_by_label("草稿正文",exact=True)).to_have_value(re.compile("中文回复"),timeout=15000)
        nav("设置")
        page.get_by_role("button", name="写作引擎", exact=True).click()
        page.get_by_role("button", name="切换到此引擎", exact=True).click()
        page.wait_for_timeout(500)
        assert store.get("engine") == "openclaw"
        page.screenshot(
            path=str(ROOT / ".local/studio-engines-test.png"), full_page=True
        )
        for size in [{"width": 390, "height": 844}]:
            page.set_viewport_size(size)
            for name in [
                "工作室",
                "我的人格",
                "互动收件箱",
                "写推文",
                "内容库",
                "设置",
            ]:
                nav(name)
                page.wait_for_timeout(150)
                assert page.evaluate(
                    "document.documentElement.scrollWidth<=innerWidth+1"
                ), name + " overflows"
            nav("我的人格")
            page.get_by_label("角色名字", exact=True).fill("手机验收角色")
            page.get_by_role("button", name="保存人格", exact=True).click()
            page.get_by_label("试聊消息").fill("手机试聊")
            page.get_by_role("button", name="发送试聊", exact=True).click()
            page.wait_for_timeout(1000)
            nav("写推文")
            page.get_by_role("button", name="返回灵感库", exact=True).click()
            page.get_by_label("灵感、观察或工作片段").fill("手机端手写草稿")
            page.get_by_role("button", name="保存并开始创作", exact=True).click()
            page.get_by_label("推文正文", exact=True).fill("手机端手写草稿")
            page.get_by_role("button", name="确认定稿", exact=True).click()
            page.get_by_role("button", name="保存为最终稿", exact=True).click()
            page.get_by_role("button", name="到内容库查看定稿", exact=True).click()
            page.get_by_label("草稿正文", exact=True).first.wait_for()
            nav("设置")
            page.get_by_role("button", name="写作引擎", exact=True).click()
            page.get_by_role("button", name="切换到此引擎", exact=True).click()
            page.wait_for_timeout(300)
            assert store.get("engine") == "hermes"
            nav("工作室")
            page.get_by_role("button", name="暂停运行", exact=True).click()
            page.get_by_role("button", name="开始运行", exact=True).wait_for()
            assert store.get("paused")
            page.screenshot(
                path=str(ROOT / ".local/studio-mobile-test.png"), full_page=True
            )
        assert not errors, errors
        # Production is observed only; no administrator is created by this test.
        live = context.new_page()
        live.goto("http://127.0.0.1:18880")
        live.get_by_role("heading", name="创建你的工作室", exact=True).wait_for()
        live.screenshot(path=str(ROOT / ".local/studio-welcome.png"), full_page=True)
        browser.close()
    server.should_exit = True
print(
    json.dumps(
        {
            "passed": True,
            "data": "disposable_simulation",
            "desktop_mobile": True,
            "clipboard_denied": True,
            "popup_blocked": True,
            "navigation_save": True,
            "reply_target": True,
            "completion_truth": True,
            "engine_switch": True,
            "production": "setup_page_only",
            "x_logged_out": "simulated_login_page",
            "clipboard_unicode_success": True,
            "regeneration_refresh": True,
        }
    )
)
