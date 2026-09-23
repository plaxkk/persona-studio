"""Opt-in real model test against synthetic MCP state; consumes model quota."""

import sys, asyncio, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio.store import Store
from studio.codex_persona import CodexPersonaRunner, preflight


async def main():
    c = preflight()
    assert c["ready"]
    with tempfile.TemporaryDirectory() as t:
        runner = CodexPersonaRunner(Store(Path(t)))
        runner.imports.presence("1.3.0")
        ident = runner.imports.create("example", 20, c["model"], c["reasoning"])
        with runner.store.db() as db:
            db.execute(
                "UPDATE persona_imports SET status='collecting' WHERE id=?", (ident,)
            )

        async def heartbeat():
            while True:
                runner.imports.presence("1.3.0")
                await asyncio.sleep(10)

        heart = asyncio.create_task(heartbeat())
        try:
            result = await asyncio.wait_for(
                runner.execute(
                    runner.imports.get(ident),
                    'Synthetic protocol integration test only. Call the persona progress tool once, then the persona finish tool. Do not browse. Then return {"finished":true}.',
                    {
                        "type": "object",
                        "properties": {"finished": {"type": "boolean"}},
                        "required": ["finished"],
                        "additionalProperties": False,
                    },
                    True,
                ),
                90,
            )
            assert (
                result["finished"] and runner.imports.get(ident)["phase"] == "analyze"
            )
            print(
                "PASS real Codex -> task-bound MCP -> persisted state transition; no real X data"
            )
        finally:
            heart.cancel()


asyncio.run(main())
