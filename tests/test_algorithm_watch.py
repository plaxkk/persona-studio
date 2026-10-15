"""06D: 算法更新跟踪——不写没读到的东西，不把「看不到」写成 0。

The network is the whole point of this feature and the whole risk of it, so
`check()` takes an injectable `fetch` and every test here drives a hand-written
GitHub response: 304, a 200 with nothing new, a 200 with a new commit, and each
way the read can fail. No byte leaves the machine in this file.

Four properties get their own tests because they are the design:

* **没读到就不写事件** —— 限流/连不上只改状态，不当成「上游动了」；
* **第一次只记起点** —— 否则整个仓库的历史会被当成一次更新报出来；
* **「看不到」不是 0** —— 中间隔着别的提交时 `intervening` 是 NULL，
  文件清单超过 300 条时既写总数也写「只列了前 300 条」；
* **通知至多一次** —— 抢不到键就不发，宁可不发也不重发。
"""
import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from studio import algorithm_watch
from studio.api import create_app
from studio.desktop import desktop_route
from studio.jobs import Jobs
from studio.notify import Notifier
from studio.security import digest

REPO = algorithm_watch.REPO


def store_for(tmp_path):
    return create_app(tmp_path, "http://testserver").state.store


def commit(sha, *, message="改点东西", parent="", author="某人",
           date="2026-10-01T00:00:00Z"):
    return {
        "sha": sha,
        "commit": {"message": message, "author": {"name": author, "date": date},
                   "committer": {"date": date}},
        "parents": [{"sha": parent}] if parent else [],
    }


def body(paths, *, total=None):
    """A commit-detail body, shaped like GitHub's.

    `total` is what `stats.total` says; leave it None to send a body with no
    stats block at all, which is how GitHub answers for some very old commits.
    """
    items = [{"filename": path, "additions": 3, "deletions": 1} for path in paths]
    out = {"files": items}
    if total is not None:
        out["stats"] = {"total": total}
    return out


class Fake:
    """Hand-written GitHub. Each test says what each URL answers."""

    def __init__(self, listing, details=None):
        self.listing = listing
        self.details = details or {}
        self.seen = []

    def __call__(self, url, headers):
        self.seen.append((url, headers))
        if "/commits/" in url:
            sha = url.rsplit("/", 1)[-1]
            answer = self.details.get(sha, (200, {}, body([])))
        else:
            answer = self.listing
        # A callable lets one test make a single URL blow up without a new class.
        return answer(url, headers) if callable(answer) else answer


def listing(sha, *, etag='"e1"', message="改点东西", parent="", **rest):
    return (200, {"etag": etag}, [commit(sha, message=message, parent=parent, **rest)])


def detail(paths, *, total=None, code=200):
    return (code, {}, body(paths, total=total))


def first(store, sha="a" * 40, when=200):
    """Get past the baseline so the next check is a real comparison."""
    return algorithm_watch.check(store, fetch=Fake(listing(sha)), now=when)


def changed(store, paths, *, sha="b" * 40, parent="a" * 40, when=300, **rest):
    """Walk one real change all the way into the event list and hand it back."""
    algorithm_watch.check(
        store, fetch=Fake(listing(sha, parent=parent, **rest),
                          {sha: detail(paths, total=len(paths))}), now=when)
    return algorithm_watch.events(store)[0]


# --- 状态层 -----------------------------------------------------------------

def test_a_failed_read_is_not_a_change(tmp_path):
    """连不上 GitHub 只该改状态，不该冒出「上游动了」。"""
    store = store_for(tmp_path)

    def boom(url, headers):
        raise OSError("no route to host")

    result = algorithm_watch.check(store, fetch=boom, now=100)
    assert result["status"] == "network_error" and result["changed"] is False
    assert result["event_id"] == ""
    assert algorithm_watch.events(store) == []
    state = algorithm_watch.upstream(store)
    assert state["last_status"] == "network_error"
    assert state["last_checked"] == 100 and state["last_sha"] == ""
    assert state["last_ok"] is None
    # 没读到上游，但这件事本身要让人知道——不然页面会一直安静地显示旧状态。
    assert result["notice"]["key"].startswith(f"algorithm:{REPO}:fail:network_error:")


