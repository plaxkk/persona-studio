"""爆款拆解 (06B-2): the measured half is computed, the rest is quoted or refused.

§4.9.2 asks for the general logic of social-account-doctor. The part of that
project worth copying is not its 小红书 vocabulary but its evidence rule: every
claim carries a source. These tests pin where that rule can actually be enforced
on X -- a rate that needs two published numbers, a comparison that needs peers
of the same age, and a model quote that has to appear in the stored text -- and
pin what happens when it cannot: the document says what it does not know instead
of filling the space.
"""
import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from studio import benchmarks, deconstruct
from studio.engines import EngineError, EngineResult, prompt_for
from studio.jobs import Jobs, JobError
from studio.store import Store

HANDLE = "lifesinger"
CREATED = "Wed Apr 15 03:00:37 +0000 2026"
CREATED_EPOCH = 1776222037
DAY = 86400


def tweet(post_id, text, *, likes=600, replies=15, retweets=162, quotes=24,
          bookmarks=750, views=108597, handle=HANDLE, created=CREATED,
          note=None):
    legacy = {
        "created_at": created, "full_text": text, "favorite_count": likes,
        "reply_count": replies, "retweet_count": retweets, "quote_count": quotes,
        "bookmark_count": bookmarks,
    }
    result = {
        "__typename": "Tweet", "rest_id": post_id, "legacy": legacy,
        "core": {"user_results": {"result": {"core": {
            "screen_name": handle, "name": "Frank Wang 玉伯"}}}},
        "views": ({"state": "EnabledWithCount", "count": str(views)}
                  if views is not None else {"state": "Enabled"}),
    }
    if note is not None:
        result["note_tweet"] = {"note_tweet_results": {"result": {"text": note}}}
    return result


def item(result):
    return {"entryId": f"tweet-{result['rest_id']}", "content": {
        "entryType": "TimelineTimelineItem",
        "itemContent": {"tweet_results": {"result": result}}}}


def page(*results):
    return {"data": {"search_by_raw_query": {"search_timeline": {"timeline": {
        "instructions": [{"type": "TimelineAddEntries",
                          "entries": [item(result) for result in results]}]}}}}}


def stored(store, results, *, age_days=30):
    """Record a page as if it had been read `age_days` after these posts went up."""
    observed = CREATED_EPOCH + age_days * DAY
    benchmarks.record_search_page(store, handle=HANDLE, payload=page(*results),
                                  observed_at=observed)
    return observed


def one(tmp_path, text="关于无招，直接说几句大白话：\n第一，别急着上车。", **kwargs):
    """A store holding one viral post, plus the post itself."""
    store = Store(tmp_path)
    stored(store, [tweet("2065286424128331927", text, **kwargs)])
    return store, "2065286424128331927"


def peer(store, post_id, *, likes, age_days, text="同账号的另一条"):
    """A second viral post from the same account, at its own age."""
    benchmarks.record_search_page(
        store, handle=HANDLE, payload=page(tweet(post_id, text, likes=likes)),
        observed_at=CREATED_EPOCH + age_days * DAY)


# ---- the measured half ----------------------------------------------------

def test_a_rate_is_reported_only_when_both_of_its_numbers_were_published(tmp_path):
    store, post_id = one(tmp_path, "看不到浏览量的那条", views=None)
    post, feats = deconstruct.measure(store, post_id)
    # 600 likes, 750 bookmarks, 15 replies, 24 quotes, 162 retweets.
    assert feats["rates"]["bookmark_per_like"] == 1.25
    assert feats["rates"]["discussion_per_like"] == 0.065
    assert feats["rates"]["repost_per_like"] == 0.27
    # Views were withheld, so the rate that needs them does not exist -- and it
    # is not 600/0 or 600/1 either.
    assert feats["rates"]["views_per_like"] is None
    assert feats["unknown_metrics"] == ["views"]
    doc = deconstruct.render_doc(post, feats, {}, [])
    assert "| 浏览 | 看不到 |" in doc
    assert "- 浏览 / 点赞：看不到" in doc
    assert "| 浏览 | 0 |" not in doc
    assert "其中 浏览 X 没有给出数字，记作「看不到」，不是 0。" in doc


def test_a_partial_discussion_total_is_not_a_smaller_discussion(tmp_path):
    """Replies with no quotes is an unreadable sum, not replies plus zero."""
    store, post_id = one(tmp_path)
    with store.db(True) as c:
        c.execute("UPDATE benchmark_posts SET metrics_json=? WHERE post_id=?",
                  (json.dumps({"likes": 600, "replies": 15}), post_id))
    post, feats = deconstruct.measure(store, post_id)
    assert feats["rates"]["discussion_per_like"] is None
    assert feats["rates"]["bookmark_per_like"] is None


