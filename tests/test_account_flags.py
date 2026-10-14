"""06C-3: 风险硬标签与观察室——标签不等分数，比例不等结论，「查不了」不等「没有」。

The rule layer is tested on hand-written feature rows, the same way 06C-2's is:
that is the only way to pin one rule without also pinning four reads. Four
properties get their own test because they are the whole point of the design:

* **硬标签绕过分数门槛** —— 同一批特征上，专业性分可以是 NULL，标签照样成立；
* **宁少勿滥**（方案 §六 看精确率）—— 2 条回复里 2 条是空壳不是「100% 空壳」；
* **三句话** —— 「看过、没触发」/「这一次没评」/「这条通道根本查不了」不能混；
* **流水账与视图** —— `account_flags` 是命中流水，观察室显示的是「这一次」的标签。
"""
import time

import pytest
from fastapi.testclient import TestClient

from studio import account_flags, account_quality, account_scoring
from studio.account_flags import BLOCKED, CODES, MIN_REPLIES
from studio.api import create_app
from studio.desktop import desktop_route
from studio.security import digest

# Reused verbatim from 06C-1's suite so the end-to-end tests here drive the same
# reads the worker drives. There is no `conftest.py`: `tests/` is flat and pytest
# puts it on `sys.path` before importing this module.
from test_account_quality import (HANDLE, app_store, finished, job_of, observe,
                                  queued, stored)


def feature(name, value):
    """A feature row shaped the way `account_flags._values` reads them."""
    return {"name": name, "known": 1 if value is not None else 0, "value": value}


def rows(**values):
    """Feature rows for one account, with anything unmentioned left unread."""
    return [feature(name, value) for name, value in values.items()]


def looked(features, *, received=32, confidence="ok"):
    counts = {"profile": 1, "post": 12, "received": received, "reply": 0}
    return account_flags.flags(features, counts=counts, confidence=confidence)


def codes(result):
    return [one["code"] for one in result["flags"]]


def check(result, ident):
    """The ledger entry for a rule that ran, or None when it never ran."""
    return next((one for one in result["checks"] if one["id"] == ident), None)


def skipped(result, ident):
    """Why a rule did not run this time, or None when it did."""
    return next((one["blocked"] for one in result["skipped"] if one["id"] == ident),
                None)


# --- 硬标签不等分数 ---------------------------------------------------------

def test_a_hard_flag_does_not_wait_for_the_scores():
    """方案 §三 第 3 步：「先用规则打硬标签，再用模型打连续分」——两件事。

    这一批特征里原帖和它自己的回复一条都没有，所以专业性分是 NULL：分数层说
    「这一层没有可看的文本」。空壳互动者的证据却在（别人怎么跟它互动是另一层），
    标签成立。标签说的是「这件事被看见了」，不是「综合起来像它」。
    """
    features = rows(empty_engager_rate=0.9)
    counts = {"profile": 0, "post": 0, "received": 20, "reply": 0}
    scored = account_scoring.score(features, counts=counts,
                                   confidence="insufficient_data")
    flagged = account_flags.flags(features, counts=counts,
                                 confidence="insufficient_data")
    assert scored["scores"]["professionalism"]["value"] is None
    assert codes(flagged) == ["risk_bot"]
    # 标签自己不携带任何分数，也不因此把置信度抬高。它继承这一次读取的置信度。
    assert "value" not in flagged["flags"][0]
    assert flagged["flags"][0]["confidence"] == flagged["confidence"] == \
        "insufficient_data"


