"""账号质量三维分数与门槛（06C-2）。

《账号质量打标程序化方案》§二 说得很清楚：**输出 3 个 0–100 分 + 若干硬标签，
不要合成一个玄学总分**；§五 接着规定打分「不要线性加权完事，用门槛 + 组合」——
**先一票否决，再加减分**。本模块就是那个规则层：输入是 06C-1 的特征行，输出是
真实性 / 专业性 / 盈利能力三个分，加上一份**哪条规则跑了、哪条评不了**的账。

三条决定写法的事：

1. **分数可以不存在**。方案说「抓到的回复样本 < 30 就标 low_confidence，不要强行
   下结论」，所以样本薄是 `low_confidence`——分数照给；但连基准证据都没有时（比如
   一条回复都没读到），真实性这个分是 **NULL**。理由：没有证据的 50 分和「评过了、
   结果就是 50」在面板上长得一模一样，而这两件事对操作者的含义相反。
2. **评不了的规则要留痕**。方案举的「发帖后 3 分钟内集中完成大部分点赞」需要互动
   时间突发分数，而这条通道根本拿不到（`account_quality` 的 `_unmeasurable()` 里
   写着为什么）。评不了就记成一条 `blocked` 并带上原因，**绝不默认它「没触发」**
   ——「检查过了、没问题」和「没能检查」是两件事。
3. **哪些线是方案给的、哪些是我们自己定的，分开放**。方案原文只给了两个数（模板
   回复 > 55%、空壳互动者 > 35% 各封顶 35 分），其余阈值方案没写。这里把它们集中成
   一组命名常量并在面板上照抄显示，免得读者以为每个数字都有出处。

模型层（方案 §三 第 3 步「先用规则打硬标签，再用模型打连续分」的后半句）**没有做**：
`MODEL_VERSION` 是空字符串，面板据此说明这一层还没建，而不是显示一个假的版本号。

硬标签（`risk_bot` / `risk_pod` / `inauthentic_engagement` …）属 06C-3 的 `account_flags`，
本模块只写 `account_scores`，不写任何标签行；但封顶规则**实际触发过**这件事会记在
分数自己的账里，06C-3 直接接。
"""

from __future__ import annotations

import json
import time

RULE_VERSION = "06C-2.1"
# 方案 §三 第 3 步是「先用规则打硬标签，再用模型打连续分」。这里只有前半句。
MODEL_VERSION = ""

# 方案没有给基准分，只说「先一票否决，再加减分」——而这句话需要一个起点。取 0–100
# 的中点 50：一条规则都没触发时，分数表示的是「查过了，没有加分也没有减分」。它必须
# 和「没查」分开，所以没查到基准证据时给的是 NULL，不是 50。
BASE = 50
# 方案 §四 的标签设计只有 auth_low / auth_mid / auth_high 三档，没给分界。这里按它
# 自己的例子定：§五 举的「真实性上限 40」说明 40 上下就是「低」这一档，70 起算「高」。
BAND_CUTS = (40, 70)

SCORES = ("authenticity", "professionalism", "monetization")
SCORE_LABEL = {"authenticity": "真实性", "professionalism": "专业性",
               "monetization": "盈利能力"}
# 方案 §四 的账号级标签名。盈利能力它只给了两档（monetizable / weak_monetization），
# 其余两个各三档。
LABELS = {"authenticity": ("auth_low", "auth_mid", "auth_high"),
          "professionalism": ("pro_low", "pro_mid", "pro_high"),
          "monetization": ("weak_monetization", "monetizable")}

# 一个分数要成立，先要它的证据在场。真实性与盈利能力说的都是「别人怎么跟它互动」，
# 没有回复就无从谈起；专业性在只有原帖时也能说一点（主题一致性）。因此这里写的是
# 「这几个读取里至少有一个读到了东西」。
FLOOR = {"authenticity": ("received",),
         "professionalism": ("post", "reply"),
         "monetization": ("received",)}
FLOOR_REASON = {
    "authenticity": "近 90 天没有读到别人回复它的内容，真实性就是别人怎么跟它互动，谈不了",
    "professionalism": "原帖和它自己的回复一条都没读到，这一层没有可看的文本",
    "monetization": "没有读到别人回复它的内容，变现信号只能从评论区的意向里看，谈不了",
}

