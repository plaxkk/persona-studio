"""Retryable, server-only Supabase delivery for staged Analytics reads."""

from __future__ import annotations

import json
import re
import time
from urllib.parse import urlsplit

import httpx
from .security import Secrets


class SupabaseConfigError(ValueError):
    pass


def _config(environ):
    url = (environ.get("SUPABASE_URL") or "").rstrip("/")
    key = environ.get("SUPABASE_SECRET_KEY") or ""
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise SupabaseConfigError("supabase_not_configured") from exc
    if not (parsed.scheme == "https" and parsed.hostname
            and re.fullmatch(r"[a-z0-9-]+\.supabase\.co", parsed.hostname)
            and not parsed.username and not parsed.password
            and port in (None, 443)
            and not parsed.path and not parsed.query and not parsed.fragment
            and key.startswith("sb_secret_") and len(key) > 20
            and not any(char.isspace() for char in key)):
        raise SupabaseConfigError("supabase_not_configured")
    return url, key


def config_for_store(store):
    """Read the local-only vault configured through the studio API."""
    vault = Secrets(store.root)
    return {
        "SUPABASE_URL": store.get("supabase_url"),
        "SUPABASE_SECRET_KEY": vault.get("SUPABASE_SECRET_KEY"),
    }


def configured(store):
    try:
        _config(config_for_store(store))
        return True
    except SupabaseConfigError:
        return False


def _post(client, url, key, table, conflict, rows):
    response = client.post(
        f"{url}/rest/v1/{table}",
        params={"on_conflict": conflict},
        headers={"apikey": key, "Prefer": "resolution=merge-duplicates,return=minimal"},
        json=rows,
    )
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"http_{response.status_code}")


def sync_one_content_run(store, *, environ=None, client=None, now=None):
    """Deliver one pending run; leave it retryable until all remote writes pass.

    A retry repeats deterministic upserts keyed by run ID and post ID. No
    credential is stored in SQLite or returned to callers.
    """
    try:
        url, key = _config(config_for_store(store) if environ is None else environ)
    except SupabaseConfigError:
        return {"status": "not_configured"}
    now = int(time.time()) if now is None else int(now)
    with store.db(True) as db:
        row = db.execute(
            "SELECT * FROM analytics_runs WHERE "
            "(cloud_status IN ('pending','retry') OR "
            "(cloud_status='sending' AND cloud_retry_at<=?)) "
            "AND cloud_retry_at<=? ORDER BY observed_at,id LIMIT 1",
            (now, now),
        ).fetchone()
        if not row:
            return {"status": "idle"}
        run = dict(row)
        db.execute("UPDATE analytics_runs SET cloud_status='sending',"
                   "cloud_retry_at=? WHERE id=?", (now + 600, run["id"]))
    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=20, follow_redirects=False)
    try:
        gaps = store.rows("SELECT item_index,post_id,reason FROM analytics_coverage_gaps "
                          "WHERE run_id=? ORDER BY item_index", (run["id"],))
        posts = store.rows("SELECT post_id,published_at_ms,text,metrics_json "
                           "FROM analytics_post_metrics WHERE run_id=? ORDER BY post_id", (run["id"],))
        remote_run = {
            "id": run["id"], "account": run["account"], "source": run["source"],
            "window_from": run["window_from"], "window_to": run["window_to"],
            "observed_at": run["observed_at"], "request_limit": run["request_limit"],
            "received_count": run["received_count"], "parsed_count": run["parsed_count"],
            "gap_count": run["gap_count"], "at_request_limit": bool(run["at_request_limit"]),
            "parser_version": run["parser_version"],
            "raw_payload": json.loads(run["raw_json"]), "coverage_gaps": gaps,
        }
        _post(client, url, key, "persona_analytics_runs", "id", [remote_run])
        for start in range(0, len(posts), 100):
            batch = [{"run_id": run["id"], "account": run["account"],
                      "post_id": item["post_id"], "published_at_ms": item["published_at_ms"],
                      "body_text": item["text"], "metrics": json.loads(item["metrics_json"]),
                      "observed_at": run["observed_at"]}
                     for item in posts[start:start + 100]]
            _post(client, url, key, "persona_analytics_post_metrics", "run_id,post_id", batch)
    except (httpx.HTTPError, RuntimeError, ValueError, TypeError) as exc:
        code = str(exc) if isinstance(exc, RuntimeError) and re.fullmatch(r"http_\d{3}", str(exc)) else "network_or_data_error"
        attempts = run["cloud_attempts"] + 1
        retry_at = now + min(3600, 30 * 2 ** min(attempts - 1, 7))
        with store.db(True) as db:
            db.execute("UPDATE analytics_runs SET cloud_status='retry',cloud_attempts=?,"
                       "cloud_retry_at=?,cloud_error=? WHERE id=?",
                       (attempts, retry_at, code, run["id"]))
        return {"status": "retry", "run_id": run["id"], "error": code,
                "retry_at": retry_at}
    finally:
        if own_client:
            client.close()
    with store.db(True) as db:
        db.execute("UPDATE analytics_runs SET cloud_status='synced',cloud_synced_at=?,"
                   "cloud_retry_at=0,cloud_error='' WHERE id=?", (now, run["id"]))
    return {"status": "synced", "run_id": run["id"], "post_count": len(posts),
            "gap_count": len(gaps)}