def test_rate_limiting_reads_differently_from_being_refused(tmp_path):
    """403 有两种：配额用完了（等一会儿能好）和权限被拒（等也没用）。"""
    store = store_for(tmp_path)
    limited = lambda url, headers: (403, {"x-ratelimit-remaining": "0"}, None)
    refused = lambda url, headers: (403, {"x-ratelimit-remaining": "57"}, None)
    assert algorithm_watch.check(store, fetch=limited)["status"] == "rate_limited"
    assert algorithm_watch.check(store, fetch=refused)["status"] == "forbidden"
    assert algorithm_watch.upstream(store)["last_status"] == "forbidden"
    # 只有碰运气的那几种才值得一小时后再试。
    assert "rate_limited" in algorithm_watch.TRANSIENT
    assert "forbidden" not in algorithm_watch.TRANSIENT


@pytest.mark.parametrize("code,status", [
    (401, "unauthorized"), (404, "not_found"), (500, "upstream_error"),
    (503, "upstream_error"), (204, "bad_response"), (200, "bad_response"),
])
def test_every_failure_has_its_own_name(tmp_path, code, status):
    store = store_for(tmp_path)
    payload = [] if code == 200 else None
    result = algorithm_watch.check(store, fetch=lambda url, h: (code, {}, payload))
    assert result["status"] == status and result["changed"] is False
    assert algorithm_watch.events(store) == []


def test_a_commit_without_a_sha_is_a_bad_response(tmp_path):
    store = store_for(tmp_path)
    result = algorithm_watch.check(store, fetch=lambda url, h: (200, {}, [{}]))
    assert result["status"] == "bad_response"
    assert algorithm_watch.events(store) == []


def test_the_first_check_only_records_a_starting_point(tmp_path):
    """第一次不报事件：否则整个仓库的历史会被当成「这次更新」。"""
    store = store_for(tmp_path)
    where = "a" * 40
    result = algorithm_watch.check(store, fetch=Fake(listing(where)), now=200)
    assert result["status"] == "baseline" and result["sha"] == where
    assert result["event_id"] == "" and algorithm_watch.events(store) == []
    state = algorithm_watch.upstream(store)
    assert state["last_sha"] == where and state["baseline"] == 1
    # 它把 Etag 也记下了，下一次可以直接拿 304。
    assert state["etag"] == '"e1"'


def test_a_304_says_nothing_changed_and_writes_no_event(tmp_path):
    store = store_for(tmp_path)
    first(store)
    result = algorithm_watch.check(store, fetch=lambda url, h: (304, {}, None), now=300)
    assert result["status"] == "not_modified" and result["notice"] is None
    assert algorithm_watch.events(store) == []
    assert algorithm_watch.upstream(store)["last_checked"] == 300


def test_the_second_check_sends_the_etag_back(tmp_path):
    store = store_for(tmp_path)
    first(store)
    fake = Fake(listing("a" * 40, etag='"e2"'))
    algorithm_watch.check(store, fetch=fake, now=300)
    assert fake.seen[0][1]["If-None-Match"] == '"e1"'
    assert algorithm_watch.upstream(store)["etag"] == '"e2"'


def test_the_same_commit_never_becomes_two_events(tmp_path):
    store = store_for(tmp_path)
    first(store)
    new = "b" * 40
    twice = lambda: Fake(listing(new, parent="a" * 40),
                         {new: detail(["phoenix/x.rs"], total=1)})
    algorithm_watch.check(store, fetch=twice(), now=300)
    # 检查又跑了一次，上游没有新东西——还是那条 200 的老提交。
    second = algorithm_watch.check(store, fetch=twice(), now=400)
    assert second["status"] == "ok" and second["notice"] is None
    assert len(algorithm_watch.events(store)) == 1


# --- 事件层 -----------------------------------------------------------------

