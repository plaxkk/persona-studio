# 人格工作室

面向个人用户的自托管 X 内容工作台：**自动读取互动 → 人格判断和写作 → 你修改 → 复制并去 X → 你完成操作。**

一个独立虚构人格、一个 X 账号。中文优先，支持 OpenClaw / Hermes 写作引擎，Telegram 私人遥控器可选。Codex 和 Claude Code 仅预留适配位置。

本版没有 X 发布接口，不自动发帖、回复、点赞、转帖或关注，不包含远程浏览器控制。点击跳转只记录“已打开 X”；“我已完成”是用户确认，不是平台验证。原创与回复均采用复制后打开页面，文案不会自动进入 URL。

## 使用

打开本机 [人格工作室](http://127.0.0.1:18880)，首次设置管理员密码。

1. 在“我的人格”填写名字、虚构身份、语气、兴趣和禁区；可导入 TXT / Markdown / JSON / JSONL / CSV，内部调用 `scripts/persona_distill.py` 净化与蒸馏。导入文件上限 1 MB。
2. 在“设置 → 写作引擎”配置 Hermes 或 OpenClaw 的模型名称、OpenAI 兼容接口地址与 API Key，然后测试连接。只有实际调用成功的引擎才可选择。
3. 在 Chrome / Edge 安装并授权 [浏览器连接助手](browser-extension/README.md)，之后打开工作室会自动连接当前 X 账号；未登录时引导登录，登录完成自动验证并填入用户名。也可在“设置 → 账号连接”手动提供 `auth_token` / `ct0` 或 Cookie-Editor JSON。产品不读取浏览器 cookie 数据库。
4. 首次启动保持暂停，连接检查后从首页开始运行。未连接 Telegram 也可完成全部 Web 流程。
5. 在收件箱处理互动或写原创，编辑后的草稿自动保存。去 X 前核对浏览器当前登录账号，复制失败可手动复制，弹窗被拦可使用普通链接。

X cookies 本身有写入权限；本产品通过读取模块隔离、不暴露写入方法、禁用模型工具限制行为，不能把它们称为只读凭据。X 搜索和通知不保证完整覆盖互动，界面会显示各来源成功时间与失败状态。

## 在线面板连接本机

访问 https://persona-studio-plaxkk.vercel.app，安装连接助手 1.1 并在扩展内授权本机管理员账号。网页托管于 Vercel，Agent、数据库和凭据继续留在你的电脑。详见 [在线面板与本机连接说明](docs/DESKTOP_WEB.md)。

## 本机安装与部署

Python 3.11+、Node.js 22+；建议使用 Python 3.13、Node.js 24。SQLite 随 Python 提供，不需要 Docker。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r deployment/requirements.lock
npm --prefix assets/web-admin ci
npm --prefix assets/web-admin run build
.venv/bin/python deployment/install_studio.py
```

macOS 安装两个 launchd 服务；Linux 使用 `--systemd-user`。引擎需单独安装，详见 [部署说明](docs/DEPLOYMENT.md)。支持自定义端口与状态目录；远程部署必须使用 HTTPS 反向代理。当前本机已经安装引擎与运行服务，无需重复执行上面的命令。

安全配置入口是 Web 设置页，或双击 `deployment/credentials.command`。所有密钥通过现有 `credential_helper.py` 的读写能力保存到 `0600` 的 `.env`，不会回显。不要把凭据发到聊天或提交到 Git。

## 架构与验证

FastAPI + React / TypeScript + SQLite 的模块化单体。API `/api/v1`，管理员 HttpOnly 会话与 CSRF 保护；SQLite 持久任务队列、任务取消、去重、有限重试、草稿版本与人工编辑保护。人设、记忆、反馈、会话摘录与审计持久化。引擎子进程独立 HOME、禁用工具，只接收对应模型密钥。

默认每 30 分钟同步，手动刷新至少相隔 60 秒；每来源一页、最多 20 条，单来源 15 秒，整轮最多 55 秒。每天自动生成至多 12 条互动草稿、2 条原创草稿，每轮至多 3 条互动草稿。手动生成单独计数；这是生成预算，不是发送配额。

```sh
.venv/bin/python -m pytest tests -q
npm --prefix assets/web-admin run build
.venv/bin/python deployment/verify_studio_engines.py
.venv/bin/python deployment/verify_studio_web.py
.venv/bin/python deployment/studio_health.py
```

双引擎脚本使用真实本机引擎与本地模拟模型；浏览器验收在临时数据库中进行，使用 Chrome。二者都不验证真实 X / Telegram / 模型供应商账号。

- [架构审查与验收项](docs/ARCHITECTURE_REVIEW.md)
- [部署、迁移、备份与恢复](docs/DEPLOYMENT.md)
- [验收结果与尚待真实连接验证的范围](docs/ACCEPTANCE.md)

## 来源与许可证

基于 [NoMTF/Another-Person-in-X](https://github.com/NoMTF/Another-Person-in-X)，保留 [MIT 许可证](LICENSE)、原作者版权及 [上游 README 快照](docs/upstream/README.md)。上游提交：`7ff69fe409e75341ef987aaf45b7f4d91f4d09c3`。

旧版 `scripts/automation_runner.py`、发布脚本、旧 Admin API 与自动化部署文件作为兼容和来源历史保留；**不属于新版生产入口**。新版仅运行 `python -m studio api` 与 `python -m studio worker`。不要使用旧版全自动部署流程启动本工作室。
