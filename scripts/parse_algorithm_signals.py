#!/usr/bin/env python3
"""把上游 X 算法的公开源码解析成一份「流量与涨粉逻辑」清单（06D 追加需求）。

这个脚本是维护期工具，不是运行时组件：它读一个上游克隆，产出
`assets/web-admin/src/data/algorithmSignals.json`，面板在**构建期**把那份 JSON
打进包里。所以面板不需要接口、不需要凭据，`.local/` 那个克隆也永远不会成为
运行时依赖。

它只做两件事，且分得很清楚：

* **机器抽事实** —— 官方标识符、数值、文件、行号、原文。这些不许人手抄，
  因为抄错一个数就是编。
* **人去写话** —— 中文名、目标（流量/涨粉）、方向、生效条件、一句话解释、
  以及对不上的理由。这些写在 `references/algorithm-signals.curation.json` 里，
  和清单一起进 git，改了什么一目了然。

两个防漂移的机制：

1. 抽到的参数里凡是名字带 `Weight` / `Boost` / `Multiplier` / `Penalty` 的，
   **必须**要么被收录、要么在 curation 里写明为什么不收。上游新加一个权重参数
   而我们没跟上，脚本会点名报错——这就是「清单落后于上游」的自动告警。
2. `--check` 重算一遍并和仓库里那份 JSON 比对，不一致就非零退出。

用法：

    python3 scripts/parse_algorithm_signals.py --clone <上游克隆目录> --scaffold
    python3 scripts/parse_algorithm_signals.py --clone <上游克隆目录>
    python3 scripts/parse_algorithm_signals.py --clone <上游克隆目录> --check
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets/web-admin/src/data/algorithmSignals.json"
CURATION = ROOT / "references/algorithm-signals.curation.json"

# 两份 param 文件都读：`home-mixer` 那份是主清单，`vm-ranker` 那份里有几个
# 只在它那里定义的（站外折扣、作者多样性衰减、扰动）。同名参数两份都有的，
# 清单里只引 `home-mixer` 那一份，另一份在 curation 的 exclude 里写明原因——
# 同一个名字给两个出处，读的人没法知道该信哪个。
PARAM_FILES = ("home-mixer/params/param.rs", "vm-ranker/params.rs")
PARAM_FILE = PARAM_FILES[0]
SCALA_FILE = "user-cred-v2/UserCredV2Config.scala"
# `NEGATIVE_SCORES_OFFSET = 0.001` 这种常量不是 param，但它是解释负向权重
# 为什么不能直读的关键，所以也机器抽、也给行号。
CONST_FILE = "xai-value-model/weights.rs"
# 可见性这一块不是「权重」，但它同样决定能不能被看到，所以照同一条规矩来：
# 机器抽、带行号、能自动发现漂移。它的结构比我原先以为的复杂——不是
# ALLOW/NOTICE/INTERSTITIAL/DROP 四个平行档，是「什么动作」×「对谁生效」两维。
VISIBILITY_LEVELS = "visibility-filtering/rules/levels.rs"
VISIBILITY_RULES = (
    "visibility-filtering/rules/tweet_rules.rs",
    "visibility-filtering/rules/author_rules.rs",
)
VISIBILITY_RULE_SPEC = "visibility-filtering/rules/rule_spec/rule.rs"
VERDICT_FILE = "visibility-filtering/models/verdict.rs"

VIS_LEVEL = re.compile(r'pub\(super\) const (?P<id>\w+): &\[&Rule\] = &\[(?P<body>.*?)\];', re.S)
VIS_RULE = re.compile(
    r'pub\(super\) const (?P<id>\w+): Rule = Rule \{\s*id: (?P<rule_id>[^,]+),\s*'
    r'audience: (?P<audience>\w+),',
    re.S,
)

# `param!(\n    Ident,\n    f64,\n    "rust_...",\n    -43.2\n);` —— 单行与多行都要吃下。
PARAM_MACRO = re.compile(
    r'param!\(\s*(?P<symbol>\w+)\s*,\s*(?P<type>[^,]+?)\s*,\s*'
    r'"(?P<official>[^"]+)"\s*,\s*(?P<raw>.+?)\s*\)\s*;',
    re.S,
)
# Scala 那侧只有几个键，值是第二个字符串（可带 .toDouble/.toInt）。
SCALA_KEY = re.compile(
    r'getOrElse\(\s*"(?P<official>[^"]+)"\s*,\s*"(?P<raw>[^"]*)"\s*\)'
    r'(?P<cast>\s*\.to[A-Za-z]+)?'
)
RUST_CONST = re.compile(
    r'^(?:pub )?const (?P<symbol>[A-Z][A-Z0-9_]*)\s*:\s*(?P<type>[\w:]+)\s*=\s*(?P<raw>[^;]+);',
    re.M,
)
# `val EngagementWindowDays: Int = 7` —— 带类型标注的才认，`val x = args(...)` 不认。
SCALA_VAL = re.compile(
    r'^\s*val (?P<symbol>[A-Za-z_]\w*)\s*:\s*(?P<type>[\w\[\]]+)\s*=\s*(?P<raw>[^,\n]+?)\s*$',
    re.M,
)
# 这一类的名字一旦在上游出现，就是我们必须表态的地方。
ALARM = re.compile(r"Weight|Boost|Multiplier|Penalty")
# 上游自己写下的那两条警告，原件在 param.rs:300-309。页面上照抄，不改写。
WEIGHTS_NOTE = (
    "上游在这段代码上面亲手写了警告：权重乘的是**预测概率**，不是次数。"
    "所以「一次举报抵 468 个赞」这种读法是错的——举报的基线概率比点赞低一千倍以上，"
    "正因为低才把权重给得这么大，好让这个预测还能影响最终排序。**跨动作比大小没有意义。**"
)
ENGAGEMENT_NOTE = (
    "同一条里的另一个要点：只有**出现在 Home Timeline 里的帖子**上发生的互动才算数。"
    "直接点进某条帖子的链接（比如在群里组织人去点）对排序没有影响。"
)


def parse_value(raw: str, type_name: str):
    """把字面量变成 Python 值。认不出来就返回 None —— 绝不猜一个 0 回去。"""
    text = raw.strip()
    kind = type_name.strip().lower()
    # 类型名有两套写法：Rust 的 `f64`/`u32`，JVM 的 `Double`/`Int`。少认一套
    # 就会把一个真实存在的值渲染成「暂无数据」——那是把「我们没解析」说成
    # 「上游没有」，和猜一个 0 一样是错。所以两套都收。
    if kind in {"f64", "f32", "double", "float", "bigdecimal"}:
        try:
            return float(text.replace("_", ""))
        except ValueError:
            return None
    if kind in {
        "i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64", "usize", "isize",
        "int", "integer", "long", "short", "byte", "bigint",
    }:
        try:
            return int(text.replace("_", ""))
        except ValueError:
            return None
    if kind == "bool":
        if text == "true":
            return True
        if text == "false":
            return False
        return None
    if kind in {"string", "&str"}:
        return text[1:-1] if text.startswith('"') and text.endswith('"') else text
    return None


def line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def one_line(fragment: str) -> str:
    return re.sub(r"\s+", " ", fragment).strip()


def read_param_file(path: Path, name: str) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    found = []
    for match in PARAM_MACRO.finditer(text):
        raw = match.group("raw")
        found.append(
            {
                "id": f"{name}::{match.group('symbol')}",
                "official": match.group("official"),
                "symbol": match.group("symbol"),
                "value": parse_value(raw, match.group("type")),
                "value_raw": one_line(raw),
                "value_type": match.group("type").strip(),
                "source": {
                    "file": name,
                    "line": line_of(text, match.start()),
                    "value_line": line_of(text, match.start("raw")),
                    "kind": "param_macro",
                    "span": one_line(match.group(0)),
                },
            }
        )
    return found


def read_const_file(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    found = []
    for match in RUST_CONST.finditer(text):
        raw = match.group("raw")
        found.append(
            {
                "id": f"{CONST_FILE}::{match.group('symbol')}",
                "official": match.group("symbol"),
                "symbol": match.group("symbol"),
                "value": parse_value(raw, match.group("type")),
                "value_raw": one_line(raw),
                "value_type": match.group("type").strip(),
                "source": {
                    "file": CONST_FILE,
                    "line": line_of(text, match.start()),
                    "value_line": line_of(text, match.start("raw")),
                    "kind": "rust_const",
                    "span": one_line(match.group(0)),
                },
            }
        )
    return found


def read_scala_file(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    found = []
    for match in SCALA_VAL.finditer(text):
        raw = match.group("raw")
        found.append(
            {
                "id": f"{SCALA_FILE}::{match.group('symbol')}",
                "official": match.group("symbol"),
                "symbol": match.group("symbol"),
                "value": parse_value(raw, match.group("type")),
                "value_raw": one_line(match.group(0)),
                "value_type": match.group("type").strip(),
                "source": {
                    "file": SCALA_FILE,
                    "line": line_of(text, match.start()),
                    "value_line": line_of(text, match.start("raw")),
                    "kind": "scala_val",
                    "span": one_line(match.group(0)),
                },
            }
        )
    for match in SCALA_KEY.finditer(text):
        raw = match.group("raw").strip()
        cast = (match.group("cast") or "").strip()
        if cast.endswith("toDouble"):
            value = parse_value(raw, "f64")
        elif cast.endswith("toInt"):
            value = parse_value(raw, "i32")
        else:
            value = parse_value(raw, "String")
        found.append(
            {
                "id": f"{SCALA_FILE}::{match.group('official')}",
                "official": match.group("official"),
                "symbol": match.group("official"),
                "value": value,
                "value_raw": one_line(match.group(0)),
                "value_type": cast.lstrip(".") or "String",
                "source": {
                    "file": SCALA_FILE,
                    "line": line_of(text, match.start()),
                    "value_line": line_of(text, match.start("raw")),
                    "kind": "scala_get_or_else",
                    "span": one_line(match.group(0)),
                },
            }
        )
    return found


def read_visibility(clone: Path) -> dict:
    """可见性限制：判定档位的集合、每条规则对谁生效、推荐位比关注流多哪些。

    重点在最后一个：同一个作者、同一条帖，出现在关注流里可能还在，出现在推荐
    位里就被直接拿掉——这两份清单的差集就是那句话的证据。
    """
    levels_text = (clone / VISIBILITY_LEVELS).read_text(encoding="utf-8")
    levels = []
    members: dict[str, list[str]] = {}
    for match in VIS_LEVEL.finditer(levels_text):
        names = [name.strip().lstrip("&") for name in re.findall(r"&(\w+)", match.group("body"))]
        members[match.group("id")] = names
        levels.append(
            {
                "id": match.group("id"),
                "file": VISIBILITY_LEVELS,
                "line": line_of(levels_text, match.start()),
                "rules": len(names),
            }
        )

    audience_count: dict[str, int] = {}
    non_follower: list[dict] = []
    for name in VISIBILITY_RULES:
        text = (clone / name).read_text(encoding="utf-8")
        for match in VIS_RULE.finditer(text):
            who = match.group("audience")
            audience_count[who] = audience_count.get(who, 0) + 1
            if who == "NonFollowers":
                # 这几条只在「看的人没关注这个作者」时才生效——关注了就看不到这条限制。
                non_follower.append(
                    {
                        "rule": match.group("id"),
                        "file": name,
                        "line": line_of(text, match.start()),
                    }
                )

    home = set(members.get("TIMELINE_HOME", []))
    recommend = set(members.get("TIMELINE_HOME_RECOMMENDATIONS", []))
    spec_text = (clone / VISIBILITY_RULE_SPEC).read_text(encoding="utf-8")
    audience_at = re.search(r'enum Audience \{', spec_text)
    non_followers_at = re.search(r'Self::NonFollowers => [^,]+', spec_text)
    return {
        "verdict": {"file": VERDICT_FILE, "line": _line_of_pattern(clone, VERDICT_FILE, r'pub enum Verdict \{')},
        "audience_enum": {
            "file": VISIBILITY_RULE_SPEC,
            "line": line_of(spec_text, audience_at.start()) if audience_at else None,
            "non_followers_condition_line": (
                line_of(spec_text, non_followers_at.start()) if non_followers_at else None
            ),
        },
        "levels": levels,
        "audiences": dict(sorted(audience_count.items())),
        "non_follower_rules": sorted(non_follower, key=lambda row: (row["file"], row["line"])),
        # 差集：推荐位有、关注流没有的那些「直接拿掉」。
        "recommendations_only": sorted(recommend - home),
        "home_only": sorted(home - recommend),
    }


def _line_of_pattern(clone: Path, name: str, pattern: str):
    path = clone / name
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    match = re.search(pattern, text)
    return line_of(text, match.start()) if match else None


def clone_identity(clone: Path) -> dict:
    def git(*args: str) -> str:
        try:
            done = subprocess.run(
                ["git", "-C", str(clone), *args],
                capture_output=True, text=True, timeout=30, check=True,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return done.stdout.strip()

    return {"sha": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain"))}


def check_clone(clone: Path) -> str:
    """克隆得是那份仓库，否则宁可什么都不写，也不要产出半份清单。"""
    if not clone.is_dir():
        return f"找不到上游克隆目录：{clone}"
    if not (clone / PARAM_FILE).is_file():
        return f"这个目录里没有 {PARAM_FILE} —— 上游可能改了结构，这次不要更新清单。"
    for name in (*PARAM_FILES[1:], CONST_FILE, SCALA_FILE, VISIBILITY_LEVELS,
                 VISIBILITY_RULE_SPEC, VERDICT_FILE, *VISIBILITY_RULES):
        if not (clone / name).is_file():
            return f"这个目录里没有 {name} —— 上游可能改了结构，这次不要更新清单。"
    readme = clone / "README.md"
    if not readme.is_file():
        return "这个目录里没有 README.md —— 看着不像上游克隆。"
    first = readme.read_text(encoding="utf-8", errors="replace").splitlines()[:1]
    if not first or "For You" not in first[0]:
        return f"README.md 的首行不是那份上游仓库（读到的是 {first!r}）。"
    return ""


def load_curation() -> dict:
    if not CURATION.is_file():
        return {"include": {}, "exclude": {}, "groups": [], "not_listed": []}
    return json.loads(CURATION.read_text(encoding="utf-8"))


def build(found: list[dict], curation: dict, upstream: dict, read_at: str, visibility: dict) -> dict:
    groups = curation["groups"]
    include = curation["include"]
    ordered = {group["id"]: i for i, group in enumerate(groups)}

    entries = []
    for row in found:
        meta = include.get(row["id"])
        if not meta:
            continue
        entry = dict(row)
        entry.update({k: v for k, v in meta.items() if k != "id"})
        entries.append(entry)

    # 顺序是清单的一部分：分区顺序 -> 区内次序 -> 再按 id 稳定。同一次输入
    # 两次运行必须字节一致，否则 git diff 看不出人改了什么。
    #
    # 区内次序分两种，不能混：
    # * 权重类的区（`ranked` / `rank_by`）——按官方数值排。这就是「按重要等级
    #   罗列」，等级是官方给的那个数，不是我们算的综合分。
    # * 其余区（乘子、门槛、冷启动、互关）——区里的数不同量纲（0.75 的站外
    #   折扣、7 天的窗口、第 15 号的坑位），按大小排等于造一个假比较。所以用
    #   curation 里写死的 `order`：次序是编的，那就明说是编的；官方的值照样
    #   摆在每一行里，读的人自己看。
    def sort_key(entry: dict):
        group = groups[ordered[entry["group"]]]
        value = entry.get("value")
        number = value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
        if entry["group"] == "engagement_negative" or group.get("rank_by") == "abs":
            rank = (0, -abs(number) if number is not None else 0.0, entry["id"])
        elif group.get("ranked") and group.get("rank_by") == "desc":
            rank = (0, -number if number is not None else 0.0, entry["id"])
        else:
            rank = (1, float(entry.get("order", 0)), entry["id"])
        return (ordered[entry["group"]], rank)

    entries.sort(key=sort_key)

    # 同一个官方名字在两份 param 文件里给了两个值（`ContDwellTimeWeight` 就是
    # 0.006 对 0.004）。这不是我们能替上游调解的事，所以照实摊开——清单里只引
    # 一份，但「另一份不一样」这句话必须让读的人看到。
    by_symbol: dict[str, list[dict]] = {}
    for row in found:
        by_symbol.setdefault(row["symbol"], []).append(row)
    conflicts = [
        {
            "symbol": symbol,
            "sources": sorted(
                (
                    {
                        "file": row["source"]["file"],
                        "line": row["source"]["line"],
                        "value": row["value"],
                    }
                    for row in rows
                ),
                key=lambda one: one["file"],
            ),
        }
        for symbol, rows in sorted(by_symbol.items())
        if len({(row["source"]["file"], row["value"]) for row in rows}) > 1
        and len({str(row["value"]) for row in rows}) > 1
    ]

    listed = {entry["id"] for entry in entries}
    excluded = curation["exclude"]
    unclassified = sorted(
        row["id"] for row in found
        if row["id"] not in listed and row["id"] not in excluded and ALARM.search(row["symbol"])
    )
    return {
        "upstream": {
            "repo": "xai-org/x-algorithm",
            "branch": "main",
            "sha": upstream.get("sha", ""),
            "dirty": upstream.get("dirty", False),
            "read_at": read_at,
            "license": "Apache-2.0",
            "paths": sorted({row["source"]["file"] for row in entries}),
        },
        "notes": {"weights": WEIGHTS_NOTE, "engagement": ENGAGEMENT_NOTE},
        "visibility": visibility,
        "groups": groups,
        "entries": entries,
        "conflicts": conflicts,
        "not_listed": curation["not_listed"],
        "unclassified": unclassified,
    }


def coverage(found: list[dict], payload: dict) -> str:
    """「上游还有什么我们没列」——只报告，不拦；拦的是带 Weight 的那一类。"""
    listed = {entry["id"] for entry in payload["entries"]}
    skipped = [row for row in found if row["id"] not in listed]
    lines = [
        f"上游这次读到 {len(found)} 个参数，清单收 {len(listed)} 个，其余 {len(skipped)} 个未列。",
    ]
    # 名字一个一个写出来。按前缀归并成「FIXTURE×3」看着清爽，但正好把要看的
    # 那个名字淹掉——上游新加一个 `AuthorExplorationBonus` 也会被折成
    # 「AuthorExplorationBonus×1」，而它和 `…V2` 读起来一模一样。这份报告的
    # 全部用处就是让人看见名字，所以宁长不折。
    CAP = 40
    if skipped:
        tail = ""
        if len(skipped) > CAP:
            tail = f"　（只列前 {CAP} 个，其余 {len(skipped) - CAP} 个用 --scaffold 看全）"
        lines.append("未列出的：" + "、".join(row["symbol"] for row in skipped[:CAP]) + tail)
    if payload["unclassified"]:
        lines.append("")
        lines.append("！上游出现了带 Weight/Boost/Multiplier/Penalty 的名字，但清单没表态：")
        lines.extend(f"  - {one}" for one in payload["unclassified"])
        lines.append("  要么收进 curation 的 include，要么在 exclude 里写明为什么不收。")
    return "\n".join(lines)


def write_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def main() -> int:
    global CURATION  # noqa: PLW0603 —— 命令行可换一份 curation，测试要用
    parser = argparse.ArgumentParser(description="解析上游源码，产出流量/涨粉逻辑清单")
    parser.add_argument("--clone", required=True, help="上游克隆目录（必填，脚本里不硬编码路径）")
    parser.add_argument("--out", default=str(OUT))
    parser.add_argument("--curation", default=str(CURATION))
    parser.add_argument("--read-at", default="", help="写进清单的读取时刻；不传用当前 UTC")
    parser.add_argument("--sha", default="", help="覆盖 sha（夹具目录没有 .git 时用）")
    parser.add_argument("--scaffold", action="store_true", help="列出所有抽到的参数，供人补 curation")
    parser.add_argument("--check", action="store_true", help="只比对仓库里那份，不写文件")
    parser.add_argument("--report-only", action="store_true", help="带未归类项也照常写文件")
    args = parser.parse_args()

    out = Path(args.out)
    CURATION = Path(args.curation)

    clone = Path(args.clone).expanduser()
    problem = check_clone(clone)
    if problem:
        print(f"这次不产出清单：{problem}", file=sys.stderr)
        return 2

    found: list[dict] = []
    for name in PARAM_FILES:
        path = clone / name
        if not path.is_file():
            print(f"这个目录里没有 {name} —— 上游可能改了结构，这次不要更新清单。", file=sys.stderr)
            return 2
        found.extend(read_param_file(path, name))
    found.extend(read_const_file(clone / CONST_FILE))
    found.extend(read_scala_file(clone / SCALA_FILE))
    if not found:
        print(f"在 {PARAM_FILE} 里一个参数都没抽到 —— 别更新清单，先看看上游改了什么。", file=sys.stderr)
        return 2

    if args.scaffold:
        for row in found:
            flag = "  <-- 带 Weight/Boost/Multiplier/Penalty" if ALARM.search(row["symbol"]) else ""
            print(f"{row['id']}\t{row['value_raw']}\tL{row['source']['line']}{flag}")
        print(f"\n共 {len(found)} 个。", file=sys.stderr)
        return 0

    upstream = clone_identity(clone)
    if args.sha:
        upstream["sha"] = args.sha
    if not upstream["sha"]:
        print("读不到这个克隆的 commit（没有 .git？）——清单需要钉一个 commit，先补上。", file=sys.stderr)
        return 2

    read_at = args.read_at or __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    payload = build(found, load_curation(), upstream, read_at, read_visibility(clone))

    if args.check:
        if not out.is_file():
            print(f"仓库里还没有 {out}。", file=sys.stderr)
            return 2
        have = json.loads(out.read_text(encoding="utf-8"))
        for key in ("groups", "entries", "not_listed", "notes", "visibility", "conflicts"):
            if have.get(key) != payload.get(key):
                print(f"清单和上游对不上了：{key} 不一样。", file=sys.stderr)
                return 1
        if have.get("upstream", {}).get("sha") != payload["upstream"]["sha"]:
            print("清单钉的 commit 和这个克隆不是同一条。", file=sys.stderr)
            return 1
        print("清单和这个克隆一致。")
        return 0

    print(coverage(found, payload))
    if payload["unclassified"] and not args.report_only:
        # 拒绝写文件的时候，把点名的参数也放进 stderr —— 只看退出码和 stderr 的
        # 调用方（CI、pytest）不该被迫去翻 stdout 才知道该给什么表态。
        print("有上游参数没表态，这次不写文件：", file=sys.stderr)
        for one in payload["unclassified"]:
            print(f"  - {one}", file=sys.stderr)
        print("要么收进 curation 的 include，要么在 exclude 里写明为什么不收。"
              "想先看看就加 --report-only。", file=sys.stderr)
        return 2

    write_json(out, payload)
    print(f"\n写出 {out}：钉在 {payload['upstream']['sha'][:7]}，共 {len(payload['entries'])} 条。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
