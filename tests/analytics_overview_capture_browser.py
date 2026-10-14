"""Synthetic Overview XHR capture in local Chrome; no X account or credentials."""

import json
import os
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
PAGE = "https://x.com/i/account_analytics/overview"
VARIABLES = {
    "current_from_iso": "2026-09-16T00:00:00.000Z",
    "current_to_iso": "2026-10-14T00:00:00.000Z",
    "prev_from_iso": "2026-08-19T00:00:00.000Z",
    "prev_to_iso": "2026-09-16T00:00:00.000Z",
}
REQUEST = "https://x.com/i/api/graphql/fixture/accountOverviewDailyQuery?variables=" + quote(
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
                "current_time_series": [], "hourly_backfill": [],
                "previous_totals": [], "legacy_current_follow_metrics": [],
                "legacy_previous_follow_metrics": [],
            }}}}, "request_headers": {"authorization": "must-not-leak"}}))
        page.route(PAGE, lambda route: route.fulfill(
            content_type="text/html", body=(
                "<script>const xhr=new XMLHttpRequest();"
                f"xhr.open('GET',{json.dumps(REQUEST)});"
                "xhr.send();xhr.onloadend=()=>{window.__fixtureDone=true}</script>")))
        page.goto(PAGE)
        page.wait_for_function("window.__fixtureDone === true")
        captured = page.evaluate("window.__personaStudioTakeAnalytics()")
        assert captured["query_kind"] == "overview"
        assert captured["prev_to"] == VARIABLES["prev_to_iso"]
        assert captured["payload"]["data"]["viewer_v2"]["user_results"]["result"]["current_time_series"] == []
        assert "authorization" not in json.dumps(captured)
        print("PASS local Chrome Overview XHR capture")
    finally:
        browser.close()