# --- 方案原文给出的线 -------------------------------------------------------
# §五 的伪代码写的是 0.55（§二 另有一处 60%，那是打 inauthentic_engagement 硬标签的
# 条件，不是封顶的条件，两个数不是一回事）。
CLICHE_CAP_RATE = 0.55
SHELL_CAP_RATE = 0.35      # §五 的伪代码：空壳互动者 > 0.35 → 真实性上限 35
CAP_SCORE = 35             # §五 例子里两个封顶值都是 35
AGE_BONUS_DAYS = 365       # §五：account_age > 365 … 真实性 +15
BONUS = 15

# --- 方案没给、由本机定的线（面板会把这一组照抄给读者看）---------------------
COHESION_HIGH = 0.5        # 主题一致性（词面代理）算「高」的线
DISTINCT_REPLIERS_HIGH = 10  # 方案说 unique_repliers「high」，这是个需要数字的词
ER_FOLLOWERS_FLOOR = 0.0002  # 方案说互动率「过低扣分」，过低是多低由本机定
OWN_REPLY_LENGTH_FLOOR = 8   # 它回复别人的中位长度
OWN_QUESTION_RATE_HIGH = 0.3
INTENT_DENSITY_HIGH = 0.1
LINK_SHARE_HIGH = 0.2
PRO_ONE_LINER_CAP = 30
MON_INTENT_BONUS = 20
MON_LINK_BONUS = 10
NO_INTENT_CAP = 30
LOW_ENGAGEMENT_PENALTY = -15


def label_for(score: str, value) -> str:
    """The 方案 §四 label for one score, or '' when there is no score."""
    if value is None:
        return ""
    names = LABELS[score]
    if len(names) == 2:
        return names[1] if value >= BAND_CUTS[1] else names[0]
    if value < BAND_CUTS[0]:
        return names[0]
    return names[2] if value >= BAND_CUTS[1] else names[1]


def _rule(ident, score, kind, amount, needs, test, because) -> dict:
    return {"id": ident, "score": score, "kind": kind, "amount": amount,
            "needs": tuple(needs), "test": test, "because": because}


def rules() -> list[dict]:
    """Every rule that can be evaluated from 06C-1's features."""
    return [
        _rule("auth_cap_template_replies", "authenticity", "cap", CAP_SCORE,
              ("reply_cliche_rate",),
              lambda v: v["reply_cliche_rate"] > CLICHE_CAP_RATE,
              f"模板回复占比 > {CLICHE_CAP_RATE:.0%}，真实性封顶 {CAP_SCORE} 分（方案 §五）"),
        _rule("auth_cap_shell_engagers", "authenticity", "cap", CAP_SCORE,
              ("empty_engager_rate",),
              lambda v: v["empty_engager_rate"] > SHELL_CAP_RATE,
              f"空壳互动者占比 > {SHELL_CAP_RATE:.0%}，真实性封顶 {CAP_SCORE} 分（方案 §五）"),
        # §五 的例子里这条读的是「互动率是否与体量匹配」；「高得离谱也扣分」那半边
        # 没有界线，见 BLOCKED。
        _rule("auth_penalty_low_engagement", "authenticity", "delta",
              LOW_ENGAGEMENT_PENALTY, ("follower_er_median",),
              lambda v: v["follower_er_median"] < ER_FOLLOWERS_FLOOR,
              f"粉丝口径互动率 < {ER_FOLLOWERS_FLOOR:.4%}，互动与体量不匹配（线由本机定）"),
        _rule("auth_bonus_organic", "authenticity", "delta", BONUS,
              ("account_age_days", "text_cohesion", "distinct_engagers"),
              lambda v: (v["account_age_days"] > AGE_BONUS_DAYS
                         and v["text_cohesion"] >= COHESION_HIGH
                         and v["distinct_engagers"] >= DISTINCT_REPLIERS_HIGH),
              f"账号 > {AGE_BONUS_DAYS} 天、主题一致性 ≥ {COHESION_HIGH}、"
              f"不重复互动者 ≥ {DISTINCT_REPLIERS_HIGH} 人，真实性 +{BONUS}"
              "（方案给方向，三个线由本机定）"),
        _rule("pro_cap_one_liner_replies", "professionalism", "cap",
              PRO_ONE_LINER_CAP, ("own_reply_length_median",),
              lambda v: v["own_reply_length_median"] < OWN_REPLY_LENGTH_FLOOR,
              f"它回复别人的中位长度 < {OWN_REPLY_LENGTH_FLOOR} 字，专业性封顶 "
              f"{PRO_ONE_LINER_CAP} 分（线由本机定）"),
        _rule("pro_bonus_dialogue", "professionalism", "delta", BONUS,
              ("own_reply_question_rate",),
              lambda v: v["own_reply_question_rate"] >= OWN_QUESTION_RATE_HIGH,
              f"它自己的回复里 ≥ {OWN_QUESTION_RATE_HIGH:.0%} 带提问，是在对话而不是"
              "广播（线由本机定）"),
        _rule("pro_bonus_cohesion", "professionalism", "delta", BONUS,
              ("text_cohesion",),
              lambda v: v["text_cohesion"] >= COHESION_HIGH,
              f"主题一致性（词面代理）≥ {COHESION_HIGH}，专业性 +{BONUS}（线由本机定）"),
        _rule("mon_bonus_intent", "monetization", "delta", MON_INTENT_BONUS,
              ("reply_intent_density",),
              lambda v: v["reply_intent_density"] >= INTENT_DENSITY_HIGH,
              f"意向评论密度 ≥ {INTENT_DENSITY_HIGH:.0%}，盈利能力 +{MON_INTENT_BONUS}"
              "（线由本机定）"),
        _rule("mon_cap_no_intent", "monetization", "cap", NO_INTENT_CAP,
              ("reply_intent_density",),
              lambda v: v["reply_intent_density"] == 0,
              f"读到的回复里一条意向评论都没有，盈利能力封顶 {NO_INTENT_CAP} 分"
              "（线由本机定）"),
        _rule("mon_bonus_links", "monetization", "delta", MON_LINK_BONUS,
              ("link_share",),
              lambda v: v["link_share"] >= LINK_SHARE_HIGH,
              f"带链接帖占比 ≥ {LINK_SHARE_HIGH:.0%}，盈利能力 +{MON_LINK_BONUS}"
              "（这是「CTA 帖占比」的代理，线由本机定）"),
    ]