def test_a_real_change_becomes_one_event_with_a_summary(tmp_path):
    store = store_for(tmp_path)
    first(store)
    new = "b" * 40
    paths = ["home-mixer/feed.rs", "phoenix/ranker/score.rs", "phoenix/model/config.py"]
    fake = Fake(listing(new, message="调整排序权重", parent="a" * 40),
                {new: detail(paths, total=len(paths))})
    result = algorithm_watch.check(store, fetch=fake, now=300)

    assert result["status"] == "changed" and result["changed"] is True
    assert result["event_id"] and result["notice"]["key"] == f"algorithm:{REPO}:{new}"
    assert result["notice"]["text"].startswith(f"{REPO} bbbbbbb")
    rows = algorithm_watch.events(store)
    assert len(rows) == 1
    row = rows[0]
    assert row["sha"] == new and row["parent_sha"] == "a" * 40
    assert row["message"] == "调整排序权重" and row["author"] == "某人"
    assert row["url"] == f"https://github.com/{REPO}/commit/{new}"
    assert row["commit_at"] == 1790812800  # 2026-10-01T00:00:00Z
    # 父提交就是上次看到的那条 → 中间确实没有别的提交，这个 0 是算得出来的。
    assert row["intervening"] == 0
    assert row["coverage_gap"] == ""
    assert "动过 2 个模块" in row["summary"]
    assert "信息流编排 1 个文件（+3 / −1）" in row["summary"]
    assert "排序模型（Phoenix） 2 个文件" in row["summary"]
    assert "这次没有动 关注流召回、候选流水线。" in row["summary"]
    assert [item["module"] for item in row["modules_json"]] == ["home-mixer", "phoenix"]
    assert row["files_listed"] == 3 and row["files_total"] == 3
    assert row["files_truncated"] == 0
    assert row["files_json"][0] == {"path": "home-mixer/feed.rs", "status": "",
                                    "added": 3, "removed": 1}
    # 事件一写下来，就带一份「待判断」等着人。
    one = algorithm_watch.proposal(store, row["id"])
    assert one["proposal_status"] == "proposed"
    assert one["notify_status"] == "pending"
    assert one["analysis"] == "" and one["engine"] == ""


def test_paths_outside_the_four_modules_are_kept_but_not_confused_with_them(tmp_path):
    """文档和脚本的改动要记下来，但不能冒充「排序模型动过」。"""
    store = store_for(tmp_path)
    first(store)
    row = changed(store, ["home-mixer/feed.rs", "docs/README.md", "scripts/build.sh"])
    assert [item["module"] for item in row["modules_json"]] == ["home-mixer", "other"]
    assert row["modules_json"][1]["label"] == "四个模块之外"
    assert "四个模块之外 2 个文件" in row["summary"]


def test_a_partial_listing_says_so_instead_of_claiming_omission(tmp_path):
    """清单不全时不说「没动 X」——那半句只有清单完整时才成立。"""
    store = store_for(tmp_path)
    first(store)
    new = "b" * 40
    algorithm_watch.check(
        store, fetch=Fake(listing(new, parent="a" * 40),
                          {new: detail(["phoenix/x.rs"], total=9)}), now=300)
    row = algorithm_watch.events(store)[0]
    assert row["files_listed"] == 1 and row["files_total"] == 9
    assert row["files_truncated"] == 1
    assert "这次没有动" not in row["summary"]
    assert "清单不全" in row["summary"]
    assert "只看到前 1 个" in row["coverage_gap"]


def test_not_seeing_how_many_commits_is_not_zero(tmp_path):
    """中间隔着别的提交时写 NULL，不写 0——0 的意思不一样。"""
    store = store_for(tmp_path)
    first(store)
    row = changed(store, ["phoenix/x.rs"], sha="c" * 40, parent="b" * 40)
    assert row["intervening"] is None
    assert "中间还有别的提交" in row["coverage_gap"]
    assert "aaaaaaa" in row["coverage_gap"] and "bbbbbbb" in row["coverage_gap"]


def test_a_missing_parent_field_is_also_unknown(tmp_path):
    store = store_for(tmp_path)
    first(store)
    row = changed(store, ["phoenix/x.rs"], sha="c" * 40, parent="")
    assert row["intervening"] is None and row["parent_sha"] == ""


def test_a_truncated_listing_keeps_the_total(tmp_path):
    """GitHub 只列前 300 个文件时，既要写总数也要说只列了 300 个。"""
    store = store_for(tmp_path)
    first(store)
    many = [f"home-mixer/f{index}.rs" for index in range(algorithm_watch.FILE_CAP)]
    new = "b" * 40
    algorithm_watch.check(
        store, fetch=Fake(listing(new, parent="a" * 40),
                          {new: detail(many, total=algorithm_watch.FILE_CAP + 17)}),
        now=300)
    row = algorithm_watch.events(store)[0]
    assert row["files_listed"] == algorithm_watch.FILE_CAP
    assert row["files_total"] == algorithm_watch.FILE_CAP + 17
    assert row["files_truncated"] == 1
    # 每个模块只列前几条路径，列不满的部分要说出来。
    assert row["modules_json"][0]["files"] == algorithm_watch.FILE_CAP
    assert row["modules_json"][0]["paths_truncated"] is True
    assert len(row["modules_json"][0]["paths"]) == algorithm_watch.PATHS_PER_MODULE