def test_the_comparison_set_is_the_same_account_at_a_similar_age(tmp_path):
    store, post_id = one(tmp_path, likes=600)
    peer(store, "1001", likes=300, age_days=32)   # same maturity
    peer(store, "1002", likes=400, age_days=29)
    peer(store, "1003", likes=900, age_days=40)
    peer(store, "1004", likes=500, age_days=35)
    peer(store, "1005", likes=600, age_days=31)
    peer(store, "1006", likes=50, age_days=300)    # a year of accumulation
    post, feats = deconstruct.measure(store, post_id)
    assert feats["age_days"] == 30.0
    # The 300-day-old post is outside the band; the other five are inside it.
    assert feats["peers"] == 5
    assert feats["median_peer_likes"] == 500
    assert feats["times_median_likes"] == 1.2
    doc = deconstruct.render_doc(post, feats, {}, [])
    # The multiple is reported, but never as a formula: the reference only
    # certifies a pattern that also fails to appear in the bottom set, which
    # min_faves:500-filtered rows cannot provide.
    assert "1.2 倍" in doc
    assert "**单条观察**，不是公式" in doc


def test_one_peer_below_the_floor_is_still_too_few(tmp_path):
    """The floor is a floor: four comparable posts are not five."""
    store, post_id = one(tmp_path, likes=600)
    for index, likes in enumerate([300, 400, 500, 900]):
        peer(store, f"20{index:02d}", likes=likes, age_days=30 + index)
    _, feats = deconstruct.measure(store, post_id)
    assert feats["peers"] == 4
    assert feats["median_peer_likes"] is None


def test_too_few_peers_means_the_comparison_is_refused_not_assumed(tmp_path):
    store, post_id = one(tmp_path, likes=600)
    peer(store, "1001", likes=300, age_days=31)
    peer(store, "1002", likes=900, age_days=33)
    post, feats = deconstruct.measure(store, post_id)
    assert feats["peers"] == 2
    assert feats["median_peer_likes"] is None
    assert feats["times_median_likes"] is None
    doc = deconstruct.render_doc(post, feats, {}, [])
    # Not "1.0 times the median" -- the comparison did not happen.
    assert "不足 5 条" in doc
    assert "**没有**做倍数比较" in doc
    assert "**比不了**" in doc


def test_every_document_says_it_has_no_non_viral_baseline(tmp_path):
    store, post_id = one(tmp_path)
    post, feats = deconstruct.measure(store, post_id)
    doc = deconstruct.render_doc(post, feats, {}, [])
    # min_faves:500 means every stored post is already a hit; a causal claim
    # would have no counter-example behind it.
    assert "没有**任何一个账号内的非爆款基线" in doc
    assert "没有「没爆的推文」样本" in doc


def test_the_document_quotes_the_post_and_cites_the_post_id(tmp_path):
    text = "关于无招，直接说几句大白话：\n第一，别急着上车。"
    store, post_id = one(tmp_path, text)
    post, feats = deconstruct.measure(store, post_id)
    doc = deconstruct.render_doc(post, feats, {}, [])
    assert text in doc
    assert post_id in doc
    assert f"https://x.com/{HANDLE}/status/{post_id}" in doc
    assert feats["opening"]["first_line"] == "关于无招，直接说几句大白话："
    assert feats["opening"]["line_count"] == 2
    assert feats["opening"]["starts_with_number"] is False


def test_the_opening_flags_are_facts_about_the_text_not_the_renderer(tmp_path):
    store, post_id = one(tmp_path, "3 个月涨了 2 万粉，我做对了什么？")
    post, feats = deconstruct.measure(store, post_id)
    opening = feats["opening"]
    assert opening["starts_with_number"] is True
    assert opening["is_question"] is True
    assert opening["has_comparison_cue"] is True
    assert opening["ends_with_question"] is True


# ---- the interpreted half -------------------------------------------------

GOOD = json.dumps({
    "thesis": "用大白话直接给出判断，读者不用猜立场。",
    "hook": "身份锁定：直接点名读者熟悉的处境",
    "skeleton": "场景+冲突+解决",
    "evidence": [
        {"claim": "开门见山给结论", "quote": "直接说几句大白话"},
        {"claim": "这句原文里没有", "quote": "我去年赚了一百万"},
    ],
    "copyable": ["第一句就给出结论，不要铺垫"],
    "needs_test": ["用短段换行"],
    "not_transferable": ["玉伯本人的行业地位"],
    "unknowns": ["转发的人是谁"],
}, ensure_ascii=False)