def test_a_thin_reply_sample_makes_a_ratio_not_count():
    """方案 §六：「每周抽 50 个账号人工复核，看**精确率**」。

    2 条回复里 2 条是空壳不是「100% 空壳」，是「什么都看不出来」。所以比例要
    先有 `MIN_REPLIES` 条回复才算数——否则一个精确率好看的假象会被写进观察室，
    而观察室的结论是要拿去决定信不信这些标签的。
    """
    thin = looked(rows(empty_engager_rate=1.0), received=2)
    assert codes(thin) == []
    assert check(thin, "flag_shell_audience") is None
    assert "这次只读到 2 条回复" in skipped(thin, "flag_shell_audience")
    assert str(MIN_REPLIES) in skipped(thin, "flag_shell_audience")
    # 刚好够就作数：门槛是「不到 10 条」。
    enough = looked(rows(empty_engager_rate=1.0), received=MIN_REPLIES)
    assert codes(enough) == ["risk_bot"]


def test_looked_and_did_not_fire_is_not_could_not_look():
    """三句话必须是三句：看过没触发 / 这一次没读到 / 这条通道查不了。"""
    result = looked(rows(empty_engager_rate=0.1))
    # (a) 看过、没触发：它在 checks 里，并且明说 fired 是 False。
    low = check(result, "flag_shell_audience")
    assert low and low["fired"] is False and low["because"]
    assert skipped(result, "flag_shell_audience") is None
    # (b) 这一次没评：需要的那几个特征没读到，所以规则根本没跑。
    assert "没有读到" in skipped(result, "flag_inauthentic_engagement")
    # (c) 这条通道查不了：方案点名要、这一条读取路径永远给不出的那几条。
    assert [one["id"] for one in result["blocked"]] == [one["id"] for one in BLOCKED]
    assert all(len(one["blocked"]) > 20 for one in result["blocked"])
    # 三个名单不重样，否则面板会把同一件事说两遍（或者把没查说成没问题）。
    ran = {one["id"] for one in result["checks"]}
    unread = {one["id"] for one in result["skipped"]} - {one["id"] for one in BLOCKED}
    never = {one["id"] for one in BLOCKED}
    assert not ran & unread and not ran & never and not unread & never
    assert len({one["id"] for one in result["skipped"]}) == len(result["skipped"])


def test_every_line_says_who_set_it():
    """方案只给了两个数，其余是线由本机定——面板要照抄给读者看。"""
    by_id = {rule["id"]: rule for rule in account_flags.rules()}
    assert {rule["source"] for rule in account_flags.rules()} == {"方案", "本机"}
    assert by_id["flag_shell_audience"]["source"] == "方案"
    assert by_id["flag_inauthentic_engagement"]["source"] == "方案"
    assert by_id["flag_reciprocal_engagers"]["source"] == "本机"
    assert by_id["flag_sold_commerce"]["source"] == "本机"
    thresholds = account_flags.thresholds()
    # 方案 §二 的两个数：空壳互动者 > 40%、模板回复 > 60%。改线必须同时改这句话。
    assert thresholds["shell_flag_rate"] == 0.40
    assert thresholds["cliche_flag_rate"] == 0.60
    assert "40%" in by_id["flag_shell_audience"]["because"]
    assert "60%" in by_id["flag_inauthentic_engagement"]["because"]
    assert "本机" in by_id["flag_reciprocal_engagers"]["because"]


# --- 一条规则不等于一个标签 --------------------------------------------------

def test_two_rules_landing_on_one_code_are_one_label():
    """表是按 `(feature_id, code)` 建的：两条理由命中同一件事，是一个标签。"""
    features = rows(empty_engager_rate=0.9, reply_cliche_rate=0.8,
                    reply_like_ratio_median=0.001)
    result = looked(features)
    assert codes(result) == ["risk_bot"]
    one = result["flags"][0]
    assert sorted(one["rules"]) == ["flag_inauthentic_engagement",
                                    "flag_shell_audience"]
    # 两条理由都留着：「为什么是这个标」要能答上来。
    assert "空壳互动者" in one["evidence"] and "模板回复" in one["evidence"]
    assert one["label"] == account_flags.CODE_LABEL["risk_bot"]
    # 四个 code 的顺序是方案 §三 第 4 步的顺序，命中的排前面，没命中的不出现。
    assert set(codes(result)) <= set(CODES)


