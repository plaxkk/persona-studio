import asyncio
import fcntl
import json
import os
from pathlib import Path
import time
from .store import Store
from .jobs import Jobs
from .telegram import Telegram
from .codex_persona import CodexPersonaRunner


async def serve(store):
    jobs = Jobs(store)
    jobs.recover()
    telegram = Telegram(store, jobs)
    importer = CodexPersonaRunner(store)
    importer.recover()
    importer.imports.cleanup()
    persona = None
    active = None
    tele = None
    last_telegram = 0
    last_cleanup = time.time()
    try:
        while True:
            store.set("worker_heartbeat", int(time.time()))
            jobs.schedule()
            if time.time() - last_cleanup > 3600:
                importer.imports.cleanup()
                last_cleanup = time.time()
            if persona is None or persona.done():
                if persona is not None:
                    await persona
                persona = asyncio.create_task(importer.tick())
            if active is None or active.done():
                if active is not None:
                    await active
                item = jobs.claim()
                active = asyncio.create_task(jobs.run(item)) if item else None
            if time.time() - last_telegram >= 5 and (tele is None or tele.done()):
                if tele is not None:
                    await tele
                tele = asyncio.create_task(telegram.tick())
                last_telegram = time.time()
            await asyncio.sleep(1)
    finally:
        for task in [active, tele, persona]:
            if task:
                task.cancel()
        await asyncio.gather(
            *(t for t in [active, tele, persona] if t), return_exceptions=True
        )
        store.set("worker_heartbeat", 0)


def main():
    os.umask(0o077)
    root = Path(
        os.environ.get(
            "STUDIO_STATE_DIR", Path(__file__).resolve().parents[1] / ".local/state"
        )
    )
    store = Store(root)
    with (root / "worker.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Studio worker already running")
        asyncio.run(serve(store))


if __name__ == "__main__":
    main()
