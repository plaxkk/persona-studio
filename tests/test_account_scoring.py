"""06C-2: 三个 0–100 分怎么来的，以及「评不了」和「没触发」为什么不是一回事。

The rule layer is tested directly on hand-written feature rows, because that is
the only way to pin one rule's arithmetic without also pinning ten features and
three reads. Three properties get their own test because they are the point of
the design: 先一票否决再加减分 is an *order*; a rule whose input never arrived
must not read like a rule that ran and found nothing; and a score that was never
computed must not look like a score of 50.
"""
import json
import time

from fastapi.testclient import TestClient

from studio import account_quality, account_scoring
from studio.account_quality import READ_SET, _received_features
from studio.api import create_app
from studio.desktop import desktop_route
from studio.security import digest

# Reused verbatim from 06C-1's suite so the end-to-end run here drives the same
# reads the worker drives. There is no `conftest.py`: `tests/` is flat and pytest
# puts it on `sys.path` before importing this module.
from test_account_quality import (HANDLE, JOINED_AT, app_store, finished, job_of,
                                  observe, queued, stored)

NOW = int(time.time())


def feature(name, value):
    """A feature row shaped the way `account_scoring._known_values` reads them."""
    return {"name": name, "known": 1 if value is not None else 0, "value": value}


def healthy(**values):
    """Feature rows for one account, with anything unmentioned left unread."""
    return [feature(name, value) for name, value in values.items()]


def scored(rows, *, received=32, posts=12, reply=0, profile=1, confidence="ok"):
    counts = {"profile": profile, "post": posts, "received": received, "reply": reply}
    return account_scoring.score(rows, counts=counts, confidence=confidence)


def entry(result, name):
    return result["scores"][name]


def fired(result, name):
    return {rule["id"]: rule for rule in entry(result, name)["rules"] if rule["fired"]}


def unchecked(result, name):
    return {rule["id"]: rule["blocked"] for rule in entry(result, name)["skipped"]}


def engager(handle, stats):
    """One received reply, with whatever the search response said about its author."""
    return {"author": handle, "text": "看不懂", "extra_json": {"author_stats": stats}}


def profile_fields(**over):
    """The author stats a search result carries, with all fields visible."""
    stats = {"followers": 800, "statuses": 400, "bio_length": 40,
             "default_avatar": False, "created_at": JOINED_AT}
    stats.update(over)
    return stats


# --- 分数可以不存在 ---------------------------------------------------------

def test_no_evidence_means_no_score_rather_than_a_midpoint():
    result = scored([], received=0, posts=0, profile=0, confidence="insufficient_data")
    for name in account_scoring.SCORES:
        one = entry(result, name)
        # 0 分和 50 分都是判断，而这里什么都没读过。None 才是「没判断」。
        assert one["value"] is None and one["label"] == ""
        assert one["reason"], "no score has to say why there is no score"
    assert result["confidence"] == "insufficient_data"


def test_each_score_has_its_own_evidence_floor():
    # 只有原帖时，专业性能评（主题一致性在原帖上算得出来），另外两个不行：
    # 真实性与盈利能力说的都是别人怎么跟它互动。
    result = scored(healthy(text_cohesion=0.9), received=0)
    assert entry(result, "authenticity")["value"] is None
    assert entry(result, "monetization")["value"] is None
    assert entry(result, "professionalism")["value"] == 65  # 50 + 15
    assert "别人怎么跟它互动" in entry(result, "authenticity")["reason"]


def test_a_check_that_ran_and_found_nothing_is_the_midpoint():
    result = scored(healthy(reply_cliche_rate=0.0, reply_intent_density=0.0))
    assert entry(result, "authenticity")["value"] == 50
    assert entry(result, "authenticity")["label"] == "auth_mid"
    # 意向为零是**评过了**的结果，所以这里封顶；而空壳占比没读到，是没评。
    assert entry(result, "monetization")["value"] == 30
    assert "mon_cap_no_intent" in fired(result, "monetization")
    assert "auth_cap_shell_engagers" not in fired(result, "authenticity")
    assert "auth_cap_shell_engagers" in unchecked(result, "authenticity")


