"""Optional single-owner remote. No social writes; notifications are at-most-once."""

import asyncio
import json
import time
import httpx
from .security import Secrets, sanitize
from .jobs import JobError


def owner_allowed(message, owner):
    return bool(
        owner
        and owner.isdecimal()
        and message.get("chat", {}).get("type") == "private"
        and str(message.get("chat", {}).get("id")) == owner
        and str(message.get("from", {}).get("id")) == owner
        and not message.get("from", {}).get("is_bot")
    )


class Telegram:
    def __init__(self, store, jobs):
        self.store = store
        self.jobs = jobs
        self.vault = Secrets(store.root)
        self.checked = ""
        self.last_notice = 0
        self.retry_at = 0

    async def call(self, method, payload=None):
        token = self.vault.get("TELEGRAM_BOT_TOKEN")
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"https://api.telegram.org/bot{token}/{method}", json=payload or {}
            )
            if response.status_code != 200:
                raise RuntimeError("telegram_unavailable")
            data = response.json()
            if not data.get("ok"):
                raise RuntimeError("telegram_unavailable")
            return data.get("result")

    async def send(self, text):
        await self.call(
            "sendMessage", {"chat_id": self.store.get("owner_id"), "text": text[:3900]}
        )

    async def handle(self, update):
        ident = int(update["update_id"])
        message = update.get("message") or {}
        owner = self.store.get("owner_id")
        with self.store.db(True) as c:
            if not c.execute(
                "INSERT OR IGNORE INTO telegram_updates VALUES(?,?)",
                (ident, int(time.time())),
            ).rowcount:
                return
            self.store.set("telegram_offset", ident + 1, c)
        if not owner_allowed(message, owner):
            self.store.event("telegram", "已拒绝非 owner 或群聊消息")
            return
        text = str(message.get("text", "")).strip()
        if not text:
            return
        if text == "/pause":
            with self.store.db(True) as c:
                self.store.set("paused", True, c)
                c.execute(
                    "UPDATE tasks SET cancel=1,status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END WHERE status IN ('running','queued') AND kind NOT IN ('chat','probe')"
                )
            await self.send("已暂停同步和自动生成。你仍可试聊，恢复请到 Web 工作室。")
        elif text in ["/status", "/start"]:
            await self.send(
                ("当前已暂停" if self.store.get("paused") else "同步与生成已开启")
                + "。所有 X 操作仍需你亲自完成。\n/pause 暂停\n/drafts 查看草稿\n其他文字可用于人设试聊。"
            )
        elif text == "/drafts":
            rows = self.store.rows(
                "SELECT id,text FROM drafts WHERE status='draft' ORDER BY created DESC LIMIT 3"
            )
            await self.send(
                "\n\n".join("草稿：" + r["text"][:850] for r in rows)
                or "暂时没有待处理草稿。"
            )
        else:
            try:
                self.jobs.enqueue(
                    "chat",
                    {
                        "text": sanitize(text, self.vault.all().values()),
                        "channel": "telegram",
                    },
                    dedupe="telegram-chat:" + str(ident),
                )
            except JobError:
                await self.send("写作引擎还没有准备好，请在 Web 设置中完成连接测试。")

    async def tick(self):
        if not self.store.get("telegram_enabled") or time.time() < self.retry_at:
            return
        owner = self.store.get("owner_id")
        token = self.vault.get("TELEGRAM_BOT_TOKEN")
        if not owner or not token:
            self.store.set("telegram_status", "not_configured")
            return
        try:
            identity = token + ":" + owner
            if self.checked != identity:
                await self.call("getMe")
                hook = await self.call("getWebhookInfo")
                if hook.get("url"):
                    self.store.set("telegram_status", "webhook_conflict")
                    return
                self.checked = identity
                self.store.set("telegram_status", "ready")
            updates = await self.call(
                "getUpdates",
                {
                    "offset": self.store.get("telegram_offset", 0),
                    "timeout": 0,
                    "allowed_updates": ["message"],
                },
            )
            self.store.set("telegram_status", "ready")
            for update in updates:
                await self.handle(update)
            rows = self.store.rows(
                "SELECT * FROM tasks WHERE kind='chat' AND status='succeeded' ORDER BY created DESC LIMIT 20"
            )
            for row in rows:
                payload = json.loads(row["payload"])
                if payload.get("channel") != "telegram":
                    continue
                key = "chat:" + row["id"]
                with self.store.db(True) as c:
                    inserted = c.execute(
                        "INSERT OR IGNORE INTO notices VALUES(?,?,?)",
                        (key, "attempted", int(time.time())),
                    ).rowcount
                if inserted:
                    await self.send(
                        json.loads(row["result"]).get("text", "未能生成回复")
                    )
                    with self.store.db() as c:
                        c.execute(
                            "UPDATE notices SET status='sent' WHERE draft_id=?", (key,)
                        )
            if time.time() - self.last_notice > 60:
                drafts = self.store.rows(
                    "SELECT id,text FROM drafts WHERE status='draft' ORDER BY created DESC LIMIT 3"
                )
                for draft in drafts:
                    with self.store.db(True) as c:
                        inserted = c.execute(
                            "INSERT OR IGNORE INTO notices VALUES(?,?,?)",
                            (draft["id"], "attempted", int(time.time())),
                        ).rowcount
                    if inserted:
                        await self.send(
                            "新草稿待审阅（未发布）：\n"
                            + draft["text"][:1600]
                            + "\n\n请到 Web 工作室编辑或复制，再去 X 完成。"
                        )
                        with self.store.db() as c:
                            c.execute(
                                "UPDATE notices SET status='sent' WHERE draft_id=?",
                                (draft["id"],),
                            )
                self.last_notice = time.time()
        except Exception:
            self.retry_at = time.time() + 60
            self.store.set("telegram_status", "connection_error")
            self.store.event(
                "telegram",
                "Telegram 连接异常，请检查配置。通知发送结果不确定时不会自动重发。",
            )
