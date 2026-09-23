# 人格工作室部署与迁移

## 当前部署

本机 macOS 使用 launchd，API 监听 `127.0.0.1:18880`，另一个 worker 执行读取和模型任务。无需 Docker 或额外数据库。旧 `local.x-persona.admin/hermes/shadow` 服务已停止，其 plist 移到 `.local/state/backups/services-*`。新的服务为 `local.persona-studio.api` 和 `local.persona-studio.worker`。

状态目录默认 `.local/state`，目录 0700，凭据 `.env` 0600。数据库 `studio.sqlite3` 保存业务状态，原 `factory.sqlite3` 保留；`backups/` 保存迁移前数据库、配置与人设副本。包含旧凭据的迁移备份也按私密数据保护，不可上传。Web 的“备份”只备份业务数据库，不包含 `.env`。

引擎安装在独立环境中。现有 Hermes 使用已安装的私有 Python 环境；路径通过 `STUDIO_HERMES_PYTHON` / `STUDIO_HERMES_SOURCE`，或已有 `.env` 内 `HERMES_PYTHON` / `HERMES_SOURCE` 定位。OpenClaw 通过 PATH 定位。运行时分别使用 `state/runtimes/hermes` 和 `state/runtimes/openclaw`，不使用个人 HOME、频道配置或个人技能；工具禁用。无需启动 OpenClaw gateway 或 Telegram 通道服务。

OpenClaw 安装可用 `npm install -g openclaw@2026.3.31`；Hermes 采用独立 Git 检出和外置虚拟环境：克隆 `https://github.com/NousResearch/hermes-agent.git`，检出已验收的提交 `6d17b2a59376d64f5ba62cf09ba40ee00d954171`，用 Python 3.11 创建源码目录之外的 venv，再在该环境执行 `pip install -e /absolute/hermes-source`；设置上述两个 `STUDIO_HERMES_*` 路径。旧 `deployment/setup_hermes.py` 属于旧版自动化布局，不用于本产品。本机已安装，无需重新安装。供应商需提供 OpenAI 兼容 Chat Completions 接口；其他协议尚未验收。

## 配置与服务管理

```sh
# 首次安装 / 迁移，始终保持暂停
.venv/bin/python deployment/install_studio.py --port 18880 --state-dir /absolute/private/state
# 本机状态目录默认 .local/state，可省略两个参数

# 只重启服务，不重复执行迁移安装
launchctl kickstart -k gui/$(id -u)/local.persona-studio.api
launchctl kickstart -k gui/$(id -u)/local.persona-studio.worker

# 只读状态诊断
.venv/bin/python deployment/studio_health.py
```

服务日志在状态目录 `logs/`。API 关闭访问日志；供应商原始错误不写入用户日志；页面显示稳定的错误原因。任务表记录状态、错误码和用量。日志需要由部署者定期归档轮转。

Web 创建管理员密码后即可使用，无需管理 token。会话一天过期，HttpOnly / SameSite=Strict，写请求要求 CSRF。所有凭据只接受写入、更换、验证、删除；API 不回显。首次初始化未完成之前，任何能访问本机 loopback 的用户都可能先创建管理员，应在共享机器上及时完成初始化。

## Linux 与远程访问

Linux 使用同一 Python/Node 依赖和 `deployment/install_studio.py --systemd-user`。安装脚本写入用户 systemd units，`UMask=0077`、`NoNewPrivileges=true`、退出自动重启。用户级服务在注销后持续运行需要管理员为该用户启用 linger。Linux 服务模板已实现，当前 macOS 环境不能实机验证 systemd。

```sh
.venv/bin/python deployment/install_studio.py --systemd-user --public-url https://studio.example.com
systemctl --user status persona-studio-api persona-studio-worker
```

API 默认仍绑定 loopback。使用 Nginx/Caddy 终止 HTTPS 并代理到 loopback，保留外部 Host（必须与 `STUDIO_PUBLIC_URL` 一致），支持 SSE 长连接，禁用事件流缓冲。建议仅自己可访问的网络。远程初始化要求状态目录 `setup-token` 内的一次性初始化口令；它不会在日志打印。勿将远程 HTTP 或裸端口暴露到互联网。

环境变量：`STUDIO_STATE_DIR`、`STUDIO_PORT`、`STUDIO_PUBLIC_URL`、`STUDIO_HOST`；只有显式 HTTPS public URL 时允许非 loopback 监听。数据库与人设是单账号设计；已有已验证账号不能直接换为另一账号，使用新的状态目录。

## 备份与恢复

迁移在旧服务停止后通过 SQLite backup API 制作一致性快照，再导入人设、语料、记忆、审计和待审内容。旧动作不会推断为已发布；缺少原帖链接的回复草稿不提供回复跳转。审计中旧发送字段仅作为历史来源保存。旧服务 plist 从 LaunchAgents 移走，避免登录时重新启动。

恢复步骤：停止两个新服务；复制指定备份数据库为 `studio.sqlite3`（先另行保留当前数据，确保没有连接后清理对应 WAL/SHM）；恢复所需的 `.env`，设置目录 0700、文件 0600；重新启动 API，设置暂停后再启动 worker。不要重新启用旧发送服务作为常规恢复办法。

队列重启：未执行任务保留；执行中的只读同步可重新排队；可能已收费的模型任务标为中断，需手动重试，避免隐式重复计费。用户修改的草稿通过版本号保护，后台重生成冲突时结果留在任务记录，不能覆盖手工修改。

## 验证边界

所有测试夹具都使用临时目录，不向生产数据库插入示例。真实账号连接、供应商模型、X 验证挑战与 Telegram 通知仍需用户提供凭据后验收；界面不会提前显示已就绪。模拟浏览器测试默认使用本机 Chrome，也可通过 `STUDIO_CHROME` 指定本机 Chrome/Chromium 路径。