def test_linking_alone_is_not_shilling():
    """方案 §四 把「高意向 + 高链接」列为一个**正常类别**（强带货号）。

    So 带链接 alone must not flag: it takes a second, independent signal saying
    the conversation around it is templated.
    """
    assert codes(looked(rows(link_share=0.9, reply_cliche_rate=0.2))) == []
    high = check(looked(rows(link_share=0.9, reply_cliche_rate=0.2)),
                 "flag_sold_commerce")
    assert high["fired"] is False
    fired = looked(rows(link_share=0.9, reply_cliche_rate=0.7))
    assert codes(fired) == ["risk_shill"]
    assert "正常类别" in fired["flags"][0]["evidence"]
    # 两条线的开闭都是有意选的：链接占比是 ≥，模板回复是 >。
    assert codes(looked(rows(link_share=0.5, reply_cliche_rate=0.61))) == ["risk_shill"]
    assert codes(looked(rows(link_share=0.5, reply_cliche_rate=0.6))) == []
    # 空壳那条同样是 >：40% 整不算。
    assert codes(looked(rows(empty_engager_rate=0.40))) == []
    assert codes(looked(rows(empty_engager_rate=0.41))) == ["risk_bot"]


def test_the_pod_rule_calls_itself_a_proxy():
    """真正的判据是互动者共现率，单账号一次读取拿不到——所以它必须自称弱代理。"""
    result = looked(rows(engager_repeat_rate=0.6, engager_median_followers=50))
    assert codes(result) == ["risk_pod"]
    assert "弱代理" in result["flags"][0]["evidence"]
    assert any(one["id"] == "flag_pod_co_engagement" for one in result["blocked"])
    # 重复率高、粉丝中位数也高的小圈子里的真号不算——两条线要同时成立。
    assert codes(looked(rows(engager_repeat_rate=0.6,
                             engager_median_followers=5000))) == []


# --- 库里的那一份是流水账 ----------------------------------------------------

def run_once(tmp_path, *steps):
    """A real observation: its feature row has to exist before flags can point at it."""
    store = app_store(tmp_path)
    queued(store)
    summary = observe(store, job_of(store), *(steps or finished()))
    return store, summary


def test_every_flag_row_says_which_rule_version_drew_it(tmp_path):
    store, summary = run_once(tmp_path)
    ident = summary["feature_id"]
    account_flags.record(store, ident, HANDLE, summary["observed_at"],
                         looked(rows(empty_engager_rate=0.9)))
    written = account_flags.for_feature(store, ident)
    assert len(written) == 1
    one = written[0]
    # 规则以后会改，所以每一行都要说清是哪个版本画的——否则旧标签没法解释。
    assert one["rule_version"] == account_flags.RULE_VERSION
    assert one["code"] == "risk_bot" and one["layer"] == account_flags.LAYER_RECEIVED
    assert one["handle"] == HANDLE and one["evidence"]
    assert one["confidence"] == summary["confidence"]


def test_a_flag_that_stopped_firing_is_not_left_hanging(tmp_path):
    store, summary = run_once(tmp_path)
    ident, at = summary["feature_id"], summary["observed_at"]
    account_flags.record(store, ident, HANDLE, at,
                         looked(rows(empty_engager_rate=0.9)))
    assert [one["code"] for one in account_flags.for_feature(store, ident)] == \
        ["risk_bot"]
    # 重算会整段覆盖：这一次没命中的标签必须真的消失，否则「这个标现在还在不在」
    # 就答不上来。
    account_flags.record(store, ident, HANDLE, at,
                         looked(rows(empty_engager_rate=0.1)))
    assert account_flags.for_feature(store, ident) == []
    room = account_flags.watchroom(store)
    # 观察室说的是「它现在为什么在榜上」，所以这一次的标签是空的……
    assert room["watchlist"][0]["flags"] == []
    # ……但它来过这件事留在流水账里，命中次数不会被抹掉。
    assert room["watchlist"][0]["hits"] == 1
    assert room["watchlist"][0]["hits_json"] == ["flag_shell_audience"]


