# 在线面板，本机执行

正式入口：https://persona-studio-plaxkk.vercel.app

Vercel 仅托管静态 React 页面和连接助手 ZIP。网页通过 Chrome/Edge 扩展向同一台电脑的 loopback API 发请求，不经过云端 API、中转服务器或云端数据库。SQLite、人设、记忆、X cookies、Telegram 凭据、模型 API key 和引擎进程保留在本机。草稿及会话内容会显示在已授权网页的浏览器内存中；不应把“本机存储”理解为网页不能看到显示内容。

## 首次连接

1. 启动本机工作室，确认 http://127.0.0.1:18880 可打开并已设置管理员密码。
2. 在 Chrome / Edge 安装连接助手 1.3.4，旧版需更新文件并在扩展管理页点“重新加载”。扩展 ID 保持不变。
3. 首次安装执行 `.venv/bin/python deployment/install_browser_connector.py`（自定义状态目录/端口使用 `--state` / `--port`）。从浏览器工具栏打开连接助手，点击“授权并打开在线工作室”，无需密码。
4. 扩展打开正式网站。网页检测到授权后显示本机工作室。

本机连接程序通过 Chrome Native Messaging 在当前操作系统用户下签发设备会话，仅允许固定扩展 ID 调用，不提供密码免验 HTTP 登录。设备会话令牌仅保存在扩展 `storage.session`，不返回网页，不写 URL、日志或磁盘配置。授权最长 24 小时，浏览器关闭后需重新授权；新授权使旧设备会话失效。页面“断开”、本机账号退出对应设备会话及扩展“断开在线面板”均可停止访问。

配置 X、Telegram、模型密钥请打开本机设置。网页支持人设、语料、试聊、互动收件箱、同步、草稿编辑和生成、切换已验证引擎、活动记录、备份到本机及暂停。Codex 可通过本机 ChatGPT 登录直接测试和切换，无需 API Key；Claude 暂仍为预留入口。

## 安全边界

- 只允许固定正式 HTTPS origin，不允许整个 vercel.app 域名或任意预览域名。
- 拒绝无痕、iframe 调用和任意 URL 转发；只允许业务 API 的固定路径和方法。
- 连接助手不显示密码框；Native Messaging 清单限制固定扩展来源，程序限制操作、端口和当前用户的私有状态目录。普通本机 cookie 会话继续执行 CSRF 校验。旧版密码登录接口暂保留兼容。
- 本机服务独立核验设备会话、扩展 Origin、有效期和接口白名单。配置密钥、初始化管理员、通用文件读取和命令执行不属于设备接口。
- 请求不跟随重定向。写操作失败不自动重试，以免重复生成或重复保存。
- 云端页面使用严格 CSP，不接入分析脚本；页面代码及后续 Git 推送属于信任边界，只有可信代码可以发布。
- 电脑休眠/服务关闭时不可执行。页面显示离线，保留尚未保存的编辑状态供重连；重新加载页面仍可能丢失未保存内容。
- 页面每四秒刷新状态，不长时间占用 Vercel 函数，也不开放本机公网端口。

## 构建和发布

`npm --prefix assets/web-admin run build` 构建本机版，`npm --prefix assets/web-admin run build:desktop` 构建在线版到 `.vercel-dist` 并打包对应扩展。

`vercel.json` 定义静态构建与安全响应头；`.vercelignore` 排除数据库、凭据、运行目录和后端源码。GitHub 保留完整项目源码，Vercel 发布物只包含前端静态文件及公开扩展代码。

更换正式域名时，必须同步修改 `studio/desktop.py`、`browser-extension/desktop.js` 和扩展 manifest 的来源白名单，并重新构建/更新扩展。

## 验收

`tests/test_desktop.py` 覆盖设备登录、过期、新授权替换、撤销、来源校验、敏感操作拒绝和登录限流。

`PYTHONPATH=. .venv/bin/python tests/desktop_browser_e2e.py` 在临时数据库和隔离真实 Chromium 中验证扩展授权、草稿落到本机、模拟本机 Agent 试聊、引擎切换、暂停、敏感接口拒绝、重连保留编辑、撤销及桌面/移动布局。Agent 输出是明确的模拟夹具；生产模型需配置真实 API key 后验证。

原生通信协议参考：[Chrome Native Messaging](https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging)。安装器支持 macOS / Linux 当前用户的 Chrome、Chrome for Testing、Chromium 和 Edge，不读取浏览器 cookie 数据库。
