"""Synthetic Content XHR check in local Chrome; no X account or credentials."""

import json
import os
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
URL = "https://x.com/i/account_analytics/content?type=posts&days=28"
VARIABLES = {"from_time": "2026-09-15T00:00:00Z",
             "to_time": "2026-10-13T00:00:00Z", "max_results": 1000}
REQUEST = "https://x.com/i/api/graphql/fixture/contentPageQuery?variables=" + quote(
    json.dumps(VARIABLES))


with sync_playwright() as playwright:
    chrome = next((path for path in (
        os.environ.get("STUDIO_CHROME"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        playwright.chromium.executable_path)
        if path and Path(path).is_file()), None)
    if chrome is None:
        raise RuntimeError("未找到 Playwright 或本机 Chrome")
    browser = playwright.chromium.launch(executable_path=chrome, headless=True)
    try:
        page = browser.new_page()
        page.add_init_script(path=str(ROOT / "browser-extension/analytics-capture.js"))
        page.route("https://x.com/i/api/graphql/**", lambda route: route.fulfill(
            json={"data": {"viewer_v2": {"user_results": {"result": {
                "tweets_results": []}}}},
                  "request_headers": {"authorization": "must-not-leak"}}))
        page.route("https://x.com/i/account_analytics/content**", lambda route: route.fulfill(
            content_type="text/html", body=(
                "<script>const xhr=new XMLHttpRequest();"
                f"xhr.open('GET',{json.dumps(REQUEST)});"
                "xhr.send();xhr.onloadend=()=>{window.__fixtureDone=true}</script>")))
        page.goto(URL)
        page.wait_for_function("window.__fixtureDone === true")
        captured = page.evaluate("window.__personaStudioTakeAnalytics()")
        assert captured["max_results"] == 1000
        assert captured["payload"]["data"]["viewer_v2"]["user_results"]["result"]["tweets_results"] == []
        assert "authorization" not in json.dumps(captured)
        print("PASS local Chrome Content XHR capture")
    finally:
        browser.close()