def test_a_listing_without_a_total_is_not_a_complete_listing(tmp_path):
    """没有总数就没法说「没动的那些模块」，所以那半句不写。"""
    store = store_for(tmp_path)
    first(store)
    new = "b" * 40
    algorithm_watch.check(
        store, fetch=Fake(listing(new, parent="a" * 40),
                          {new: (200, {}, body(["phoenix/x.rs"]))}), now=300)
    row = algorithm_watch.events(store)[0]
    assert row["files_total"] is None and row["files_listed"] == 1
    assert "这次没有动" not in row["summary"]
    assert "可能不止这些" in row["coverage_gap"]


def test_a_commit_still_lands_when_its_file_list_cannot_be_read(tmp_path):
    """清单拿不到，改动本身还是要记下来——只是写明这一段没看到。"""
    store = store_for(tmp_path)
    first(store)
    new = "b" * 40
    algorithm_watch.check(
        store, fetch=Fake(listing(new, parent="a" * 40), {new: (500, {}, None)}), now=300)
    row = algorithm_watch.events(store)[0]
    assert row["files_json"] == [] and row["files_total"] is None
    assert row["summary"] == "这次提交的文件清单是空的，所以改了什么看不到。"
    assert "文件清单没拿到" in row["coverage_gap"]
    assert algorithm_watch.upstream(store)["last_sha"] == new


# --- 通知层 -----------------------------------------------------------------

class FakeTelegram:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def send(self, text):
        if self.fail:
            raise OSError("telegram down")
        self.sent.append(text)


def telegram_ready(store):
    store.set("telegram_enabled", True)
    store.set("owner_id", "1")


def test_a_notice_is_claimed_before_it_is_sent(tmp_path):
    """抢到键才发；同一个键第二次进来直接说「发过了」。"""
    store = store_for(tmp_path)
    telegram_ready(store)
    sender = FakeTelegram()
    notifier = Notifier(store, telegram=sender)

    async def run():
        first = await notifier.send("algorithm:x:1", "上游动了")
        again = await notifier.send("algorithm:x:1", "上游动了")
        return first, again

    first, again = asyncio.run(run())
    assert first["telegram"] == "sent" and first["status"] == "sent"
    assert len(sender.sent) == 1 and "上游动了" in sender.sent[0]
    assert again["status"] == "already" and len(sender.sent) == 1


def test_a_claim_that_failed_is_still_recorded(tmp_path):
    """抢到了但没送出去，这件事留在账上——不重发，但也不假装送过了。"""
    store = store_for(tmp_path)
    telegram_ready(store)
    notifier = Notifier(store, telegram=FakeTelegram(fail=True))

    async def run():
        failed = await notifier.send("algorithm:x:2", "上游动了")
        again = await notifier.send("algorithm:x:2", "上游动了")
        return failed, again

    failed, again = asyncio.run(run())
    assert failed["telegram"] == "failed:OSError"
    rows = store.rows("SELECT * FROM notices WHERE draft_id=?", ("algorithm:x:2",))
    assert rows and rows[0]["status"] != "attempted"
    assert again["status"] == "already"


def test_no_telegram_is_a_different_sentence_from_a_failed_one(tmp_path, monkeypatch):
    """「没配 Telegram」和「发了但失败」不是同一句话。"""
    store = store_for(tmp_path)
    async def skipped(*_):
        return "skipped"
    monkeypatch.setattr(Notifier, "desktop", skipped)
    result = asyncio.run(Notifier(store).send("algorithm:x:3", "上游动了"))
    assert result["telegram"] == "not_configured"
    assert result["desktop"] == "skipped"
    assert result["status"] == "not_configured"
    async def failed(*_):
        return "failed"
    monkeypatch.setattr(Notifier, "desktop", failed)
    result = asyncio.run(Notifier(store).send("algorithm:x:4", "上游动了"))
    assert result["telegram"] == "not_configured"
    assert result["desktop"] == "failed"
    assert result["status"] == "failed"


