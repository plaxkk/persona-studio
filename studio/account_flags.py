"""账号风险硬标签与观察室（06C-3）。

《账号质量打标程序化方案》§三 第 3 步把打标分成两句：「先用规则打硬标签，再用模型打
连续分」。06C-2 做了后半句（三个 0–100 分），本模块做前半句：**风险硬标签**。

「硬标签」在方案里有一个明确的意思：**它绕过分数门槛**。一个账号的真实性分可能是
NULL（一条回复都没读到），但只要空壳互动者的证据在场且样本够，`risk_bot` 照样成
立——标签说的是「这件事本身被看见了」，不是「综合起来像不像」。所以这里一个分都不
读，只读 06C-1 的特征行。

方案里有**两套名字**，别混：§三 第 4 步给的是四个账号级标签（`risk_bot` /
`risk_pod` / `risk_shill` / `risk_unverified_guru`），§二 给的是**规则**
（`inauthentic_engagement`、`burst_farm`、`unverified_pnl_claim`…）。本模块的 `code`
用前者，`rules()` 的 `id` 用后者那一套的语义。一条规则命中之后落到哪个标签上，是本
模块的判断，写在每条规则的 `code` 里；面板会把「哪条规则命中了」和「落成哪个标签」
两样都显示出来，免得读者以为它们是同一件事。

**每条线都要说清是谁定的。** 方案原文只给了两个数：空壳互动者 > 40%（§二）、模板
回复 > 60%（§二 的 `inauthentic_engagement`）。其余阈值方案没写，由本机定，并且集中
在下面一组命名常量里；每条规则的 `because` 句尾都带着「（方案 §…）」或「（线由本机
定）」，面板照抄给读者看。

**评不了的，写成 `BLOCKED` 而不是删掉。** 方案点名要的规则里，有三条这条通道真的做
不了（突发要互动时间、共现要多账号、晒单与复盘之分要语义）。留着它们是为了让「查过
了、没有」和「查不了」在面板上不是同一件事——06C-2 已经因为这原因被钉过一条测试，
这里照做。

**宁少勿滥。** 方案 §六 说的评估方式是「每周抽 50 个账号人工复核，看**精确率**，不要
只看分数分布好看」。所以每条规则都要求两个信号同时成立，并且要求回复样本够
（`MIN_REPLIES`）——2 条回复里 2 条是空壳不是「100% 空壳」，是「什么都没有」。
观察室就是那张人工复核表：`account_watchlist` 收下所有命中过的账号，人给结论，
`precision = confirmed / (confirmed + dismissed)`；一次都没复核过时它是 **NULL**
（面板渲染「暂无数据」），不是 0。

模型层（§三 第 3 步的后半句）在这里**同样没有做**：`MODEL_VERSION` 是空字符串。
`risk_unverified_guru` 就是被它挡住的那一个。
"""

from __future__ import annotations

import json
import re
import time

# 和 `account_quality.clean_handle` 同一套格式。本模块不能反过来 import 采集层
# （采集层 import 本模块），所以这条正则在这里重复一遍——两处必须同时改。
HANDLE = re.compile(r"[A-Za-z0-9_]{1,15}")

RULE_VERSION = "06C-3.1"
# 方案 §三 第 3 步：「先用规则打硬标签，再用模型打连续分」。这里只有前半句。
MODEL_VERSION = ""
MODEL_NOTE = "模型层未建（方案 §三：先用规则，再用模型）；需要语义判断的标签因此没有打"

# 方案 §三 第 4 步的四个账号级风险硬标签，顺序就是面板上的顺序。
CODES = ("risk_bot", "risk_pod", "risk_shill", "risk_unverified_guru")
CODE_LABEL = {"risk_bot": "疑似机器或刷量互动",
              "risk_pod": "疑似互赞互关的小圈子",
              "risk_shill": "疑似水军带货",
              "risk_unverified_guru": "疑似没验证过的「大神」"}

LAYER_RECEIVED = "C 互动文本与互动者质量"

# --- 方案原文给出的线 -------------------------------------------------------
SHELL_FLAG_RATE = 0.40    # §二：空壳互动者 > 40%
CLICHE_FLAG_RATE = 0.60   # §二 的 inauthentic_engagement：模板回复 > 60%