# --- 先一票否决，再加减分 ---------------------------------------------------

def test_the_veto_comes_first_and_later_rules_still_move_the_number():
    # 方案 §五 的伪代码是顺序执行的：先 min(auth, 35)，再 -= / +=。所以被一票否决
    # 的账号，后面的加分作用在 35 上得到 50；先加后封顶才会停在 35。这一条钉的是
    # 顺序，不是数值。
    rows = healthy(reply_cliche_rate=0.9, account_age_days=4000,
                   text_cohesion=0.9, distinct_engagers=25)
    result = scored(rows)
    assert fired(result, "authenticity")["auth_cap_template_replies"]["amount"] == 35
    assert entry(result, "authenticity")["value"] == 50

    # 而只有封顶、没有后面的加分时，分数就停在封顶线上。
    assert entry(scored(healthy(reply_cliche_rate=0.9)), "authenticity")["value"] == 35


def test_the_shell_cap_fires_on_the_rate_the_feature_layer_computed():
    result = scored(healthy(reply_cliche_rate=0.0, empty_engager_rate=0.6))
    assert fired(result, "authenticity")["auth_cap_shell_engagers"]["amount"] == 35
    assert entry(result, "authenticity")["value"] == 35


def test_a_veto_on_one_score_does_not_touch_the_other_two():
    rows = healthy(reply_cliche_rate=0.9, reply_intent_density=0.0,
                   own_reply_length_median=40, own_reply_question_rate=0.0)
    result = scored(rows, reply=4)
    assert entry(result, "authenticity")["value"] == 35
    assert entry(result, "professionalism")["value"] == 50
    assert entry(result, "monetization")["value"] == 30


def test_two_vetoes_take_the_lower_cap_not_the_sum():
    result = scored(healthy(reply_cliche_rate=0.9, empty_engager_rate=0.5))
    assert entry(result, "authenticity")["value"] == 35
    assert set(fired(result, "authenticity")) == {
        "auth_cap_template_replies", "auth_cap_shell_engagers"}


def test_a_low_engagement_rate_subtracts_from_the_base():
    assert entry(scored(healthy(follower_er_median=0.0)), "authenticity")["value"] == 35


def test_bonuses_add_on_top_of_the_base():
    rich = scored(healthy(reply_intent_density=0.9, link_share=0.9))
    assert entry(rich, "monetization")["value"] == 80  # 50 + 20 + 10


# --- 标签档位 ---------------------------------------------------------------

def test_monetization_has_two_bands_where_the_other_two_have_three():
    assert len(account_scoring.LABELS["monetization"]) == 2
    assert [account_scoring.label_for("monetization", n)
            for n in (0, 39, 40, 69, 70, 100)] == ["weak_monetization"] * 4 + [
        "monetizable"] * 2
    assert [account_scoring.label_for("authenticity", n)
            for n in (0, 39, 40, 69, 70, 100)] == ["auth_low", "auth_low",
                                                  "auth_mid", "auth_mid",
                                                  "auth_high", "auth_high"]


def test_the_bands_only_exist_when_there_is_a_score():
    assert account_scoring.label_for("authenticity", None) == ""


# --- 评不了的规则要留痕 -----------------------------------------------------

def test_a_rule_that_could_not_be_checked_is_not_recorded_as_not_triggered():
    result = scored(healthy())
    assert fired(result, "authenticity") == {}
    skipped = unchecked(result, "authenticity")
    # 方案 §五 举的突发分数这条通道根本拿不到，所以它是静态的「评不了」。
    assert "auth_burst_and_relevance" in skipped
    # 而模板回复占比只是这一次没读到——两种情况要留下两种不同的句子。
    assert "reply_cliche_rate" in skipped["auth_cap_template_replies"]
    assert "没触发" not in skipped["auth_cap_template_replies"]


def test_every_rule_the_plan_asks_for_that_we_cannot_judge_is_accounted_for():
    for want in ("auth_burst_and_relevance", "pro_information_density",
                 "mon_verified_share"):
        assert any(rule["id"] == want for rule in account_scoring.BLOCKED)
    for rule in account_scoring.BLOCKED:
        assert rule["score"] in account_scoring.SCORES
        # 只说「没做」不够，还要说清为什么这一条通道做不了。
        assert len(rule["blocked"]) > 20