def test_the_watchlist_counts_one_hit_per_observation(tmp_path):
    store, summary = run_once(tmp_path)
    ident, at = summary["feature_id"], summary["observed_at"]
    first = looked(rows(empty_engager_rate=0.9))
    account_flags.record(store, ident, HANDLE, at, first)
    # 同一次观察被重跑（worker 重试、或者同一份特征重算）：`last_hit` 挡住重复计数。
    account_flags.record(store, ident, HANDLE, at, first)
    account_flags.record(store, ident, HANDLE, at, first)
    entry = account_flags.entry(store, HANDLE)
    assert entry["hits"] == 1 and entry["last_hit"] == at
    assert entry["hits_json"] == ["flag_shell_audience"]
    # 换一次观察才算第二次命中；命中的规则是并集，新的一条加进去、旧的不丢。
    account_flags.record(store, ident, HANDLE, at + 86400,
                         looked(rows(engager_repeat_rate=0.6,
                                     engager_median_followers=50)))
    entry = account_flags.entry(store, HANDLE)
    assert entry["hits"] == 2 and entry["last_hit"] == at + 86400
    assert entry["hits_json"] == ["flag_reciprocal_engagers", "flag_shell_audience"]
    # 自动上榜的账号一开始就是「待复核」，不是「已经确认有问题」。
    assert entry["status"] == "watching" and entry["note"] == ""


def test_a_run_with_no_flags_never_touches_the_watchlist(tmp_path):
    store, summary = run_once(tmp_path)
    account_flags.record(store, summary["feature_id"], HANDLE,
                         summary["observed_at"], looked(rows(empty_engager_rate=0.0)))
    assert account_flags.entry(store, HANDLE) is None
    assert account_flags.watchroom(store)["watchlist"] == []


# --- 观察室 -----------------------------------------------------------------

def test_precision_is_unknown_until_someone_has_looked(tmp_path):
    """方案 §六 要的是精确率，而精确率只有人的结论给得出来。"""
    store = app_store(tmp_path)
    account_flags.watch(store, HANDLE)
    room = account_flags.watchroom(store)
    # 一次都没复核过时它是 None，不是 0：0 会说「精确率是 0%」，
    # 而事实是「还没有人量过」。面板渲染「暂无数据」。
    assert room["review"] == {"reviewed": 0, "confirmed": 0, "dismissed": 0,
                              "pending": 1, "precision": None}
    account_flags.watch(store, "someoneelse")
    account_flags.review(store, HANDLE, verdict="confirmed", note="确实是刷的")
    account_flags.review(store, "someoneelse", verdict="dismissed", note="同行而已")
    room = account_flags.watchroom(store)
    assert room["review"] == {"reviewed": 2, "confirmed": 1, "dismissed": 1,
                              "pending": 0, "precision": 0.5}
    assert {one["status"] for one in room["statuses"]} == set(account_flags.STATUSES)
    assert [one["code"] for one in room["codes"]] == list(CODES)


def test_the_review_queue_cap_is_disclosed_not_silent(tmp_path):
    """方案 §六 说「每周抽 50 个」；一个不告诉你的截断会被读成「一共就这么多」。"""
    store = app_store(tmp_path)
    for n in range(account_flags.REVIEW_CAP + 1):
        account_flags.watch(store, f"ghost{n:02d}")
    room = account_flags.watchroom(store)
    assert room["cap"] == account_flags.REVIEW_CAP == 50
    assert room["total"] == 51 and room["truncated"] is True
    assert len(room["watchlist"]) == 50
    # 截断的是返回的那一份，不是统计：那一个没进队列的账号仍在待复核里。
    assert room["review"]["pending"] == 51