# --- 方案没给、由本机定的线（面板会把这一组照抄给读者看）--------------------
MIN_REPLIES = 10          # 下面的比例都要先有这么多条回复才算数
REPLY_LIKE_FLOOR = 0.01   # §二 说「reply_like_ratio 极低」，极低是多低方案没写
POD_REPEAT_RATE = 0.5
POD_FOLLOWERS_FLOOR = 200
SHILL_LINK_SHARE = 0.5
REVIEW_CAP = 50           # §六：每周抽 50 个账号人工复核

STATUSES = ("watching", "confirmed", "dismissed")
STATUS_LABEL = {"watching": "待复核", "confirmed": "确实有问题",
                "dismissed": "误报"}


class AccountFlagsError(ValueError):
    pass


def _rule(ident, code, layer, needs, test, because, source,
          min_replies=MIN_REPLIES) -> dict:
    return {"id": ident, "code": code, "layer": layer, "needs": tuple(needs),
            "test": test, "because": because, "source": source,
            "min_replies": min_replies}


def rules() -> list[dict]:
    """Every risk flag this channel can actually evaluate."""
    return [
        _rule("flag_shell_audience", "risk_bot", LAYER_RECEIVED,
              ("empty_engager_rate",),
              lambda v: v["empty_engager_rate"] > SHELL_FLAG_RATE,
              f"空壳互动者占比 > {SHELL_FLAG_RATE:.0%}：来互动的人里近一半没有头像、"
              "简介是空的，或者刚注册就到处回帖（方案 §二 给的数）",
              "方案"),
        _rule("flag_inauthentic_engagement", "risk_bot", LAYER_RECEIVED,
              ("reply_cliche_rate", "reply_like_ratio_median"),
              lambda v: (v["reply_cliche_rate"] > CLICHE_FLAG_RATE
                         and v["reply_like_ratio_median"] < REPLY_LIKE_FLOOR),
              f"模板回复占比 > {CLICHE_FLAG_RATE:.0%}（方案 §二 的 "
              "inauthentic_engagement）**且**回复/点赞比 < "
              f"{REPLY_LIKE_FLOOR}：回复量堆起来了，点赞没有跟着来，说明这些回复不是"
              "冲着内容来的。缺一不算；「极低」的线由本机定",
              "方案"),
        _rule("flag_reciprocal_engagers", "risk_pod", LAYER_RECEIVED,
              ("engager_repeat_rate", "engager_median_followers"),
              lambda v: (v["engager_repeat_rate"] >= POD_REPEAT_RATE
                         and v["engager_median_followers"] < POD_FOLLOWERS_FLOOR),
              f"≥ {POD_REPEAT_RATE:.0%} 的回复来自已经回复过的人，且互动者的粉丝中位数"
              f" < {POD_FOLLOWERS_FLOOR} 人：互动集中在一小圈账号之间。这是**弱代理**"
              "——真正的判据是互动者共现率，单账号一次读取拿不到（见下面没评的那条），"
              "所以它会被小圈子里的真号误伤；两条线都由本机定",
              "本机"),
        _rule("flag_sold_commerce", "risk_shill", LAYER_RECEIVED,
              ("link_share", "reply_cliche_rate"),
              lambda v: (v["link_share"] >= SHILL_LINK_SHARE
                         and v["reply_cliche_rate"] > CLICHE_FLAG_RATE),
              f"带链接帖占比 ≥ {SHILL_LINK_SHARE:.0%} **且**模板回复 > "
              f"{CLICHE_FLAG_RATE:.0%}：带货动作是真的，但围着它的互动是模板化的。方案"
              " §四 把「高意向 + 高链接」列为一个**正常类别**（强带货号），所以只看带"
              "链接不算风险，加上互动模板化才落到水军；带链接的线由本机定",
              "本机"),
    ]


