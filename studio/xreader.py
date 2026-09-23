"""Read-only X boundary: intentionally no post, reply, like or follow methods."""

from __future__ import annotations
import asyncio
import re
import time
from .security import Secrets, sanitize
from .safety import skip_source
from .twikit_compat import CompatibleClient, XCompatibilityError
import httpx


class ReadError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def classify(exc):
    name = type(exc).__name__.lower()
    if isinstance(exc, XCompatibilityError):
        return "client_incompatible"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.TransportError):
        return "network_error"
    if isinstance(exc, (KeyError, IndexError, AttributeError, TypeError)):
        return "client_incompatible"
    if name == "notfound":
        return "client_incompatible"
    if any(x in name for x in ["unauthor", "forbidden", "account", "badrequest"]):
        return "needs_login"
    if "toomany" in name or "ratelimit" in name:
        return "rate_limited"
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "timeout"
    return "source_error"


def get(value, key, default=None):
    return (
        value.get(key, default)
        if isinstance(value, dict)
        else getattr(value, key, default)
    )


def tweet_row(tweet, source, username, own_ids=()):
    # Notifications expose their tweet under .tweet; never persist opaque SDK objects.
    tweet = get(tweet, "tweet") or tweet
    ident = str(get(tweet, "id") or get(tweet, "id_str") or "")
    author = get(tweet, "user") or get(tweet, "author") or {}
    handle = str(get(author, "screen_name") or "")
    text = str(get(tweet, "full_text") or get(tweet, "text") or "")
    if (
        not ident.isdecimal()
        or not re.fullmatch(r"[A-Za-z0-9_]{1,15}", handle)
        or not text
    ):
        return None
    quote = get(tweet, "quote") or get(tweet, "quoted_tweet")
    parent = get(tweet, "in_reply_to") or get(tweet, "in_reply_to_status_id")
    context = {
        "source": source,
        "parent_id": str(parent or ""),
        "quote": {
            "id": str(get(quote, "id") or ""),
            "text": str(get(quote, "full_text") or get(quote, "text") or "")[:6000],
        },
    }
    row = {
        "id": ident,
        "author": handle,
        "text": sanitize(text[:10000]),
        "url": f"https://x.com/{handle}/status/{ident}",
        "kind": "timeline",
        "context": context,
    }
    if handle.lower() == username.lower():
        row["kind"] = "own"
    elif parent and str(parent) in own_ids:
        row["kind"] = "reply"
    elif username.lower() in (context["quote"]["text"].lower()) or (
        quote
        and get(get(quote, "user", {}), "screen_name", "").lower() == username.lower()
    ):
        row["kind"] = "quote"
    elif source == "replies":
        row["kind"] = "reply"
    elif source == "quotes":
        row["kind"] = "quote"
    elif "@" + username.lower() in text.lower():
        row["kind"] = "mention"
    row["reason"] = ""
    observed = text + "\n" + context["quote"]["text"]
    if skip_source(observed):
        row["reason"] = "此内容不适合自动互动，已跳过"
        row["status"] = "skipped"
    else:
        row["status"] = "new"
    return row


class XReader:
    def __init__(self, secrets: Secrets, client=None):
        self.secrets = secrets
        self.client = client

    def connect(self):
        if self.client is not None:
            return self.client
        auth = self.secrets.get("X_AUTH_TOKEN")
        ct0 = self.secrets.get("X_CT0")
        if not auth or not ct0:
            raise ReadError("not_connected")
        self.client = CompatibleClient("en-US")
        self.client.set_cookies({"auth_token": auth, "ct0": ct0})
        return self.client

    async def close(self):
        http = get(self.client, "http")
        if http and hasattr(http, "aclose"):
            await http.aclose()

    async def verify(self, expected=""):
        try:
            user = await asyncio.wait_for(self.connect().user(), 15)
            handle = str(get(user, "screen_name") or "")
            if not handle:
                raise ReadError("invalid_identity")
            if expected and expected.lower().lstrip("@") != handle.lower():
                raise ReadError("identity_mismatch")
            return {
                "id": str(get(user, "id")),
                "username": handle,
                "name": str(get(user, "name") or handle),
            }
        except ReadError:
            raise
        except Exception as exc:
            raise ReadError(classify(exc)) from None

    async def sync(self, username):
        client = self.connect()
        identity = await self.verify(username)
        sources = {}
        posts = {}
        own_ids = set()

        async def read(name, fn):
            try:
                result = await asyncio.wait_for(fn(), 15)
                rows = []
                for tweet in list(result or [])[:20]:
                    row = tweet_row(tweet, name, identity["username"], own_ids)
                    if row:
                        if row["kind"] == "own":
                            own_ids.add(row["id"])
                        rows.append(row)
                for row in rows:
                    posts.setdefault(row["id"], row)
                sources[name] = {"status": "ok", "count": len(rows)}
            except Exception as exc:
                code = classify(exc)
                sources[name] = {"status": code, "count": 0}
                if code == "needs_login":
                    raise ReadError(code) from None

        # Read sources concurrently; each is one page only. Outer timeout bounds the whole round.
        async def all_sources():
            await read(
                "own",
                lambda: client.get_user_tweets(identity["id"], "Tweets", count=20),
            )
            async with asyncio.TaskGroup() as group:
                group.create_task(
                    read(
                        "mentions",
                        lambda: client.search_tweet(
                            "@" + identity["username"], "Latest", count=20
                        ),
                    )
                )
                group.create_task(
                    read(
                        "replies",
                        lambda: client.search_tweet(
                            "to:" + identity["username"], "Latest", count=20
                        ),
                    )
                )
                group.create_task(
                    read(
                        "quotes",
                        lambda: client.search_tweet(
                            "x.com/" + identity["username"] + "/status",
                            "Latest",
                            count=20,
                        ),
                    )
                )
                group.create_task(
                    read(
                        "notifications",
                        lambda: client.get_notifications("Mentions", count=20),
                    )
                )
                group.create_task(
                    read("timeline", lambda: client.get_latest_timeline(count=20))
                )

        try:
            await asyncio.wait_for(
                all_sources(), 40
            )  # verify already used <=15s, entire call <=55s.
        except asyncio.TimeoutError:
            sources["round"] = {"status": "timeout", "count": 0}
        except ExceptionGroup as exc:
            if any(
                isinstance(e, ReadError) and e.code == "needs_login"
                for e in exc.exceptions
            ):
                raise ReadError("needs_login") from None
            sources["round"] = {"status": "source_error", "count": 0}
        # Own-thread context is kept in stored posts; full automatic coverage is never claimed.
        return {
            "identity": identity,
            "posts": list(posts.values()),
            "sources": sources,
            "complete": all(s["status"] == "ok" for s in sources.values()),
        }