def sync_one_overview_run(store, *, environ=None, client=None, now=None):
    """Upsert one Overview run and its separate time series; retry partial delivery."""
    try:
        url, key = _config(config_for_store(store) if environ is None else environ)
    except SupabaseConfigError:
        return {"status": "not_configured"}
    now = int(time.time()) if now is None else int(now)
    with store.db(True) as db:
        row = db.execute(
            "SELECT * FROM analytics_overview_runs WHERE "
            "(cloud_status IN ('pending','retry') OR "
            "(cloud_status='sending' AND cloud_retry_at<=?)) "
            "AND cloud_retry_at<=? ORDER BY observed_at,id LIMIT 1",
            (now, now),
        ).fetchone()
        if not row:
            return {"status": "idle"}
        run = dict(row)
        db.execute("UPDATE analytics_overview_runs SET cloud_status='sending',"
                   "cloud_retry_at=? WHERE id=?", (now + 600, run["id"]))
    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=20, follow_redirects=False)
    try:
        gaps = store.rows("SELECT series,item_index,metric_index,reason "
                          "FROM analytics_overview_gaps WHERE run_id=? "
                          "ORDER BY series,item_index,metric_index", (run["id"],))
        points = store.rows("SELECT series,item_index,metric_index,timestamp_ms,"
                            "metric_type,count,verified FROM analytics_overview_points "
                            "WHERE run_id=? ORDER BY series,item_index,metric_index", (run["id"],))
        remote_run = {
            "id": run["id"], "account": run["account"],
            "window_from": run["window_from"], "window_to": run["window_to"],
            "previous_from": run["previous_from"], "previous_to": run["previous_to"],
            "observed_at": run["observed_at"], "point_count": run["point_count"],
            "gap_count": run["gap_count"], "parser_version": run["parser_version"],
            "scalars": json.loads(run["scalars_json"]),
            "raw_payload": json.loads(run["raw_json"]), "coverage_gaps": gaps,
        }
        _post(client, url, key, "persona_analytics_overview_runs", "id", [remote_run])
        for start in range(0, len(points), 100):
            batch = [{"run_id": run["id"], **{k: v for k, v in point.items()
                     if k != "verified"},
                      "verified": None if point["verified"] is None
                      else bool(point["verified"])}
                     for point in points[start:start + 100]]
            _post(client, url, key, "persona_analytics_overview_points",
                  "run_id,series,item_index,metric_index", batch)
    except (httpx.HTTPError, RuntimeError, ValueError, TypeError) as exc:
        code = str(exc) if isinstance(exc, RuntimeError) and re.fullmatch(r"http_\d{3}", str(exc)) else "network_or_data_error"
        attempts = run["cloud_attempts"] + 1
        retry_at = now + min(3600, 30 * 2 ** min(attempts - 1, 7))
        with store.db(True) as db:
            db.execute("UPDATE analytics_overview_runs SET cloud_status='retry',"
                       "cloud_attempts=?,cloud_retry_at=?,cloud_error=? WHERE id=?",
                       (attempts, retry_at, code, run["id"]))
        return {"status": "retry", "run_id": run["id"], "error": code,
                "retry_at": retry_at}
    finally:
        if own_client:
            client.close()
    with store.db(True) as db:
        db.execute("UPDATE analytics_overview_runs SET cloud_status='synced',"
                   "cloud_synced_at=?,cloud_retry_at=0,cloud_error='' WHERE id=?",
                   (now, run["id"]))
    return {"status": "synced", "run_id": run["id"], "point_count": len(points),
            "gap_count": len(gaps)}