def test_the_desktop_notification_never_interpolates_the_text():
    """上游的提交说明是不可信输入，不能拼进 AppleScript 源码里。"""
    from studio.notify import SCRIPT
    assert "item 1 of argv" in SCRIPT and "item 2 of argv" in SCRIPT
    assert "$" not in SCRIPT and "&" not in SCRIPT and "}" not in SCRIPT


# --- 人给的那一半 -----------------------------------------------------------

def test_a_judgement_does_not_touch_the_measured_half(tmp_path):
    store = store_for(tmp_path)
    first(store)
    row = changed(store, ["phoenix/x.rs"])
    assert algorithm_watch.judge(store, row["sha"], status="watching",
                                 impact="先看着", now=400)
    after = algorithm_watch.events(store)[0]
    assert after["sha"] == row["sha"] and after["summary"] == row["summary"]
    assert after["intervening"] == 0 and after["files_total"] == 1
    one = algorithm_watch.proposal(store, row["id"])
    assert one["proposal_status"] == "watching" and one["impact"] == "先看着"
    assert one["impact_at"] == 400
    # 没记下来的提交就是没记下来，不是「记了一条空的」。
    assert algorithm_watch.judge(store, "f" * 40, status="watching", impact="") == ""


def test_an_engine_that_answered_nothing_is_not_the_same_as_never_asked(tmp_path):
    """引擎那一栏留空是「还没让引擎分析」，写了名字才是「它答了」。"""
    store = store_for(tmp_path)
    first(store)
    row = changed(store, ["phoenix/x.rs"])
    algorithm_watch.attach_analysis(store, row["id"], "", "", "引擎返回 invalid_output")
    one = algorithm_watch.proposal(store, row["id"])
    assert one["analysis"] == "" and one["engine"] == ""
    assert one["engine_error"] == "引擎返回 invalid_output"
    assert algorithm_watch.events(store)[0]["has_analysis"] == 0
    algorithm_watch.attach_analysis(store, row["id"], "影响不大", "hermes", "")
    one = algorithm_watch.proposal(store, row["id"])
    assert one["analysis"] == "影响不大" and one["engine"] == "hermes"
    assert one["engine_error"] == ""
    assert algorithm_watch.events(store)[0]["has_analysis"] == 1


def test_the_engine_is_given_facts_and_no_advice(tmp_path):
    store = store_for(tmp_path)
    first(store)
    row = changed(store, ["phoenix/x.rs"])
    text = algorithm_watch.proposal_text(row, row["files_json"])
    assert row["sha"][:7] in text and row["message"] in text
    assert row["summary"] in text
    assert "phoenix/x.rs" in text and "+3 / −1" in text


def test_the_engine_text_carries_the_gap_when_there_is_one(tmp_path):
    store = store_for(tmp_path)
    first(store)
    new = "b" * 40
    algorithm_watch.check(
        store, fetch=Fake(listing(new, parent="a" * 40),
                          {new: detail(["phoenix/x.rs"], total=9)}), now=300)
    row = algorithm_watch.events(store)[0]
    assert row["coverage_gap"] in algorithm_watch.proposal_text(row, row["files_json"])


def test_notified_is_written_down_and_only_the_waiting_ones_are_picked(tmp_path):
    store = store_for(tmp_path)
    first(store)
    row = changed(store, ["phoenix/x.rs"])
    assert [one["id"] for one in algorithm_watch.pending_notify(store)] == [row["id"]]
    algorithm_watch.mark_notified(store, row["id"], "sent", "Telegram：sent；桌面：sent",
                                  now=400)
    assert algorithm_watch.pending_notify(store) == []
    one = algorithm_watch.proposal(store, row["id"])
    assert one["notify_status"] == "sent" and one["notified_at"] == 400
    assert one["notify_note"] == "Telegram：sent；桌面：sent"


# --- 调度 -------------------------------------------------------------------

def test_a_transient_failure_waits_an_hour_instead_of_a_day(tmp_path):
    store = store_for(tmp_path)
    store.set("paused", False)
    algorithm_watch.check(store, fetch=lambda url, h: (429, {}, None), now=1000)
    jobs = Jobs(store)
    jobs.schedule_algorithm(1000 + 3599)
    assert store.rows("SELECT id FROM tasks WHERE kind='algorithm_check'") == []
    jobs.schedule_algorithm(1000 + 3600)
    queued = store.rows("SELECT * FROM tasks WHERE kind='algorithm_check'")
    assert len(queued) == 1 and queued[0]["dedupe"] == "algorithm-check"


