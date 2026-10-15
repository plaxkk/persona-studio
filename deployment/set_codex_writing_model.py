"""Pin Studio's Codex writing model without changing the user's Codex default."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from studio.codex_persona import model_metadata  # noqa: E402
from studio.store import Store  # noqa: E402


def configure(state_dir: Path, model: str | None, reasoning: str):
    if model:
        try:
            supported = {
                level["effort"]
                for level in model_metadata(model)["models"][0]["supported_reasoning_levels"]
            }
        except (OSError, ValueError, KeyError, IndexError, StopIteration, TypeError):
            raise ValueError("模型不在本机 Codex 模型清单中。") from None
        if reasoning not in supported:
            raise ValueError("该模型不支持指定的推理强度。")

    store = Store(state_dir)
    backup = store.backup()
    with store.db(True) as connection:
        store.set("codex_writing_model", {"model": model, "reasoning": reasoning} if model else None, connection)
        connection.execute(
            "UPDATE engines SET status='not_verified',checked=0 WHERE id='codex'"
        )
    return backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", nargs="?", help="Studio 写作专用模型名")
    parser.add_argument("--reasoning", default="medium", help="模型推理强度")
    parser.add_argument("--follow-desktop", action="store_true", help="恢复使用本机 Codex 默认模型")
    parser.add_argument("--state-dir", type=Path, default=ROOT / ".local/state")
    args = parser.parse_args()
    if bool(args.model) == args.follow_desktop:
        parser.error("指定模型名，或单独使用 --follow-desktop")
    try:
        backup = configure(args.state_dir, None if args.follow_desktop else args.model, args.reasoning)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps({"backup": str(backup), "model": args.model or "desktop_default", "status": "not_verified"}, ensure_ascii=False))
    print("模型已保存；请在工作室点击“测试连接”，真实调用通过后才能使用。")


if __name__ == "__main__":
    main()
