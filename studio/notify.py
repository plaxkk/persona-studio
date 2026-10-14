"""把一条已经写下的事实送到主人的两个地方：Telegram 与 macOS 通知。

去重借 `notices` 表本来就在做的事（`telegram.py` 用它给草稿和聊天回复做
at-most-once）：先 `INSERT OR IGNORE` 抢下这条通知的键，抢不到就说明发过或正在发，
直接返回。抢到之后**不重发**——发送结果不确定时宁可不重发，也不要同一条事件发两遍。
代价是「抢到了但没送出去」会永远落空，所以结果原样写回那份记录，页面上看得见
（`notify_status` / `notify_note`）。「没配 Telegram」和「发了但失败」也是两句话。
"""

import asyncio
import shutil
import sys
import time

TITLE = "人格工作室"
# macOS 通知的正文放不下这么多字，截断而不是让系统自己悄悄截。
BODY = 500

# 用 `on run argv` 收参数，而不是把正文拼进 AppleScript 源码里：拼字符串的话，
# 上游的提交说明里只要有一个引号就能改掉这段脚本。argv 不给它这个机会。
SCRIPT = ("on run argv\n"
          "display notification (item 1 of argv) with title (item 2 of argv)\n"
          "end run")


class Notifier:
    def __init__(self, store, telegram=None):
        self.store = store
        # 由 worker 在拿到那唯一一个 Telegram 客户端之后补上；这里不自己造一个，
        # 免得同一个进程里有两条通往 Telegram 的路。
        self.telegram = telegram

    def claim(self, key):
        """抢下这条通知。抢不到就是已经发过了。"""
        with self.store.db(True) as c:
            return bool(c.execute(
                "INSERT OR IGNORE INTO notices VALUES(?,?,?)",
                (key, "attempted", int(time.time()))).rowcount)

    def settle(self, key, status):
        with self.store.db() as c:
            c.execute("UPDATE notices SET status=? WHERE draft_id=?", (status, key))

    async def desktop(self, title, text):
        if sys.platform != "darwin" or not shutil.which("osascript"):
            return "skipped"
        try:
            process = await asyncio.create_subprocess_exec(
                "osascript", "-e", SCRIPT, "--", text[:BODY], title,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL)
            await asyncio.wait_for(process.wait(), timeout=10)
        except (OSError, asyncio.TimeoutError):
            return "failed"
        return "sent" if process.returncode == 0 else "failed"

    async def send(self, key, text, title=None):
        title = title or TITLE
        if not self.claim(key):
            return {"status": "already", "telegram": "already", "desktop": "already"}
        telegram = "not_configured"
        if (self.telegram is not None and self.store.get("telegram_enabled")
                and self.store.get("owner_id")):
            try:
                await self.telegram.send(f"{title}\n{text}")
                telegram = "sent"
            except Exception as exc:
                telegram = f"failed:{type(exc).__name__}"
        desktop = await self.desktop(title, text)
        if "sent" in (telegram, desktop):
            status = "sent"
        elif telegram == "not_configured" and desktop == "skipped":
            status = "not_configured"
        else:
            status = "failed"
        self.settle(key, status)
        return {"status": status, "telegram": telegram, "desktop": desktop}