# 方案写着、但这一条通道评不了的规则。收集在这里而不是删掉，是为了让「没扣分」和
# 「没能看」在面板上不是同一件事——上一片（06C-1）把它们记成 `known=0` 的特征行，
# 这里是它们在规则层的同一句话。
BLOCKED = [
    {"id": "auth_burst_and_relevance", "score": "authenticity",
     "blocked": "方案要「互动时间突发 + 回复语义相关度」两条一起看；突发分数这条通道"
                "根本拿不到（第三方看不到单条推文的互动时间分布），语义相关度要模型层"},
    {"id": "auth_engagement_too_high", "score": "authenticity",
     "blocked": "方案说互动率「高得离谱也扣分」，但没给界线；没有校准数据时本机不猜这个数"},
    {"id": "pro_information_density", "score": "professionalism",
     "blocked": "信息密度要数字 / ticker / 代码块 / 图表各自的占比，这一片只采了链接与媒体有无"},
    {"id": "pro_quoted_substance", "score": "professionalism",
     "blocked": "「被实质引用」要看引用正文里有没有反驳或补充，搜索读回的是帖子本身，不带引用正文"},
    {"id": "pro_negative_handling", "score": "professionalism",
     "blocked": "「争议处理」要能分辨解释与只骂 / 删评，删评看不到，看得到的负面回复又没有标签"},
    {"id": "mon_verified_share", "score": "monetization",
     "blocked": "蓝V互动占比要互动者的认证字段，搜索结果不带这个字段"},
]


def _known_values(rows) -> dict:
    """`{name: value}` for the features that were actually measured."""
    values = {}
    for row in rows or ():
        if not isinstance(row, dict) or not row.get("name"):
            continue
        value = row.get("value")
        values[str(row["name"])] = value if (row.get("known") and value is not None) else None
    return values


def _floor_met(score: str, counts: dict) -> bool:
    return any(int((counts or {}).get(kind, 0) or 0) >= 1 for kind in FLOOR[score])