def test_every_rule_carries_the_number_it_used_and_where_it_came_from():
    plan = {rule["id"]: rule for rule in account_scoring.rules()}
    assert set(plan) == {
        "auth_cap_template_replies", "auth_cap_shell_engagers",
        "auth_penalty_low_engagement", "auth_bonus_organic",
        "pro_cap_one_liner_replies", "pro_bonus_dialogue", "pro_bonus_cohesion",
        "mon_bonus_intent", "mon_cap_no_intent", "mon_bonus_links"}
    for rule in plan.values():
        assert rule["kind"] in ("cap", "delta") and rule["needs"]
        assert rule["because"]
        # 每一分都由自己这一条规则加减，不是别的分的结果。
        assert rule["score"] in account_scoring.SCORES
    # 方案给的两个数要在句子和常量里对得上，否则面板显示的会和算出来的不一样。
    assert "55%" in plan["auth_cap_template_replies"]["because"]
    assert "35%" in plan["auth_cap_shell_engagers"]["because"]
    assert plan["auth_cap_template_replies"]["because"].endswith("（方案 §五）")
    assert "本机定" in plan["mon_bonus_links"]["because"]


def test_the_thresholds_shown_to_the_panel_are_the_ones_the_rules_use():
    shown = scored(healthy())["thresholds"]
    assert shown["cliche_cap_rate"] == account_scoring.CLICHE_CAP_RATE == 0.55
    assert shown["shell_cap_rate"] == account_scoring.SHELL_CAP_RATE == 0.35
    assert shown["cap_score"] == account_scoring.CAP_SCORE == 35
    assert shown["base"] == account_scoring.BASE == 50
    assert set(shown) == set(account_scoring.thresholds())


# --- 信心与版本 -------------------------------------------------------------

def test_low_confidence_rides_along_with_the_scores_instead_of_erasing_them():
    result = scored(healthy(reply_cliche_rate=0.2), received=4,
                    confidence="low_confidence")
    assert result["confidence"] == "low_confidence"
    assert entry(result, "authenticity")["value"] == 50
    # 样本不满 30 正是方案点名要标 low_confidence 的情形，而分数照给。
    assert account_quality.confidence({"profile": 1, "post": 12, "received": 4},
                                      {}) == "low_confidence"


def test_the_model_layer_is_declared_absent_rather_than_faked():
    result = scored(healthy())
    assert result["rule_version"] == account_scoring.RULE_VERSION == "06C-2.1"
    assert result["model_version"] == "" and result["model_note"]


# --- 采集层缺的那个特征 -----------------------------------------------------

def test_shell_needs_the_two_weak_signals_to_agree():
    # 方案 §C 列了四个信号，但它们的强度不一样：头像或简介缺，一条就够；发帖极少
    # 与注册很新要**同时**成立——只有一条时它更像新用户而不是壳。所以五个人里
    # 三个是壳，而不是四个。
    given = [
        profile_fields(default_avatar=True),                      # 头像缺 → 壳
        profile_fields(bio_length=0),                             # 简介空 → 壳
        profile_fields(statuses=2, created_at=NOW - 20 * 86400),  # 又少又新 → 壳
        profile_fields(statuses=2),                               # 只少 → 不是
        profile_fields(created_at=NOW - 20 * 86400),              # 只新 → 不是
    ]
    rows = {row["name"]: row for row in
            _received_features([engager(f"e{n}", stats)
                                for n, stats in enumerate(given)], {})}
    assert rows["empty_engager_rate"]["value"] == 0.6
    assert rows["empty_engager_rate"]["sample_size"] == 5