def test_a_successful_check_waits_a_full_day_and_is_not_queued_twice(tmp_path):
    store = store_for(tmp_path)
    store.set("paused", False)
    first(store, when=1000)
    jobs = Jobs(store)
    jobs.schedule_algorithm(1000 + 3600)
    assert store.rows("SELECT id FROM tasks WHERE kind='algorithm_check'") == []
    jobs.schedule_algorithm(1000 + algorithm_watch.CHECK_INTERVAL)
    assert len(store.rows("SELECT id FROM tasks WHERE kind='algorithm_check'")) == 1
    # 已经在队列里的时候再点一次，不会再排一条。
    jobs.schedule_algorithm(1000 + algorithm_watch.CHECK_INTERVAL * 2)
    assert len(store.rows("SELECT id FROM tasks WHERE kind='algorithm_check'")) == 1


def test_the_automatic_check_is_off_when_the_switch_is_off(tmp_path):
    store = store_for(tmp_path)
    store.set("paused", False)
    store.set("algorithm_watch", False)
    jobs = Jobs(store)
    jobs.last_algorithm_schedule = 0
    jobs.schedule()
    assert store.rows("SELECT id FROM tasks WHERE kind='algorithm_check'") == []


def test_the_automatic_check_does_not_ride_on_the_browser_being_online(tmp_path):
    """上游动没动和本机插件装没装是两件事，所以它不搭 `schedule_measurements` 的车。"""
    store = store_for(tmp_path)
    store.set("paused", False)
    jobs = Jobs(store)
    jobs.last_algorithm_schedule = 0
    jobs.schedule()
    assert len(store.rows("SELECT id FROM tasks WHERE kind='algorithm_check'")) == 1


def test_a_manual_check_survives_the_pause_switch(tmp_path):
    """暂停停的是往外发的动作；读一个公开仓库不在其中。"""
    store = store_for(tmp_path)
    assert Jobs(store).enqueue("algorithm_check", {}, dedupe="algorithm-check")


# --- 路由 -------------------------------------------------------------------

def authed(app):
    """A session row plus the cookie and CSRF header the middleware expects."""
    store = app.state.store
    token = "algorithm-session"
    with store.db(True) as db:
        db.execute("INSERT INTO sessions(token_hash,csrf,expires) VALUES(?,?,?)",
                   (digest(token), "csrf", int(time.time()) + 600))
    return {"cookie": f"studio_session={token}", "x-csrf-token": "csrf"}