def _one(score: str, values: dict, counts: dict) -> dict:
    applied, skipped = [], []
    for rule in rules():
        if rule["score"] != score:
            continue
        # 需要的东西这次没读到：这条规则这一次等于没跑，而且要说清差哪一个。
        missing = [name for name in rule["needs"] if values.get(name) is None]
        if missing:
            skipped.append({"id": rule["id"],
                            "blocked": f"需要 {'、'.join(missing)}，这一次没有读到"})
            continue
        applied.append({"id": rule["id"], "kind": rule["kind"],
                        "amount": rule["amount"],
                        "fired": bool(rule["test"](values)),
                        "because": rule["because"]})
    skipped += [{"id": entry["id"], "blocked": entry["blocked"]}
                for entry in BLOCKED if entry["score"] == score]

    entry = {"base": BASE, "value": None, "label": "", "reason": "",
             "rules": applied, "skipped": skipped}
    if not _floor_met(score, counts):
        entry["reason"] = FLOOR_REASON[score]
        return entry
    # 方案 §五：**先一票否决，再加减分**。顺序是有意义的——先封顶再加减，意味着一个
    # 满分的加分项救不回被一票否决的账号；反过来先加再封顶就成了线性加权。
    value = BASE
    for rule in applied:
        if rule["fired"] and rule["kind"] == "cap":
            value = min(value, rule["amount"])
    for rule in applied:
        if rule["fired"] and rule["kind"] == "delta":
            value += rule["amount"]
    entry["value"] = max(0, min(100, int(round(value))))
    entry["label"] = label_for(score, entry["value"])
    return entry


def thresholds() -> dict:
    """Every number this layer decides with, so the panel can show its work."""
    return {"base": BASE, "band_cuts": list(BAND_CUTS),
            "cliche_cap_rate": CLICHE_CAP_RATE, "shell_cap_rate": SHELL_CAP_RATE,
            "cap_score": CAP_SCORE, "age_bonus_days": AGE_BONUS_DAYS,
            "bonus": BONUS, "cohesion_high": COHESION_HIGH,
            "distinct_repliers_high": DISTINCT_REPLIERS_HIGH,
            "er_followers_floor": ER_FOLLOWERS_FLOOR,
            "own_reply_length_floor": OWN_REPLY_LENGTH_FLOOR,
            "own_question_rate_high": OWN_QUESTION_RATE_HIGH,
            "intent_density_high": INTENT_DENSITY_HIGH,
            "link_share_high": LINK_SHARE_HIGH}


def score(rows, *, counts: dict, confidence: str) -> dict:
    """Three scores plus the ledger of what was checked and what could not be."""
    values = _known_values(rows)
    return {"rule_version": RULE_VERSION, "model_version": MODEL_VERSION,
            "model_note": "模型层未建（方案 §三：先用规则，再用模型）",
            "confidence": confidence,
            "scores": {name: _one(name, values, counts) for name in SCORES},
            "thresholds": thresholds()}


def record(store_obj, feature_id: str, handle: str, result: dict, *,
           c=None) -> None:
    """Store one account's scores, keyed to its feature run.

    Writes inside the caller's transaction when given one: a feature run and the
    scores computed from it are the same observation, and a half-written pair
    would be read back as "computed the features, never scored them".
    """
    payload = json.dumps(result, ensure_ascii=False, sort_keys=True)
    now = int(time.time())
    statement = """
        INSERT INTO account_scores(
            feature_id,handle,authenticity,professionalism,monetization,
            confidence,rule_version,model_version,labels_json,created,updated)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(feature_id) DO UPDATE SET
            authenticity=excluded.authenticity,
            professionalism=excluded.professionalism,
            monetization=excluded.monetization,
            confidence=excluded.confidence,rule_version=excluded.rule_version,
            model_version=excluded.model_version,labels_json=excluded.labels_json,
            updated=excluded.updated
    """
    values = (feature_id, handle,
              result["scores"]["authenticity"]["value"],
              result["scores"]["professionalism"]["value"],
              result["scores"]["monetization"]["value"],
              result["confidence"], result["rule_version"], result["model_version"],
              payload, now, now)
    if c is not None:
        c.execute(statement, values)
        return
    with store_obj.db(True) as conn:
        conn.execute(statement, values)


def for_feature(store_obj, feature_id: str) -> dict | None:
    """The scores stored for one feature run, with the ledger parsed back."""
    rows = store_obj.rows(
        "SELECT * FROM account_scores WHERE feature_id=?", (feature_id,))
    if not rows:
        return None
    row = rows[0]
    try:
        row["labels_json"] = json.loads(row["labels_json"])
    except (TypeError, ValueError):
        row["labels_json"] = {}
    return row