# 方案写着、但这一条通道评不了的规则。收集在这里而不是删掉，理由和 06C-2 的 `BLOCKED`
# 一样：**「查过了、没有」不是「查不了」**，两者在面板上必须是两句话。
BLOCKED = [
    {"id": "flag_burst_farm", "code": "risk_bot",
     "blocked": "方案 §二 要「发帖后 3 分钟内集中完成大部分点赞」；第三方看不到单条推文"
                "的互动时间分布，这条通道根本拿不到（06C-1 已把它记成不可测特征）"},
    {"id": "flag_pod_co_engagement", "code": "risk_pod",
     "blocked": "互赞互关的主判据是互动者共现率——同一批人反复出现在一批账号下面；这要"
                "看多个账号的互动者集合，单个账号的一次读取给不出（06C-1 已把它记成不可"
                "测），上面那条规则只是弱代理"},
    {"id": "flag_unverified_pnl_claim", "code": "risk_unverified_guru",
     "blocked": "方案 §二 要区分「只有晒单截图」与「有时间序列观点和事后复盘」，这是语义"
                "判断，要模型层（§三 第 3 步的后半句），本片没有做"},
    {"id": "flag_like_list_astroturf", "code": "risk_bot",
     "blocked": "方案 §六 自己说的：点赞列表对他人不可见或受限，不要把「谁点了赞」当主"
                "特征——这条通道读不到点赞人名单，只能靠回复"},
]

_INDEX = {rule["id"]: rule for rule in rules()}


def _values(rows) -> dict:
    """`{name: value}` for the feature rows that were actually measured."""
    values = {}
    for row in rows or ():
        if not isinstance(row, dict) or not row.get("name"):
            continue
        value = row.get("value")
        values[str(row["name"])] = value if (row.get("known") and value is not None) else None
    return values


def flags(rows, *, counts: dict, confidence: str) -> dict:
    """This run's risk flags, plus the ledger of what was checked and what was not.

    One `flag` per 方案 §三 第 4 步 code, not per rule: the table is keyed
    `(feature_id, code)`, and two rules landing on `risk_bot` is one label with
    two reasons, not two labels.
    """
    values = _values(rows)
    replies = int((counts or {}).get("received", 0) or 0)
    checks, skipped = [], []
    for rule in rules():
        missing = [name for name in rule["needs"] if values.get(name) is None]
        if missing:
            skipped.append({"id": rule["id"], "code": rule["code"],
                            "blocked": f"需要 {'、'.join(missing)}，这一次没有读到"})
            continue
        if replies < rule["min_replies"]:
            # 2 条回复里 2 条是空壳不是 100% 空壳，是「什么都看不出来」。
            skipped.append({"id": rule["id"], "code": rule["code"],
                            "blocked": f"这次只读到 {replies} 条回复，不到 "
                                       f"{rule['min_replies']} 条，比例不作数"})
            continue
        checks.append({"id": rule["id"], "code": rule["code"],
                       "fired": bool(rule["test"](values)),
                       "because": rule["because"]})
    skipped += [{"id": entry["id"], "code": entry["code"], "blocked": entry["blocked"]}
                for entry in BLOCKED]

    by_code: dict[str, dict] = {}
    for check in checks:
        if not check["fired"]:
            continue
        one = by_code.setdefault(check["code"], {
            "code": check["code"], "label": CODE_LABEL[check["code"]],
            "layer": _INDEX[check["id"]]["layer"], "rules": [],
            "evidence": "", "confidence": confidence})
        one["rules"].append(check["id"])
        one["evidence"] = "；".join(filter(None, (one["evidence"], check["because"])))
    return {"rule_version": RULE_VERSION, "model_version": MODEL_VERSION,
            "model_note": MODEL_NOTE, "confidence": confidence,
            "flags": [by_code[code] for code in CODES if code in by_code],
            "checks": checks, "skipped": skipped,
            "blocked": [dict(entry) for entry in BLOCKED]}


# --- 写库 -------------------------------------------------------------------

def record(store_obj, feature_id: str, handle: str, observed_at: int, result: dict,
           *, c=None) -> None:
    """Write one run's flags, and fold them into the watchlist.

    The flags are written inside the caller's transaction when given one: a run's
    features, scores and flags are the same observation, and a half-written triple
    would be read back as "computed the features, never looked for flags". The
    watchlist is written in that same transaction too, but it is the one row here
    that **outlives the run** — `hits` accumulates across runs, and `last_hit`
    keeps a retry of the same observation from counting twice.
    """
    if c is not None:
        _write(store_obj, c, feature_id, handle, observed_at, result)
        return
    with store_obj.db(True) as conn:
        _write(store_obj, conn, feature_id, handle, observed_at, result)


