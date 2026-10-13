"""Read health independently from scheduler/engine activity."""
import json
import time

SOURCES = ("own", "mentions", "replies", "quotes", "notifications", "timeline")


def summary(store, now=None):
    now = int(time.time()) if now is None else now
    recorded = {r['name']: r for r in store.rows("SELECT * FROM sources")}
    sources = [recorded.get(name, {'name': name, 'status': 'not_attempted', 'last_success': 0,
                                   'last_attempt': 0, 'count': 0}) for name in SOURCES]
    interval = store.get("sync_interval") * 2
    for source in sources:
        source["stale"] = not source["last_success"] or now - source["last_success"] > interval
    recent = store.rows("SELECT status,error,result,updated FROM tasks WHERE kind='sync' ORDER BY created DESC,rowid DESC LIMIT 1")
    if recent and recent[0]['status'] == 'succeeded':
        try:
            if json.loads(recent[0]['result']).get('complete') is False:
                recent[0]['status'] = 'partial'
        except (TypeError, ValueError):
            pass
    return {"sources": sources, "latest": recent[0] if recent else None,
            "stale": any(s["stale"] for s in sources),
            "last_complete": store.get("last_complete_sync", 0)}


def failed(store, code):
    now = int(time.time())
    with store.db(True) as c:
        for name in SOURCES:
            c.execute("""INSERT INTO sources(name,status,last_attempt,count) VALUES(?,?,?,0)
              ON CONFLICT(name) DO UPDATE SET status=excluded.status,last_attempt=excluded.last_attempt,count=0""", (name, code, now))
