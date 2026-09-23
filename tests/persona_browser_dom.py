"""Synthetic X fixtures only: never opens a signed-in user profile."""

import asyncio, json
from pathlib import Path
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
HTML = """<meta charset="utf-8"><style>[data-testid=tweetText]{white-space:pre-wrap}</style><nav><a data-testid="AppTabBar_Profile_Link" href="/example">本人</a></nav><main><div data-testid="UserName">示例</div><div data-testid="UserDescription">简介</div>
<article data-testid="tweet"><a href="/example/status/90071992547409933"><time datetime="2026-09-23"></time></a><div>Replying to @other</div><div data-testid="tweetText">中文😀\n第二行</div><div data-testid="quoteTweet"><div data-testid="tweetText">他人引用</div></div></article>
<article data-testid="tweet"><a href="/other/status/90071992547409934"><time></time></a><div data-testid="tweetText">不要计入本人</div></article>
<article data-testid="tweet"><a href="/example/status/90071992547409935"><time></time></a><div data-testid="tweetText">截断正文</div><button data-testid="tweet-text-show-more-link" onclick="this.previousElementSibling.innerText='完整正文\\n第二行';this.remove()">Show more</button></article>
<article data-testid="tweet"><a href="/example/status/90071992547409936"><time></time></a><div data-testid="quoteTweet"><div data-testid="tweetText">无附言引用，不计入本人</div></div></article>
<article data-testid="tweet"><a href="/example/status/90071992547409937"><time></time></a><div data-testid="tweetText">翻译</div><button>Show original</button></article>
</main>"""


async def main():
    async with async_playwright() as p:
        choices = list(
            Path.home().glob(
                "Library/Caches/ms-playwright/chromium-*/chrome-mac-x64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
            )
        )
        binary = (
            str(choices[-1])
            if choices
            else "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        )
        browser = await p.chromium.launch(executable_path=binary, headless=True)
        page = await browser.new_page()
        await page.route(
            "https://x.com/**", lambda r: r.fulfill(body=HTML, content_type="text/html")
        )
        await page.goto("https://x.com/example")
        code = (
            (ROOT / "browser-extension/persona-page.js")
            .read_text()
            .replace("export async function", "async function")
        )
        await page.add_script_tag(
            content=code + ";window.readPersonaPage=readPersonaPage;"
        )

        async def act(kind, target=""):
            return await page.evaluate(
                'async ([kind,target])=>readPersonaPage({kind,target},"example")',
                [kind, target],
            )

        result = await act("snapshot")
        assert len(result["records"]) == 4
        a = result["records"][0]
        assert (
            a["id"] == "90071992547409933"
            and a["kind"] == "reply"
            and a["text"] == "中文😀\n第二行"
            and a["context"] == "他人引用"
        )
        assert result["records"][2]["text"] == ""
        assert result["records"][3]["translated"]
        target = next(t["id"] for t in result["targets"] if t["kind"] == "expand")
        await act("identity")
        expanded = await act("expand", target)
        assert expanded["records"][1]["text"] == "完整正文\n第二行"
        assert not expanded["records"][1]["truncated"]
        stale = await act("expand", target)
        assert "失效" in stale["snapshot"]
        await page.evaluate('document.querySelector("nav a").href="/other"')
        assert (await act("scroll"))["state"] == "account_mismatch"
        await page.evaluate(
            'document.querySelector("nav").remove();document.querySelector("main").innerHTML=\'<a href="/login">Login</a>\''
        )
        assert (await act("snapshot"))["state"] == "waiting_login"
        await browser.close()
    print(
        "PASS synthetic DOM: attribution, multiline/emoji, big ID, expansion, stale targets, translation, login, identity mismatch"
    )


asyncio.run(main())