def _write(store_obj, c, feature_id: str, handle: str, observed_at: int,
           result: dict) -> None:
    now = int(time.time())
    # 重算会整段覆盖：上一次命中、这一次不命中的标签必须真的消失，否则
    # 「这个标现在还在不在」就答不上来。
    c.execute("DELETE FROM account_flags WHERE feature_id=?", (feature_id,))
    for one in result["flags"]:
        c.execute(
            """INSERT INTO account_flags(
                   feature_id,handle,code,layer,evidence,confidence,rule_version,created)
               VALUES(?,?,?,?,?,?,?,?)""",
            (feature_id, handle, one["code"], one["layer"], one["evidence"],
             one["confidence"], result["rule_version"], now))
    if not result["flags"]:
        return
    rule_ids = sorted({ident for one in result["flags"] for ident in one["rules"]})
    existing = c.execute(
        "SELECT hits,last_hit,hits_json FROM account_watchlist WHERE handle=?",
        (handle,)).fetchone()
    if existing is None:
        c.execute(
            """INSERT INTO account_watchlist(
                   handle,added_at,hits,last_hit,hits_json,status,note,action,updated)
               VALUES(?,?,?,?,?,'watching','','',?)""",
            (handle, now, 1, int(observed_at),
             json.dumps(rule_ids, ensure_ascii=False), now))
        return
    try:
        seen = json.loads(existing["hits_json"])
    except (TypeError, ValueError):
        seen = []
    merged = sorted(set(map(str, seen if isinstance(seen, list) else [])) | set(rule_ids))
    c.execute(
        """UPDATE account_watchlist SET hits=?,last_hit=?,hits_json=?,updated=?
            WHERE handle=?""",
        (existing["hits"] + (0 if existing["last_hit"] == int(observed_at) else 1),
         int(observed_at), json.dumps(merged, ensure_ascii=False), now, handle))


def for_feature(store_obj, feature_id: str) -> list[dict]:
    """The flag rows stored for one feature run, in code order."""
    return store_obj.rows(
        "SELECT * FROM account_flags WHERE feature_id=? ORDER BY code", (feature_id,))


# --- 观察室 -----------------------------------------------------------------

def _clean(handle: str) -> str:
    clean = str(handle or "").strip().lstrip("@").lower()
    if not HANDLE.fullmatch(clean):
        raise AccountFlagsError("用户名格式不正确。")
    return clean


def entry(store_obj, handle: str) -> dict | None:
    """One watchlist row, with `hits_json` parsed back."""
    rows = store_obj.rows("SELECT * FROM account_watchlist WHERE handle=?",
                          (_clean(handle),))
    if not rows:
        return None
    row = rows[0]
    try:
        row["hits_json"] = json.loads(row["hits_json"])
    except (TypeError, ValueError):
        row["hits_json"] = []
    return row


def watch(store_obj, handle: str, *, note: str = "") -> dict:
    """Put an account in the watchroom by hand.

    A hit adds the account automatically, so this is the way in for an account
    nobody's read yet — the operator sees something on the timeline and wants it
    kept. Re-adding never wipes a verdict: a row that was already reviewed stays
    reviewed, and a note is only replaced when a new one is given.
    """
    clean, now = _clean(handle), int(time.time())
    with store_obj.db(True) as c:
        c.execute(
            """INSERT INTO account_watchlist(
                   handle,added_at,hits,last_hit,hits_json,status,note,action,updated)
               VALUES(?,?,0,NULL,'[]','watching',?,'',?)
               ON CONFLICT(handle) DO UPDATE SET
                   note=CASE WHEN excluded.note<>'' THEN excluded.note ELSE note END,
                   updated=excluded.updated""",
            (clean, now, note, now))
    return entry(store_obj, clean)


def forget(store_obj, handle: str) -> bool:
    """Take an account out of the watchroom. False when it was not in it."""
    clean = _clean(handle)
    with store_obj.db(True) as c:
        return bool(c.execute("DELETE FROM account_watchlist WHERE handle=?",
                              (clean,)).rowcount)


def review(store_obj, handle: str, *, verdict: str, note: str = "",
           action: str = "") -> dict:
    """Record a human's verdict on one flagged account.

    This is the only place 精确率 comes from: 方案 §六 asks for 50 accounts a week
    looked at by hand, and a verdict is what turns a flag into a measurement of
    the flag's own precision.
    """
    if verdict not in STATUSES:
        raise AccountFlagsError(f"结论只能是 {('、'.join(STATUSES))} 之一。")
    clean = _clean(handle)
    with store_obj.db(True) as c:
        changed = c.execute(
            "UPDATE account_watchlist SET status=?,note=?,action=?,updated=? WHERE handle=?",
            (verdict, note, action, int(time.time()), clean)).rowcount
        if not changed:
            raise AccountFlagsError("这个账号不在观察室里。")
    return entry(store_obj, clean)