def test_the_queue_puts_pending_reviews_before_finished_ones(tmp_path):
    """否则复核过的账号会占满那 50 个名额，把还没人看过的挤出队列。"""
    store = app_store(tmp_path)
    account_flags.watch(store, "reviewedone")
    account_flags.review(store, "reviewedone", verdict="confirmed")
    account_flags.watch(store, "stillpending")
    room = account_flags.watchroom(store)
    assert [one["handle"] for one in room["watchlist"]] == ["stillpending",
                                                           "reviewedone"]


def test_watching_again_does_not_undo_a_verdict(tmp_path):
    """人给的结论比机器给的分数贵，所以再加一次不能把它冲掉。"""
    store = app_store(tmp_path)
    account_flags.watch(store, HANDLE, note="先在旁边盯着")
    account_flags.review(store, HANDLE, verdict="dismissed", note="误报",
                         action="不用管")
    again = account_flags.watch(store, HANDLE, note="再记一次")
    assert again["status"] == "dismissed" and again["action"] == "不用管"
    assert again["note"] == "再记一次"
    # 不给新备注就保留旧的，而不是清空。
    assert account_flags.watch(store, HANDLE)["note"] == "再记一次"


def test_the_watchroom_says_when_an_account_is_not_in_it(tmp_path):
    store = app_store(tmp_path)
    assert account_flags.forget(store, HANDLE) is False
    with pytest.raises(account_flags.AccountFlagsError) as raised:
        account_flags.review(store, HANDLE, verdict="confirmed")
    assert "不在观察室" in str(raised.value)
    with pytest.raises(account_flags.AccountFlagsError) as raised:
        account_flags.review(store, HANDLE, verdict="大概吧")
    assert "结论只能是" in str(raised.value)
    for bad in ("中文名字", "", "@", "a" * 16, "with-dash"):
        with pytest.raises(account_flags.AccountFlagsError):
            account_flags.watch(store, bad)
    assert account_flags.watch(store, "@LifeSinger")["handle"] == HANDLE
    assert account_flags.forget(store, "@LIFESINGER") is True


# --- 路由 -------------------------------------------------------------------

def flags_client(tmp_path):
    app = create_app(tmp_path, "http://testserver")
    store = app.state.store
    store.set("paused", False)
    token = "flags-session"
    with store.db(True) as db:
        db.execute("INSERT INTO sessions(token_hash,csrf,expires) VALUES(?,?,?)",
                   (digest(token), "csrf", int(time.time()) + 600))
    return app, store, {"cookie": f"studio_session={token}", "x-csrf-token": "csrf"}


def test_the_four_watchroom_paths_survive_the_connector_allowlist():
    """漏一处白名单，云面板就是静默 403——06B-1 漏过一次，这里钉住。

    The extension's half of this pair lives in
    `tests/desktop_extension.test.mjs`; both lists have to agree.
    """
    assert desktop_route("/api/v1/accounts/watchroom", "GET")
    assert desktop_route(f"/api/v1/accounts/watchroom/{HANDLE}/watch", "POST")
    assert desktop_route(f"/api/v1/accounts/watchroom/{HANDLE}/review", "POST")
    assert desktop_route(f"/api/v1/accounts/watchroom/{HANDLE}", "DELETE")
    assert not desktop_route(f"/api/v1/accounts/watchroom/{HANDLE}", "GET")
    assert not desktop_route(f"/api/v1/accounts/watchroom/{HANDLE}/hack", "POST")
    assert not desktop_route("/api/v1/accounts/watchroom", "POST")
    assert not desktop_route(f"/api/v1/accounts/watchroom/{HANDLE}/watch", "GET")


