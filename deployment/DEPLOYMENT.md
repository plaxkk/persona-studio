# 本机 Hermes shadow 部署

部署位置：`/Users/kk/repos/x-auto`。上游源码 HEAD：`7ff69fe409e75341ef987aaf45b7f4d91f4d09c3`。

当前选择 Intel macOS 原生 Python/Node + 用户级 launchd。未提供远程服务器，Docker 未安装，systemd 不适用于 macOS。服务在用户登录后启动；电脑睡眠或关机期间不能保证持续在线。需要 24 小时运行时，下一步迁移至 Linux VPS。

上游项目原生依赖 OpenClaw。本部署新增 Hermes 适配，而非宣称上游原生支持 Hermes。复用本机已安装 Hermes Python 3.11 环境，固定当前源码提交 `6d17b2a59376d64f5ba62cf09ba40ee00d954171`，不更改个人 Hermes 配置。OpenClaw 已安装，但本 profile 不启动它，也不创建第二个 Telegram poller。

项目服务使用 Python 3.13.5 隔离环境 `.venv`，前端使用 Node 24.16.0。Python 依赖记录于 `deployment/requirements.lock`，前端使用原有 npm 锁文件。

## 已运行的组件

- `local.x-persona.admin`：Admin API 和已构建的 Web UI，监听 `127.0.0.1:18880`。
- `local.x-persona.hermes`：独立 Telegram bridge。业务凭据缺失时驻留等待，不发起 Telegram 请求。仅接受 owner 数字 ID 对应的私人消息，拒绝群聊和其他发送者。
- `local.x-persona.shadow`：每 30 分钟运行一次，当前因 pause 跳过。补齐模型并解除暂停后可生成人设原创草稿、执行风格检查、限流、审计并加入待审队列。适配器固定 dry-run。
- Hermes 状态位于 `.local/hermes`，工厂状态位于 `.local/state`，日志位于 `.local/logs`。

安全状态为 `shadow_mode=true`、`pause_all=true`。Web/API 关闭 shadow 会返回 409，必须由部署代理在 owner 确认后修改部署锁。没有开启任何真实 X 写操作。Telegram `/pause` 和 `/status` 是明确的控制命令；没有开放通用 shell 或账户工具。

当前定时服务仅接入原创 shadow 草稿。刷推、回复、点赞、转帖、引用、关注的上游 runner 已通过离线 fixture 和 dry-run 测试；真实账号扫描、Hermes 上下文判断与上述动作的定时联动仍待凭据补齐后的集成验收，尚未宣称完成全天候自动运营。Live limited 尚未启用。

## 凭据入口

双击 `deployment/credentials.command`：调用原项目 `scripts/credential_helper.py`，隐藏输入 Telegram token、模型 key、X cookies，保存到 `.local/state/.env`（0600）。随后收集 owner ID、模型 ID、HTTPS Base URL 和 X 用户名，也可导入你手动导出的 Cookie-Editor JSON。

不要将密钥贴进聊天、命令参数、Git 或截图。不会读取浏览器 cookie 数据库。管理 token 已生成并保存在 `.env`；双击 `deployment/open_admin.command` 可将其复制到剪贴板并打开管理台，粘贴到右上角密码框即可。使用完毕后清理剪贴板。

需要提供：Telegram Bot Token、owner Telegram 数字 ID、模型 API Key/模型 ID/Base URL、X auth_token/ct0/X 用户名。无需本机密码。仅当选择远程 24 小时部署时才需服务器地址、SSH 用户和密钥登录方式。

填完后回到 Codex 通知“凭据已填好”。不要直接解除暂停：先由部署代理验证凭据、确认唯一 poller、启动 owner 回声验收和非 owner 拒绝验收，再验收真实账号的只读扫描与 shadow 草稿。

## 人设

当前是明确标注的临时合成 AI 人格，经 `scripts/persona_distill.py` 从 12 条人工合成示例生成，不模仿真人。

正式输入放入 `.local/inputs/`：UTF-8 TXT/Markdown prompt、JSON/JSONL（至少 `text`，可加 `author`、`created_at`）、CSV 或导出的聊天文本。说明名字、语言、语气、话题、禁区，以及真人语料的授权或明确合成标识。原始私密语料留在输入目录，不提交 Git。

## 验证与限制

本地测试包括 owner 边界、鉴权、禁止 live、pause/read-only、限流、审计、SQLite FTS、六种 X 写操作的 dry-run、浏览候选的 shadow 审计、prompt injection 跳过和 shadow 草稿链路。

真实 Hermes 调用本地模拟模型已成功，确认发送给模型的 tools 为空。浏览器检查使用独立临时 Chrome profile，未读取用户浏览器数据。截图 `.local/web-admin.png`，脱敏健康报告 `.local/verification.json`。

尚未验证：真实 Telegram 收发、模型提供方连通性、X 登录身份与读取能力、真人设风格变异、真实自主动作。缺少凭据不能把这些标为通过。

可重复检查：

```sh
.venv/bin/python -m pytest tests -q
.venv/bin/python scripts/health_check.py --runtime hermes --state-dir .local/state
.venv/bin/python deployment/verify_hermes.py
.venv/bin/python deployment/verify_web.py
```

建议首次 live limited 上限：原创 2/日、点赞 5/日、转帖 2/日、引用 1/日、关注 1/日、回复 2/小时；浏览间隔至少 30 分钟。这些只是已保存的上限，不能代替 owner 的启用确认。所有验证通过后，另行确认允许的动作和强度。

停止服务：`launchctl bootout gui/$(id -u)/local.x-persona.admin`，同样可停止 `.hermes` 和 `.shadow`。不要删除状态目录；保留审计与回滚证据。