def test_the_listing_answers_before_anything_was_ever_checked(tmp_path):
    """还没看过上游时也要答得上来，而且不能答成「上游没有变化」。"""
    app = create_app(tmp_path, "http://testserver")
    headers = authed(app)
    with TestClient(app) as client:
        response = client.get("/api/v1/algorithm/events", headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["repo"] == REPO and payload["watching"] is True
    assert payload["state"]["last_checked"] is None
    assert payload["state"]["last_status"] == ""
    assert payload["state"]["last_sha"] == ""
    assert payload["events"] == []
    assert payload["status_label"]["adopted"] == "跟着改"
    assert payload["thresholds"]["check_interval"] == algorithm_watch.CHECK_INTERVAL
    # 「查到了」和「这一次没查成」两类都必须能分开，缺一类面板就会撒谎。
    labels = {one["status"] for one in payload["check_labels"]}
    assert set(algorithm_watch.CHECKED) <= labels
    assert {"rate_limited", "network_error", "not_found"} <= labels


def test_the_judgement_route_refuses_what_it_does_not_know(tmp_path):
    app = create_app(tmp_path, "http://testserver")
    store = app.state.store
    store.set("paused", False)
    first(store)
    changed(store, ["phoenix/x.rs"])
    headers = authed(app)
    with TestClient(app) as client:
        assert client.post(f"/api/v1/algorithm/impact/{'b' * 40}",
                           json={"status": "maybe", "impact": ""},
                           headers=headers).status_code == 422
        assert client.post("/api/v1/algorithm/impact/" + "f" * 40,
                           json={"status": "watching", "impact": ""},
                           headers=headers).status_code == 404
        assert client.post("/api/v1/algorithm/impact/zzz",
                           json={"status": "watching", "impact": ""},
                           headers=headers).status_code == 422
        good = client.post(f"/api/v1/algorithm/impact/{'b' * 40}",
                           json={"status": "Adopted", "impact": "跟着改"},
                           headers=headers)
        assert good.status_code == 200 and good.json()["status"] == "adopted"
    assert algorithm_watch.events(store)[0]["proposal_status"] == "adopted"


def test_a_manual_check_queues_once_and_can_ask_for_the_engine(tmp_path):
    app = create_app(tmp_path, "http://testserver")
    store = app.state.store
    headers = authed(app)
    with TestClient(app) as client:
        once = client.post("/api/v1/algorithm/check", json={"analyze": False},
                           headers=headers)
        twice = client.post("/api/v1/algorithm/check", json={"analyze": False},
                            headers=headers)
        assert once.status_code == 200
        assert once.json()["task_id"] == twice.json()["task_id"]
        assert once.json()["analyze"] is False
        rows = store.rows("SELECT * FROM tasks WHERE kind='algorithm_check'")
    assert len(rows) == 1
    # 手动这一趟和自动那趟用同一个去重键，所以不会插到自动检查前面。
    assert rows[0]["dedupe"] == "algorithm-check"
    assert json.loads(rows[0]["payload"]) == {}


def test_one_event_can_be_fetched_without_the_list(tmp_path):
    app = create_app(tmp_path, "http://testserver")
    store = app.state.store
    headers = authed(app)
    first(store)
    changed(store, ["phoenix/x.rs"])
    event_id = algorithm_watch.events(store)[0]["id"]
    with TestClient(app) as client:
        one = client.get(f"/api/v1/algorithm/proposals/{event_id}", headers=headers)
        assert one.status_code == 200
        assert one.json()["event"]["sha"] == "b" * 40
        assert one.json()["event"]["analysis"] == ""
        assert one.json()["event"]["files_json"][0]["path"] == "phoenix/x.rs"
        assert client.get("/api/v1/algorithm/proposals/" + "0" * 32,
                          headers=headers).status_code == 404
        assert client.get("/api/v1/algorithm/proposals/not-an-id",
                          headers=headers).status_code == 422


def test_watching_can_be_turned_off_from_the_panel(tmp_path):
    app = create_app(tmp_path, "http://testserver")
    store = app.state.store
    headers = authed(app)
    with TestClient(app) as client:
        off = client.post("/api/v1/algorithm/watching/off", headers=headers)
        assert off.status_code == 200 and off.json()["watching"] is False
        assert algorithm_watch.watching(store) is False
        assert client.post("/api/v1/algorithm/watching/maybe",
                           headers=headers).status_code == 422
        on = client.post("/api/v1/algorithm/watching/on", headers=headers)
        assert on.json()["watching"] is True
    assert store.get("algorithm_watch") is True


# --- 白名单 -----------------------------------------------------------------

def test_the_studio_allowlist_takes_every_algorithm_route_and_nothing_wider():
    """连接助手只认路径参数：这里每一条都不能靠 `?sha=` 冒充。"""
    assert desktop_route("/api/v1/algorithm/events", "GET")
    assert desktop_route("/api/v1/algorithm/proposals/" + "a" * 32, "GET")
    assert desktop_route("/api/v1/algorithm/check", "POST")
    assert desktop_route("/api/v1/algorithm/impact/" + "a" * 40, "POST")
    assert desktop_route("/api/v1/algorithm/impact/" + "a" * 7, "POST")
    assert desktop_route("/api/v1/algorithm/watching/on", "POST")
    assert desktop_route("/api/v1/algorithm/watching/off", "POST")
    # 形状不许互相冒充。
    assert not desktop_route("/api/v1/algorithm/events", "POST")
    assert not desktop_route("/api/v1/algorithm/check", "GET")
    assert not desktop_route("/api/v1/algorithm/impact/" + "a" * 40, "GET")
    assert not desktop_route("/api/v1/algorithm/watching/on", "GET")
    assert not desktop_route("/api/v1/algorithm/watching/maybe", "POST")
    assert not desktop_route("/api/v1/algorithm/proposals/not-an-id", "GET")
    assert not desktop_route("/api/v1/algorithm/impact/zzz", "POST")
    # 路径太窄、太宽都不行。
    assert not desktop_route("/api/v1/algorithm/events/../settings", "GET")
    assert not desktop_route("/api/v1/algorithm", "GET")
    assert not desktop_route("/api/v1/algorithm/", "GET")