def test_the_route_hands_the_panel_the_flags_and_their_ledger(tmp_path):
    app, store, headers = flags_client(tmp_path)
    queued(store)
    observe(store, job_of(store), *finished())
    with TestClient(app) as client:
        body = client.get(f"/api/v1/accounts/quality/{HANDLE}", headers=headers).json()
    assert body["flags"]["rule_version"] == account_flags.RULE_VERSION
    # 模型层没做，这一片也不假装做了。
    assert body["flags"]["model_version"] == "" and body["flags"]["model_note"]
    # 提到顶层与留在 run 里是同一份：面板两处都要用它。
    assert body["run"]["flags"] == body["flags"]
    assert body["flags"]["checks"] and body["flags"]["skipped"]
    assert all(one["because"] for one in body["flags"]["checks"])
    assert all(one["blocked"] for one in body["flags"]["skipped"])
    assert [one["id"] for one in body["flags"]["blocked"]] == \
        [one["id"] for one in BLOCKED]


def test_the_watchroom_route_keeps_the_two_sets_of_lines_apart(tmp_path):
    """标签层自己那组线不能被采集层那组同名盖掉——盖掉面板就会用错数解释标签。"""
    app, store, headers = flags_client(tmp_path)
    with TestClient(app) as client:
        room = client.get("/api/v1/accounts/watchroom", headers=headers).json()
        # 观察室没有单账号详情页：它要说的是一张表，不是一个人。账号的详情在
        # `/accounts/quality/{handle}`（那里对没读过的账号回 `run: null`）。
        assert client.get("/api/v1/accounts/watchroom/nobodyhere",
                          headers=headers).status_code == 404
    assert room["thresholds"]["review_cap"] == account_flags.REVIEW_CAP
    assert room["thresholds"]["min_replies"] == MIN_REPLIES
    assert room["sample_thresholds"]["days"] == account_quality.SAMPLE_WINDOW_DAYS
    assert "review_cap" not in room["sample_thresholds"]
    assert room["watchlist"] == [] and room["total"] == 0
    assert room["review"]["precision"] is None
    # 复核的人也要看得见「这四个标这一路读取本来就没打过」——名单上没出现某个标
    # 不等于那件事没发生，所以这一份也要跟着观察室走出去。
    assert [entry["code"] for entry in room["blocked"]] == [
        "risk_bot", "risk_pod", "risk_unverified_guru", "risk_bot"]
    assert all(len(entry["blocked"]) > 20 for entry in room["blocked"])
    # 队列渲染要的那三样：上限、是不是被截断了、以及码表。
    assert room["cap"] == account_flags.REVIEW_CAP and room["truncated"] is False
    assert [one["code"] for one in room["codes"]] == list(account_flags.CODES)


def test_the_watchroom_routes_write_what_they_say_they_write(tmp_path):
    app, store, headers = flags_client(tmp_path)
    with TestClient(app) as client:
        watched = client.post(f"/api/v1/accounts/watchroom/{HANDLE}/watch",
                              headers=headers, json={"note": "先在旁边盯着"}).json()
        assert watched["handle"] == HANDLE and watched["hits"] == 0
        assert watched["status"] == "watching"
        verdict = client.post(f"/api/v1/accounts/watchroom/{HANDLE}/review",
                              headers=headers,
                              json={"verdict": "dismissed", "note": "误报",
                                    "action": "不用管"}).json()
        assert verdict["status"] == "dismissed" and verdict["note"] == "误报"
        assert client.post(f"/api/v1/accounts/watchroom/{HANDLE}/review",
                           headers=headers,
                           json={"verdict": "大概吧"}).status_code == 422
        assert client.post("/api/v1/accounts/watchroom/nope!/review",
                           headers=headers,
                           json={"verdict": "confirmed"}).status_code == 422
        removed = client.request("DELETE",
                                 f"/api/v1/accounts/watchroom/{HANDLE}",
                                 headers=headers)
        assert removed.json() == {"handle": HANDLE, "removed": True}
        assert client.request("DELETE", f"/api/v1/accounts/watchroom/{HANDLE}",
                              headers=headers).status_code == 404
        # 账号观察的四个读取一条都没带来：观察室不吃读取，它是人手写的账。
        assert stored(store, "account_watchlist") == []