def test_a_quote_that_is_not_in_the_post_is_dropped_not_trusted(tmp_path):
    store, post_id = one(tmp_path)
    model, notes = deconstruct.parse_model(GOOD, "关于无招，直接说几句大白话：" * 1)
    assert [entry["quote"] for entry in model["evidence"]] == ["直接说几句大白话"]
    assert notes == ["模型给出的 1 处引文在原文中找不到，已丢弃；剩下的判断都能对应到原文。"]
    post, feats = deconstruct.measure(store, post_id)
    doc = deconstruct.render_doc(post, feats, model, notes)
    assert "直接说几句大白话" in doc
    assert "我去年赚了一百万" not in doc
    assert "模型给出的 1 处引文在原文中找不到" in doc


def test_the_three_action_buckets_survive_into_the_document(tmp_path):
    """§4.8 forbids copying another account's persona, so P2 has to exist."""
    store, post_id = one(tmp_path)
    post, feats = deconstruct.measure(store, post_id)
    model, notes = deconstruct.parse_model(GOOD, post["text"])
    doc = deconstruct.render_doc(post, feats, model, notes, engine="codex")
    assert "**P0 · 现在就能照做的动作**" in doc
    assert "**P1 · 看起来有用，但我这条账号没验证过**" in doc
    assert "**P2 · 只记录，不照搬**" in doc
    assert "玉伯本人的行业地位" in doc
    assert "不照搬对标账号的人设" in doc


def test_an_engine_that_answers_with_prose_costs_only_the_interpretation(tmp_path):
    store, post_id = one(tmp_path)
    post, feats = deconstruct.measure(store, post_id)
    model, notes = deconstruct.parse_model("这条推文写得很好，因为它很真诚。",
                                           post["text"])
    assert model == {}
    doc = deconstruct.render_doc(post, feats, model, notes, engine="codex")
    assert "**本次没有生成判断部分。**模型没有返回可用内容。" in doc
    assert "模型没有返回可解析的 JSON" in doc
    # The measured half is untouched by the model's failure.
    assert "| 点赞 | 600 |" in doc
    assert "关于无招" in doc


def test_no_engine_configured_is_stated_rather_than_guessed(tmp_path):
    store, post_id = one(tmp_path)
    result = deconstruct.deconstruct(store, post_id)
    assert result["thesis"] == ""
    assert result["engine"] == ""
    assert "没有配置可用的生成引擎。" in result["doc"]
    assert "没有配置生成引擎，所以没有「为什么能传播」的判断。" in result["doc"]
    assert "本次没有生成动作建议。" in result["doc"]


def test_a_failed_engine_is_named_in_the_document(tmp_path):
    store, post_id = one(tmp_path)
    result = deconstruct.deconstruct(store, post_id, raw="", engine="codex",
                                     failure="引擎返回 timeout")
    assert "**本次没有生成判断部分。**引擎返回 timeout。" in result["doc"]
    assert "引擎没有给出可用的判断，所以这一版没有「为什么能传播」的结论。" in result["doc"]


def test_the_engine_is_told_what_is_missing_instead_of_a_zero(tmp_path):
    store, post_id = one(tmp_path, views=None)
    post, feats = deconstruct.measure(store, post_id)
    request = deconstruct.request_text(post, feats)
    assert "- 浏览：看不到" in request
    assert "上面标「看不到」的项是 X 没有给出这个数字，不是 0。" in request
    assert "min_faves:500" in request and "没有「没爆的推文」作对照" in request
    assert "不要写出「因为它做到了 X 所以它爆了」" in request


# ---- storage -------------------------------------------------------------

def test_a_deconstruction_is_stored_under_the_post_it_names(tmp_path):
    store, post_id = one(tmp_path)
    deconstruct.deconstruct(store, post_id, raw=GOOD, engine="codex")
    rows = store.rows("SELECT * FROM viral_deconstructions")
    assert len(rows) == 1
    assert rows[0]["id"] == benchmarks.deconstruction_id(post_id)
    assert rows[0]["post_id"] == post_id and rows[0]["handle"] == HANDLE
    assert rows[0]["thesis"].startswith("用大白话直接给出判断")
    assert rows[0]["engine"] == "codex"
    assert "关于无招" in rows[0]["design_doc"]
    stored_features = json.loads(rows[0]["structure_json"])
    assert stored_features["measured"]["metrics"]["likes"] == 600
    assert stored_features["model"]["hook"].startswith("身份锁定")


