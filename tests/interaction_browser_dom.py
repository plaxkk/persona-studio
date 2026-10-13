"""Synthetic X detail page; uses local Chrome when Playwright Chromium is absent."""
import asyncio
import os
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
HTML = """<meta charset="utf-8"><nav><a data-testid="AppTabBar_Profile_Link" href="/owner">我</a></nav><main>
<article data-testid="tweet"><a href="/owner/status/100"><time datetime="2026-10-13T01:00:00Z"></time></a><div data-testid="tweetText">上文：我们讨论数据库迁移</div><div data-testid="tweetPhoto"><img alt="上文的流程图"></div></article>
<article data-testid="tweet"><a href="/visitor/status/101"><time datetime="2026-10-13T02:00:00Z"></time></a><div>Replying to @owner</div><div data-testid="tweetText">Codex 如何保证迁移可靠？</div>
<div data-testid="quoteTweet"><a href="/other/status/102"><time datetime="2026-10-13T00:00:00Z"></time></a><div data-testid="tweetText">引用里的原话</div><div data-testid="tweetPhoto"><img alt="引用里的截图"></div></div>
<div data-testid="tweetPhoto"><img alt="包含迁移版本与失败原因的截图"></div></article></main>"""


async def main():
    async with async_playwright() as p:
        paths = [os.environ.get("STUDIO_CHROME"), p.chromium.executable_path,
                 "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
        binary = next((path for path in paths if path and Path(path).is_file()), None)
        if not binary:
            raise RuntimeError("未找到 Playwright 或本机 Chrome；可设置 STUDIO_CHROME")
        browser = await p.chromium.launch(executable_path=binary, headless=True)
        page = await browser.new_page()
        await page.route("https://x.com/**", lambda route: route.fulfill(body=HTML, content_type="text/html"))
        await page.goto("https://x.com/visitor/status/101")
        code = (ROOT / "browser-extension/persona-page.js").read_text().replace("export async function", "async function")
        await page.add_script_tag(content=code + ";window.readPersonaPage=readPersonaPage;")
        detail = await page.evaluate('async () => readPersonaPage({kind:"sync_detail",author:"visitor",post_id:"101"},"owner")')
        assert detail["state"] == "ready", detail
        assert detail["record"]["text"] == "Codex 如何保证迁移可靠？"
        assert detail["record"]["parent"]["text"] == "上文：我们讨论数据库迁移"
        assert detail["record"]["quote"]["text"] == "引用里的原话"
        assert detail["record"]["media_pending"] is True
        assert [item["description"] for item in detail["record"]["media"]] == [
            "引用里的截图", "包含迁移版本与失败原因的截图", "上文的流程图"]
        bad = await page.evaluate('async () => readPersonaPage({kind:"sync_detail",author:"visitor",post_id:"999"},"owner")')
        assert bad["state"] == "source_error"
        await browser.close()
    print("PASS synthetic X detail: text, parent, quote, media and URL validation")


asyncio.run(main())
