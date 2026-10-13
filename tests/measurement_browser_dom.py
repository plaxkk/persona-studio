"""Synthetic X measurement pages; falls back to local Chrome when bundled Chromium is absent."""
import asyncio
import os
from pathlib import Path

from playwright.async_api import async_playwright


ROOT = Path(__file__).resolve().parents[1]
DETAIL = '''<meta charset="utf-8"><nav><a data-testid="AppTabBar_Profile_Link" href="/owner">Me</a></nav><main>
<article data-testid="tweet"><a href="/owner/status/123"><time datetime="2026-10-10T01:00:00Z"></time></a>
<div data-testid="tweetText">这是我的实际发布内容</div><div data-testid="quoteTweet">被引用的内容</div>
<div role="group" aria-label="3 Replies, 2 Reposts, 12 Likes, 230 Views"></div></article></main>'''
PROFILE = '''<meta charset="utf-8"><nav><a data-testid="AppTabBar_Profile_Link" href="/owner">Me</a></nav><main>
<div data-testid="UserDescription">写产品和代码</div><a href="/owner/followers" aria-label="987 Followers">987 Followers</a>
<article data-testid="tweet"><div data-testid="socialContext">Pinned</div><a href="/owner/status/123"><time datetime="2026-10-10T01:00:00Z"></time></a></article></main>'''


async def main():
    async with async_playwright() as playwright:
        paths = [os.environ.get('STUDIO_CHROME'), playwright.chromium.executable_path,
                 '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome']
        binary = next((path for path in paths if path and Path(path).is_file()), None)
        if not binary:
            raise RuntimeError('未找到 Playwright 或本机 Chrome；可设置 STUDIO_CHROME')
        browser = await playwright.chromium.launch(executable_path=binary, headless=True)
        page = await browser.new_page()
        await page.route('https://x.com/**', lambda route: route.fulfill(
            body=DETAIL if '/status/' in route.request.url else PROFILE, content_type='text/html'))
        code = (ROOT / 'browser-extension/persona-page.js').read_text().replace('export async function', 'async function')
        await page.goto('https://x.com/owner/status/123')
        await page.add_script_tag(content=code + ';window.readPersonaPage=readPersonaPage;')
        metric = await page.evaluate('async () => readPersonaPage({kind:"metric_snapshot",author:"owner",post_id:"123"},"owner")')
        assert metric['state'] == 'ready', metric
        assert metric['record']['views'] == 230, metric
        assert metric['record']['likes'] == 12, metric
        assert metric['record']['replies'] == 3, metric
        assert metric['record']['kind'] == 'quote', metric
        await page.goto('https://x.com/owner')
        await page.add_script_tag(content=code + ';window.readPersonaPage=readPersonaPage;')
        profile = await page.evaluate('async () => readPersonaPage({kind:"profile_snapshot"},"owner")')
        assert profile['record']['followers'] == 987, profile
        assert profile['record']['bio'] == '写产品和代码', profile
        assert profile['record']['pinned_tweet_id'] == '123', profile
        await browser.close()
    print('PASS synthetic X metrics/profile: URL, exact counts, published type, bio, pin')


asyncio.run(main())