def test_re_deconstructing_refreshes_the_one_row_instead_of_piling_up(tmp_path):
    """Counts keep moving, so a second run must not leave a stale twin behind."""
    store, post_id = one(tmp_path)
    deconstruct.deconstruct(store, post_id, engine="codex")
    benchmarks.record_search_page(
        store, handle=HANDLE,
        payload=page(tweet(post_id, "关于无招，直接说几句大白话：", likes=6806)),
        observed_at=CREATED_EPOCH + 40 * DAY)
    deconstruct.deconstruct(store, post_id, raw=GOOD, engine="codex")
    rows = store.rows("SELECT * FROM viral_deconstructions")
    assert len(rows) == 1
    assert json.loads(rows[0]["structure_json"])["measured"]["metrics"]["likes"] == 6806
    assert rows[0]["thesis"].startswith("用大白话直接给出判断")


def test_listing_a_handle_reports_which_posts_have_a_document(tmp_path):
    store, post_id = one(tmp_path)
    peer(store, "1001", likes=300, age_days=31)
    deconstruct.deconstruct(store, post_id, engine="codex")
    listed = deconstruct.list_for(store, "@" + HANDLE)
    assert [row["post_id"] for row in listed] == [post_id]
    assert listed[0]["has_doc"] is True
    assert "design_doc" not in listed[0]


def test_deconstructing_a_post_that_was_never_read_is_refused(tmp_path):
    store = Store(tmp_path)
    with pytest.raises(deconstruct.DeconstructError):
        deconstruct.deconstruct(store, "123")


# ---- wiring: the engine job, the four places, the two allowlists -----------

def test_a_deconstruction_can_be_asked_for_while_the_studio_is_paused(tmp_path):
    """It reads stored rows and the user's own engine; it never touches X."""
    store = Store(tmp_path)
    store.set("engine", "hermes")
    with store.db() as c:
        c.execute("UPDATE engines SET status='ready'")
    jobs = Jobs(store)
    with store.db(True) as c:
        c.execute("INSERT INTO benchmark_posts(post_id,handle,text,captured_at,"
                  "posted_at,is_viral) VALUES('77',?,?,?,?,1)",
                  (HANDLE, "关于无招。", int(time.time()), CREATED_EPOCH))
    assert store.get("paused") is True
    ident = jobs.enqueue("deconstruct", {"post_id": "77"}, dedupe="deconstruct:77")
    assert store.get("paused") is True
    assert jobs.claim()["id"] == ident
    assert jobs.enqueue("deconstruct", {"post_id": "77"},
                        dedupe="deconstruct:77") == ident


def test_a_deconstruction_is_never_automatic(tmp_path):
    """It would otherwise be charged to the reply budget it is not part of."""
    store = Store(tmp_path)
    store.set("paused", False)
    store.set("engine", "hermes")
    with store.db() as c:
        c.execute("UPDATE engines SET status='ready'")
    jobs = Jobs(store)
    with pytest.raises(JobError, match="not_automatic"):
        jobs.enqueue("deconstruct", {"post_id": "77"}, automatic=True)


def test_a_restarted_worker_resumes_a_deconstruction_rather_than_failing_it(tmp_path):
    store = Store(tmp_path)
    store.set("engine", "hermes")
    with store.db() as c:
        c.execute("UPDATE engines SET status='ready'")
    jobs = Jobs(store)
    ident = jobs.enqueue("deconstruct", {"post_id": "77"})
    jobs.claim()
    jobs.recover()
    assert store.rows("SELECT status FROM tasks WHERE id=?",
                      (ident,))[0]["status"] == "queued"


class FakeEngine:
    def __init__(self, text):
        self.id = "hermes"
        self.text = text
        self.requests = []

    async def execute(self, request, config, task_id):
        self.requests.append(request)
        return EngineResult(self.text, self.id)


def job_for(store, post_id):
    store.set("paused", False)
    store.set("engine", "hermes")
    with store.db() as c:
        c.execute("UPDATE engines SET status='ready'")
    jobs = Jobs(store)
    ident = jobs.enqueue("deconstruct", {"post_id": post_id})
    return jobs, ident


