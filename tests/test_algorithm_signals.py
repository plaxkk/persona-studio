"""06D 追加交付：「当前逻辑」清单——数值只能来自解析，不能来自手抄。

这一份清单的全部价值在于**可核对**：每一行都指得出文件、行号和上游原文。
所以这里的测试盯的就是这件事，而不是排版：

* **值必须能在夹具里对上** —— 夹具里的数值是**故意改过**的（`FavoriteWeight`
  上游 0.66、夹具 0.99）。清单里出现 0.99 才证明它是解析出来的；出现 0.66
  就说明有东西是抄来的。
* **认不出来就是认不出来** —— 字面量解析失败必须是 `null` + 保留原文，
  绝不能顺手填 0。「我们没解析出来」和「上游就是 0」是两件事。
* **没列出的要报出来** —— 上游有、清单没列的参数必须在覆盖报告里点名。
  「查不到」和「没有」不能混。
* **带 Weight 的名字必须表态** —— 上游新加一个权重参数而清单没跟上，
  脚本要**拒绝写文件**，而不是写一份看起来完整的清单。

测试全部离线跑 `fixtures/algorithm-clone-mini/`，不碰 `.local/` 下那份克隆
（那是 gitignored 的维护期方便，不是依赖）。唯一碰它的是一个会 skip 的用例。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts/parse_algorithm_signals.py"
FIXTURE = ROOT / "fixtures/algorithm-clone-mini"
FIXTURE_SHA = "0000000000000000000000000000000000000000"
CURATION = ROOT / "references/algorithm-signals.curation.json"
COMMITTED = ROOT / "assets/web-admin/src/data/algorithmSignals.json"
CLONE = ROOT / ".local/research/x-algorithm-main-20261015"

GROUPS = [
    "follow_graph",
    "engagement_positive",
    "engagement_negative",
    "multipliers",
    "gates",
    "cold_start",
]
UNITS = {
    "weight", "boost", "multiplier", "gate", "constant", "count",
    "seconds", "milliseconds", "minutes", "hours", "days", "position", "threshold",
}
DIRECTIONS = {"positive", "negative", "neutral"}
TARGETS = {"flow", "follower"}


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT, capture_output=True, text=True,
    )


@pytest.fixture(scope="module")
def payload(tmp_path_factory) -> dict:
    """夹具跑一遍，产出清单。整个模块共用这一份。"""
    out = tmp_path_factory.mktemp("signals") / "algorithmSignals.json"
    done = run("--clone", str(FIXTURE), "--sha", FIXTURE_SHA, "--out", str(out))
    assert done.returncode == 0, done.stderr
    assert out.is_file(), "脚本说成功却没写文件"
    return json.loads(out.read_text(encoding="utf-8"))


def fixture_lines(rel: str, first: int, last: int) -> str:
    """夹具里第 first..last 行的原文（含两端）。

    `param!` 是跨行的：`source["line"]` 落在 `param!(` 那一行，参数名在下一行，
    数值又在更下面。所以对出处要按**整段**去对，不能只对一行——只对一行的话，
    多行写法的每一条都会「对不上」，测出来的是格式，不是事实。
    """
    lines = (FIXTURE / rel).read_text(encoding="utf-8").splitlines()
    return "\n".join(lines[min(first, last) - 1: max(first, last)])


# --------------------------------------------------------------------------
# 值必须能在夹具里对上
# --------------------------------------------------------------------------
def test_every_entry_traces_back_to_source(payload):
    """逐条回到夹具里那一行去对：符号和原文都得出自同一段源码。"""
    for entry in payload["entries"]:
        source = entry["source"]
        assert source["file"] in payload["upstream"]["paths"], entry["id"]
        assert isinstance(source["line"], int) and source["line"] > 0, entry["id"]
        span = fixture_lines(source["file"], source["line"], source["value_line"])
        assert entry["symbol"] in span, entry["id"]
        raw = entry["value_raw"]
        assert raw, f"{entry['id']} 没留下原文——原文是「我们没解析」和「上游是 0」的分界"
        assert raw in span, entry["id"]
        # 出处那一行本身得真的在这份文件里，别指到文件外去
        assert source["value_line"] <= len(
            (FIXTURE / source["file"]).read_text(encoding="utf-8").splitlines()
        ), entry["id"]


def test_values_are_the_fixture_values_not_upstream(payload):
    """夹具故意改了值。清单跟着夹具走，才说明它不是从别处抄来的。"""
    by_symbol = {entry["symbol"]: entry for entry in payload["entries"]}
    assert by_symbol["FavoriteWeight"]["value"] == 0.99   # 上游是 0.66
    assert by_symbol["ReportWeight"]["value"] == -100.0   # 上游是 -234.0
    assert by_symbol["ReplyWeight"]["value"] == 4.0       # 上游是 5.0
    assert by_symbol["OonWeightFactor"]["value"] == 0.25  # 上游是 0.75
    assert by_symbol["EngagementWindowDays"]["value"] == 7
    assert by_symbol["EnableBidirectionalFollowHydration"]["value"] is False


def test_unparseable_value_is_null_with_raw_kept(payload):
    """`VqvWeight` 在夹具里指向另一个常量。解析不出来就写 null——不是 0。"""
    entry = next(e for e in payload["entries"] if e["symbol"] == "VqvWeight")
    assert entry["value"] is None
    assert entry["value_raw"] == "FixtureUnresolved"


# --------------------------------------------------------------------------
# 清单自身的形状
# --------------------------------------------------------------------------
def test_group_order_is_the_page_order(payload):
    assert [g["id"] for g in payload["groups"]] == GROUPS
    seen = [entry["group"] for entry in payload["entries"]]
    # 每一条的 group 都真实存在，且 entries 是按分区顺序排列的
    assert set(seen) <= set(GROUPS)
    order = {name: i for i, name in enumerate(GROUPS)}
    assert [order[g] for g in seen] == sorted(order[g] for g in seen)


def check_group_order(payload: dict) -> set[str]:
    """序内次序：权重类的区按官方数值排，其余按 curation 里写死的 order。

    区里的数不同量纲（0.75 的站外折扣、7 天的窗口、第 15 号坑位）按大小排
    等于造一个假比较，所以那几区只能按人写好的次序来。返回实际出现过的分区。
    """
    by_group: dict[str, list[dict]] = {}
    for entry in payload["entries"]:
        by_group.setdefault(entry["group"], []).append(entry)

    def known(group: str) -> list:
        values = [e["value"] for e in by_group.get(group, [])]
        seen = [v for v in values if v is not None]
        # 认不出值的排在最后：它没有名次，但也不许插在中间冒充一个已知的数。
        assert values[len(seen):] == [None] * (len(values) - len(seen)), group
        return seen

    positives = known("engagement_positive")
    assert positives == sorted(positives, reverse=True)

    negatives = known("engagement_negative")
    assert negatives == sorted(negatives)          # 都是负的，升序 = 绝对值降序
    assert negatives[0] == min(negatives)          # 最负的在最前，不是最后

    for group in ("follow_graph", "multipliers", "gates", "cold_start"):
        orders = [e["order"] for e in by_group.get(group, [])]
        assert orders == sorted(orders), group
    return set(by_group)


def test_within_group_order(payload):
    """夹具上：次序对了，而且这次确实排过东西（空集也能「全对」）。"""
    seen = check_group_order(payload)
    assert {"follow_graph", "engagement_positive", "engagement_negative"} <= seen
    assert seen <= set(GROUPS)


def test_committed_inventory_is_ordered_too():
    """线上那份六个区都要排过——夹具里没有 gates，别让它在真清单里漏掉。"""
    payload = json.loads(COMMITTED.read_text(encoding="utf-8"))
    assert check_group_order(payload) == set(GROUPS)


def test_no_duplicate_ids_and_each_entry_is_described(payload):
    ids = [entry["id"] for entry in payload["entries"]]
    assert len(ids) == len(set(ids)), "清单里有重复的 id"
    for entry in payload["entries"]:
        assert entry["id"].startswith(entry["source"]["file"] + "::"), entry["id"]
        assert entry["name"].strip(), entry["id"]
        assert entry["note"].strip(), f"{entry['id']} 的解释是空的（空解释等于没解释）"
        assert isinstance(entry["condition"], str), entry["id"]
        assert entry["unit"] in UNITS, entry["id"]
        assert entry["direction"] in DIRECTIONS, entry["id"]
        assert entry["target"] and set(entry["target"]) <= TARGETS, entry["id"]


def test_conflicts_record_both_sources(payload):
    """两份 param 文件给了不同的值时，两个出处都要摊出来，不挑一个信。"""
    conflict = next(c for c in payload["conflicts"] if c["symbol"] == "FavoriteWeight")
    files = {source["file"]: source["value"] for source in conflict["sources"]}
    assert files == {
        "home-mixer/params/param.rs": 0.99,
        "vm-ranker/params.rs": 0.5,
    }


def test_not_listed_covers_the_four_kinds(payload):
    kinds = {item["kind"] for item in payload["not_listed"]}
    assert kinds == {"withheld", "not_public", "unread", "out_of_scope"}
    for item in payload["not_listed"]:
        assert item["title"].strip()
        assert item["detail"].strip()
        assert item["evidence"], f"{item['kind']} 说了「查不到」，就得给出查过的痕迹"


def test_visibility_is_machine_extracted(payload):
    """可见性不是手写的散文，是抽出来的事实——所以它有行号，也能被 --check 发现漂移。"""
    visibility = payload["visibility"]
    levels = {row["id"]: row["rules"] for row in visibility["levels"]}
    assert levels == {"TIMELINE_HOME": 2, "TIMELINE_HOME_RECOMMENDATIONS": 3}
    # 推荐位是关注流的严格超集：多出来的那条只在推荐位被拿掉，反向为空。
    assert visibility["recommendations_only"] == ["RULE_RECOMMENDATIONS_ONLY_DROP"]
    assert visibility["home_only"] == []
    assert visibility["audiences"] == {"Everyone": 1, "NonAuthors": 1, "NonFollowers": 1}
    assert [row["rule"] for row in visibility["non_follower_rules"]] == [
        "RULE_NON_FOLLOWER_DROP"
    ]
    assert visibility["verdict"]["line"] > 0
    assert visibility["audience_enum"]["non_followers_condition_line"] > 0


# --------------------------------------------------------------------------
# 不许静默丢，不许悄悄改
# --------------------------------------------------------------------------
def test_unlisted_fixture_params_are_reported(payload, tmp_path):
    """夹具里有、清单没列的，必须在报告里点名。"""
    out = tmp_path / "signals.json"
    done = run("--clone", str(FIXTURE), "--sha", FIXTURE_SHA, "--out", str(out))
    assert done.returncode == 0, done.stderr
    for symbol in ("AdsTimeGapTSec", "EnableAdsSource", "FIXTURE_UNRELATED_FLAG"):
        assert symbol in done.stdout, f"{symbol} 没列进清单，也没在报告里出现——静默丢了"
    # 名字要写全。折成「FIXTURE×2」虽然短，但正好把要看的那一个淹掉。
    assert "FIXTURE×" not in done.stdout
    assert f"清单收 {len(payload['entries'])} 个" in done.stdout


def test_unstated_weight_blocks_the_write(tmp_path):
    """上游出现带 Weight 的名字而清单没表态时：**拒绝写文件**。

    这是整个防漂移机制的那道闸——与其写一份看上去完整、其实已经落后于上游的
    清单，不如什么都不写。
    """
    clone = tmp_path / "mini"
    shutil.copytree(FIXTURE, clone)
    target = clone / "home-mixer/params/param.rs"
    target.write_text(
        target.read_text(encoding="utf-8")
        + '\nparam!(FixtureProbeWeight, f64, "rust_home_mixer_fixture_probe_weight", 1.5);\n',
        encoding="utf-8",
    )
    out = tmp_path / "never-written.json"
    done = run("--clone", str(clone), "--sha", FIXTURE_SHA, "--out", str(out))
    assert done.returncode == 2, done.stdout
    assert "FixtureProbeWeight" in done.stderr
    assert "FixtureProbeWeight" in done.stdout, "点名要点在覆盖报告里"
    assert not out.exists(), "有没表态的参数，就不该写出文件来"

    # 表态之后（哪怕表态是「不收」），同一份克隆就该能过——闸门不是死的。
    curation = json.loads(CURATION.read_text(encoding="utf-8"))
    curation["exclude"]["home-mixer/params/param.rs::FixtureProbeWeight"] = "夹具用，不收"
    patched = tmp_path / "curation.json"
    patched.write_text(json.dumps(curation, ensure_ascii=False), encoding="utf-8")
    done = run("--clone", str(clone), "--sha", FIXTURE_SHA, "--out", str(out),
               "--curation", str(patched))
    assert done.returncode == 0, done.stderr
    assert out.is_file()


def test_missing_file_refuses_to_produce_half_a_list(tmp_path):
    """上游少了关键文件就退出，不做「部分成功」。"""
    clone = tmp_path / "mini"
    shutil.copytree(FIXTURE, clone)
    (clone / "visibility-filtering/rules/levels.rs").unlink()
    done = run("--clone", str(clone), "--sha", FIXTURE_SHA,
               "--out", str(tmp_path / "nope.json"))
    assert done.returncode == 2
    assert "levels.rs" in done.stderr


# --------------------------------------------------------------------------
# 仓库里的 curation / 清单
# --------------------------------------------------------------------------
def walk_keys(node):
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from walk_keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from walk_keys(item)


def test_curation_holds_no_numbers_of_its_own():
    """curation 是人写的那一半：中文名、方向、解释、为什么不收。

    **数值一个都不该有**——数值全部来自机器解析。curation 里出现一个手写的
    数值，就是一份能悄悄和上游漂开的副本。
    """
    curation = json.loads(CURATION.read_text(encoding="utf-8"))
    assert not [key for key in walk_keys(curation) if key == "value"]
    for ident, meta in curation["include"].items():
        assert set(meta) <= {"name", "unit", "target", "direction", "group",
                            "condition", "note", "order"}, ident
        assert meta["group"] in GROUPS, ident


def test_curation_covers_every_weight_like_name_in_the_inventory():
    """清单里带权重字样的每一条，curation 都得有表态。"""
    curation = json.loads(CURATION.read_text(encoding="utf-8"))
    payload = json.loads(COMMITTED.read_text(encoding="utf-8"))
    assert payload["unclassified"] == []
    alarm = {
        entry["id"] for entry in payload["entries"]
        if re.search(r"Weight|Boost|Multiplier|Penalty", entry["symbol"])
    }
    assert alarm <= set(curation["include"])
    for ident in alarm:
        assert ident not in curation["exclude"], f"{ident} 既收又拒，自相矛盾"


def test_committed_inventory_is_pinned_to_a_commit():
    payload = json.loads(COMMITTED.read_text(encoding="utf-8"))
    upstream = payload["upstream"]
    assert re.fullmatch(r"[0-9a-f]{40}", upstream["sha"]), upstream["sha"]
    assert upstream["repo"] == "xai-org/x-algorithm"
    assert upstream["branch"] == "main"
    assert upstream["license"] == "Apache-2.0"
    assert upstream["read_at"]
    assert payload["notes"]["weights"].strip()
    assert payload["notes"]["engagement"].strip()
    for entry in payload["entries"]:
        assert entry["source"]["file"] in upstream["paths"], entry["id"]
    assert not [e for e in payload["entries"] if e["value"] is None], \
        "线上清单里有解析不出来的值——要么补齐解析，要么把它挪进 not_listed"


@pytest.mark.skipif(not CLONE.is_dir(), reason="本机没有上游克隆（.local/ 是 gitignored）")
def test_committed_inventory_matches_the_upstream_clone():
    """有克隆的时候顺手核一遍：仓库里那份清单和上游对得上吗。

    这条只在开发机上跑得起来，所以它**不是**清单正确性的唯一凭据——
    上面那些夹具用例才是。它在这里是为了让「清单落后于上游」当场暴露。
    """
    done = run("--clone", str(CLONE), "--check")
    assert done.returncode == 0, done.stdout + done.stderr