def watchroom(store_obj) -> dict:
    """The review table: who was flagged, what they scored, and what a human said.

    Capped at `REVIEW_CAP` and the cap is disclosed rather than applied in
    silence: 方案 §六 说「每周抽 50 个」，一个不告诉你的截断会被读成「一共就这么多
    有问题的账号」。

    Ordered so the cap cannot hide the work: 待复核 的排在最前，复核过的排在后面。
    否则一个复核过、但命中时间很新的账号会占掉队列的名额，把一个还没人看过的账号
    挤出这 50 条——而队列存在的理由正是让人去看那些。
    """
    rows = store_obj.rows(
        """SELECT w.*, f.id AS feature_id, f.observed_at AS last_observed,
                  f.confidence AS run_confidence,
                  s.authenticity, s.professionalism, s.monetization
             FROM account_watchlist w
             LEFT JOIN account_features f
                    ON f.id=(SELECT x.id FROM account_features x
                              WHERE x.handle=w.handle
                              ORDER BY x.observed_at DESC, x.id DESC LIMIT 1)
             LEFT JOIN account_scores s ON s.feature_id=f.id
            ORDER BY w.status<>'watching', w.last_hit IS NULL, w.last_hit DESC,
                     w.hits DESC, w.updated DESC""")
    handles = [row["handle"] for row in rows]
    fired: dict[str, list[dict]] = {}
    if handles:
        marks = ",".join("?" for _ in handles)
        for row in store_obj.rows(
                f"""SELECT feature_id,handle,code,layer,evidence FROM account_flags
                     WHERE handle IN ({marks})""", tuple(handles)):
            fired.setdefault(row["handle"], []).append(dict(row))
    for row in rows:
        try:
            row["hits_json"] = json.loads(row["hits_json"])
        except (TypeError, ValueError):
            row["hits_json"] = []
        # 只留**这一次运行**的标签：观察室要说的是「它现在为什么在榜上」，
        # 一个上次命中、这次已经消失的标不该继续挂着。
        row["flags"] = [{"code": one["code"], "layer": one["layer"],
                         "evidence": one["evidence"]}
                        for one in fired.get(row["handle"], [])
                        if one["feature_id"] == row["feature_id"]]
    queue = rows[:REVIEW_CAP]
    for row in queue:
        row.pop("feature_id", None)
    confirmed = sum(1 for row in rows if row["status"] == "confirmed")
    dismissed = sum(1 for row in rows if row["status"] == "dismissed")
    reviewed = confirmed + dismissed
    return {"watchlist": queue, "cap": REVIEW_CAP, "total": len(rows),
            "truncated": len(rows) > REVIEW_CAP,
            "review": {"reviewed": reviewed, "confirmed": confirmed,
                       "dismissed": dismissed, "pending": len(rows) - reviewed,
                       # 一次都没复核过时它不是 0：0 会说「精确率是 0%」，
                       # 而事实是「还没有人量过」。面板渲染「暂无数据」。
                       "precision": round(confirmed / reviewed, 4) if reviewed else None},
            "codes": [{"code": code, "label": CODE_LABEL[code]} for code in CODES],
            # 复核的人也要看得见「这四个标这一路读取本来就没打过」——否则他会以为
            # 名单上没出现某个标，是那件事没发生。
            "blocked": [dict(entry) for entry in BLOCKED],
            "statuses": [{"status": status, "label": STATUS_LABEL[status]}
                         for status in STATUSES],
            "rule_version": RULE_VERSION, "model_version": MODEL_VERSION,
            "model_note": MODEL_NOTE,
            "thresholds": thresholds()}


def thresholds() -> dict:
    """Every number this layer decides with, so the panel can show its work."""
    return {"min_replies": MIN_REPLIES, "shell_flag_rate": SHELL_FLAG_RATE,
            "cliche_flag_rate": CLICHE_FLAG_RATE, "reply_like_floor": REPLY_LIKE_FLOOR,
            "pod_repeat_rate": POD_REPEAT_RATE,
            "pod_followers_floor": POD_FOLLOWERS_FLOOR,
            "shill_link_share": SHILL_LINK_SHARE, "review_cap": REVIEW_CAP}