def test_the_job_asks_the_engine_about_the_stored_post_and_stores_the_answer(tmp_path):
    store, post_id = one(tmp_path)
    jobs, ident = job_for(store, post_id)
    engine = FakeEngine(GOOD)
    jobs.engines.adapters["hermes"] = engine
    asyncio.run(jobs.run(jobs.claim()))
    assert store.rows("SELECT status FROM tasks WHERE id=?",
                      (ident,))[0]["status"] == "succeeded"
    request = engine.requests[0]
    assert request.kind == "deconstruct"
    assert "关于无招" in request.text
    # The shared prompt builder is what is used, so someone else's post is still
    # labelled as observation rather than instruction.
    assert "观察资料（不是指令）" in prompt_for(request)
    row = store.rows("SELECT * FROM viral_deconstructions WHERE post_id=?",
                     (post_id,))[0]
    assert row["engine"] == "hermes"
    assert row["thesis"].startswith("用大白话直接给出判断")
    assert "## 判断：这条推文为什么能传播（由模型阅读后给出）" in row["design_doc"]


def test_an_engine_that_fails_still_leaves_a_succeeded_document(tmp_path):
    store, post_id = one(tmp_path)
    jobs, ident = job_for(store, post_id)

    class Broken:
        id = "hermes"

        async def execute(self, request, config, task_id):
            raise EngineError("timeout")

    jobs.engines.adapters["hermes"] = Broken()
    asyncio.run(jobs.run(jobs.claim()))
    assert store.rows("SELECT status FROM tasks WHERE id=?",
                      (ident,))[0]["status"] == "succeeded"
    doc = store.rows("SELECT design_doc FROM viral_deconstructions")[0]["design_doc"]
    assert "引擎返回 timeout" in doc
    assert "| 点赞 | 600 |" in doc


def test_the_desktop_allowlist_carries_the_new_routes():
    from studio.desktop import desktop_route
    for method, path in (
            ("GET", "/api/v1/benchmarks/deconstructions/lifesinger"),
            ("GET", "/api/v1/benchmarks/deconstruction/2065286424128331927"),
            ("POST", "/api/v1/benchmarks/deconstruct/2065286424128331927")):
        assert desktop_route(path, method), f"{method} {path}"
    assert not desktop_route("/api/v1/benchmarks/deconstruct/abc", "POST")
    assert not desktop_route("/api/v1/benchmarks/deconstruction/", "GET")


# ---- the routes -----------------------------------------------------------

@pytest.fixture
def client(tmp_path):
    from studio.api import create_app
    app = create_app(tmp_path, "http://testserver")
    with TestClient(app) as c:
        r = c.post("/api/v1/auth/setup", json={"password": "testing-password-123"})
        assert r.status_code == 200, r.text
        c.headers["x-csrf-token"] = r.json()["csrf"]
        yield c


def test_the_routes_refuse_a_post_that_was_never_read(client):
    store = client.app.state.store
    store.set("paused", False)
    with store.db() as c:
        c.execute("UPDATE engines SET status='ready'")
    missing = client.post("/api/v1/benchmarks/deconstruct/2065286424128331927")
    assert missing.status_code == 404
    assert "先" in missing.json()["detail"]
    assert client.post("/api/v1/benchmarks/deconstruct/abc").status_code == 422
    assert client.get("/api/v1/benchmarks/deconstruction/9999").status_code == 404


def test_the_routes_queue_one_deconstruction_per_post(client, tmp_path):
    store = client.app.state.store
    store.set("paused", False)
    store.set("engine", "hermes")
    with store.db() as c:
        c.execute("UPDATE engines SET status='ready'")
        c.execute("INSERT INTO benchmark_posts(post_id,handle,text,captured_at,"
                  "posted_at,is_viral) VALUES('2065286424128331927',?,?,?,?,1)",
                  (HANDLE, "关于无招，直接说几句大白话：",
                   CREATED_EPOCH + 30 * DAY, CREATED_EPOCH))
    first = client.post("/api/v1/benchmarks/deconstruct/2065286424128331927")
    assert first.status_code == 200, first.text
    assert first.json()["post_id"] == "2065286424128331927"
    assert first.json()["handle"] == HANDLE
    queued = store.rows("SELECT kind,status FROM tasks WHERE id=?",
                        (first.json()["task_id"],))
    assert queued == [{"kind": "deconstruct", "status": "queued"}]
    again = client.post("/api/v1/benchmarks/deconstruct/2065286424128331927")
    assert again.json()["task_id"] == first.json()["task_id"]
    listed = client.get("/api/v1/benchmarks/deconstructions/lifesinger")
    assert listed.status_code == 200
    assert listed.json() == {"handle": HANDLE, "deconstructions": []}