def sync_one_growth_snapshot(store, *, kind="rewards", environ=None, client=None, now=None):
    """Mirror one observed growth snapshot; retry without blocking local plans."""
    if kind not in ("rewards", "followers"):
        raise ValueError("unknown_growth_snapshot")
    try:
        url, key = _config(config_for_store(store) if environ is None else environ)
    except SupabaseConfigError:
        return {"status": "not_configured"}
    now = int(time.time()) if now is None else int(now)
    local, remote, ident = (
        ("growth_reward_snapshots", "persona_growth_reward_snapshots", "id") if kind == "rewards"
        else ("growth_follower_reads", "persona_growth_follower_reads", "task_id"))
    with store.db(True) as db:
        row = db.execute(
            f"SELECT * FROM {local} WHERE (cloud_status IN ('pending','retry') "
            "OR (cloud_status='sending' AND cloud_retry_at<=?)) "
            "AND cloud_retry_at<=? ORDER BY observed_at LIMIT 1", (now, now)).fetchone()
        if not row:
            return {"status": "idle"}
        item = dict(row)
        db.execute(f"UPDATE {local} SET cloud_status='sending',cloud_retry_at=? WHERE {ident}=?",
                   (now + 600, item[ident]))
    if kind == "rewards":
        payload = {"id": item["id"], "account": item["account"],
                   "observed_at": item["observed_at"], "page_status": item["page_status"],
                   "period": item["period"], "metrics": json.loads(item["metrics_json"]),
                   "raw_payload": json.loads(item["raw_json"]), "source": item["source"]}
    else:
        payload = {"task_id": item["task_id"], "account": item["account"],
                   "observed_at": item["observed_at"], "discovered": item["discovered"],
                   "read_count": item["read_count"], "stopped_reason": item["stopped_reason"],
                   "cumulative_bands": json.loads(item["bands_json"]),
                   "exact_count": item["exact_count"],
                   "verified_observed": item["verified_observed"]}
    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=20, follow_redirects=False)
    try:
        _post(client, url, key, remote, ident, [payload])
    except (httpx.HTTPError, RuntimeError, ValueError, TypeError) as exc:
        code = str(exc) if isinstance(exc, RuntimeError) and re.fullmatch(r"http_\d{3}", str(exc)) else "network_or_data_error"
        attempts = item["cloud_attempts"] + 1
        retry_at = now + min(3600, 30 * 2 ** min(attempts - 1, 7))
        with store.db(True) as db:
            db.execute(f"UPDATE {local} SET cloud_status='retry',cloud_attempts=?,cloud_retry_at=?,cloud_error=? WHERE {ident}=?",
                       (attempts, retry_at, code, item[ident]))
        return {"status": "retry", "kind": kind, "error": code, "retry_at": retry_at}
    finally:
        if own_client:
            client.close()
    with store.db(True) as db:
        db.execute(f"UPDATE {local} SET cloud_status='synced',cloud_synced_at=?,cloud_retry_at=0,cloud_error='' WHERE {ident}=?",
                   (now, item[ident]))
    return {"status": "synced", "kind": kind, "id": item[ident]}