def test_an_engager_population_without_profile_fields_is_not_a_zero():
    # 只看到粉丝数、看不到头像和简介：一条壳的证据都没有。这时候写 0 会被读成
    # 「这批互动者一个空壳都没有」，而事实是「数不出谁是空壳」。
    thin = [engager(f"e{n}", {"followers": 10 * (n + 1)}) for n in range(3)]
    rows = {row["name"]: row for row in _received_features(thin, {})}
    assert rows["empty_engager_rate"]["value"] is None
    assert rows["empty_engager_rate"]["sample_size"] is None
    assert "数不出谁是空壳" in rows["empty_engager_rate"]["note"]
    # 而这一批人的粉丝数读到了，所以「账号字段可见率」不是 None：两个 unknown
    # 的原因不同，这也是它们分成两行的理由。
    assert rows["engager_stats_known_share"]["value"] == 1.0


# --- 接线 -------------------------------------------------------------------

def test_a_quality_run_stores_its_scores_beside_its_features(tmp_path):
    store = app_store(tmp_path)
    queued(store)
    job = job_of(store, "a" * 32)
    summary = observe(store, job, *finished())
    ident = summary["feature_id"]
    assert summary["score"]["rule_version"] == account_scoring.RULE_VERSION

    row = stored(store, "account_scores", feature_id=ident)[0]
    assert row["handle"] == HANDLE and row["confidence"] == summary["confidence"]
    assert row["model_version"] == "" and row["created"] == row["updated"]
    # 这一批读到了 12 帖 + 32 回复，三个分都该有值，并且按 40/70 分档。
    assert row["authenticity"] is not None
    assert row["professionalism"] is not None
    assert row["monetization"] is not None
    ledger = json.loads(row["labels_json"])
    assert set(ledger["scores"]) == set(account_scoring.SCORES)
    assert ledger["thresholds"]["cliche_cap_rate"] == account_scoring.CLICHE_CAP_RATE

    # 分数是这次特征运行的一部分，跟着 run 一起回去。
    run = account_quality.latest(store, HANDLE)
    assert run["id"] == ident
    assert run["score"]["feature_id"] == ident
    assert run["score"]["labels_json"]["scores"]["authenticity"]["value"] == \
        row["authenticity"]


def test_the_scores_are_only_written_with_the_features_they_came_from(tmp_path):
    store = app_store(tmp_path)
    queued(store)
    job = job_of(store, "a" * 32)
    observe(store, job, *finished())
    # 一次运行一行，不是每次读一行。
    assert len(stored(store, "account_scores")) == 1
    assert len(stored(store, "account_features")) == 1


def score_client(tmp_path):
    app = create_app(tmp_path, "http://testserver")
    store = app.state.store
    store.set("paused", False)
    token = "scoring-session"
    with store.db(True) as db:
        db.execute("INSERT INTO sessions(token_hash,csrf,expires) VALUES(?,?,?)",
                   (digest(token), "csrf", int(time.time()) + 600))
    headers = {"cookie": f"studio_session={token}", "x-csrf-token": "csrf"}
    return app, store, headers


def test_the_route_hands_the_panel_the_score_and_its_ledger(tmp_path):
    app, store, headers = score_client(tmp_path)
    queued(store)
    job = job_of(store, "a" * 32)
    observe(store, job, *finished())
    with TestClient(app) as client:
        body = client.get(f"/api/v1/accounts/quality/{HANDLE}", headers=headers).json()
    assert body["score"]["rule_version"] == account_scoring.RULE_VERSION
    one = body["score"]["labels_json"]["scores"]["professionalism"]
    assert one["base"] == account_scoring.BASE
    assert one["rules"] and one["skipped"]
    # 面板要能解释「为什么这个分是这个分」，所以每条跑过的规则都得带原话。
    assert all(rule["because"] for rule in one["rules"])
    assert all(rule["blocked"] for rule in one["skipped"])
    # 这一片不带新的读取种类，所以路径与连接助手的要求都没变。
    assert desktop_route(f"/api/v1/accounts/quality/{HANDLE}", "GET")


def test_the_route_keeps_no_score_and_no_run_apart(tmp_path):
    app, store, headers = score_client(tmp_path)
    with TestClient(app) as client:
        body = client.get(f"/api/v1/accounts/quality/{HANDLE}", headers=headers).json()
    assert body["run"] is None and body["score"] is None
    assert body["samples"] == {kind: 0 for kind in READ_SET}
