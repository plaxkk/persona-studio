# 人格工作室 · X 连接助手

1.3.3 支持在线面板的 Codex 写作连接测试与切换，并报告已安装版本以提示升级。升级旧版时，在扩展管理页重新加载；若从 ZIP 安装，先下载新包并替换解压文件。

首次使用：

1. Chrome 打开 `chrome://extensions`（Edge 为 `edge://extensions`），开启“开发者模式”。
2. 点击“加载已解压的扩展程序”，选择本目录；如下载 ZIP，请先解压。
3. 在浏览器工具栏打开“人格工作室 · X 连接助手”，点击“允许连接并打开工作室”。
4. 登录工作室管理员账户。已登录 X 时自动导入并验证；未登录时打开 X 登录页，由你完成登录后自动导入。

之后在同一浏览器、同一用户配置中打开工作室，会自动连接。自动登录检测仅在有效的五分钟配对期间运行。无痕模式不支持；Codex 内置浏览器不能使用 Chrome 扩展。可在扩展菜单停止自动连接，也可随时卸载。

仅使用 Chrome 官方 cookies API 获取 x.com 的 `auth_token` 和 `ct0`；不读取 Cookie 数据库，不收集其它 Cookie。凭据直接发至已授权本机 origin，页面只接收连接状态和验证过的用户名。当前只支持本机 HTTP loopback，远程部署继续使用凭据助手或手动导入。更改端口后需在扩展菜单重新授权地址。

配对需要管理员会话和 CSRF；单次随机令牌有效五分钟。服务端先核对实际 X 账号，成功后复用凭据助手原子写入 0600 文件。失败保留原凭据。重新导入不同凭据会暂停后台任务；相同凭据重复连接不会更改暂停状态。浏览器切换至另一个 X 账号时拒绝覆盖已绑定工作室。

没有任何发布、点赞、回复、转帖或关注接口。

参考：[Chrome cookies API](https://developer.chrome.com/docs/extensions/reference/api/cookies)、[外部消息](https://developer.chrome.com/docs/extensions/reference/manifest/externally-connectable)。

## 在线面板（1.2）

先在项目目录运行 `.venv/bin/python deployment/install_browser_connector.py` 安装本机连接程序。打开扩展菜单，点击“授权并打开在线工作室”，无需输入密码。只授权 https://persona-studio-plaxkk.vercel.app 这一来源。授权通过仅接受本扩展的 Native Messaging 本机程序完成，设备令牌保存在浏览器扩展会话内；关闭浏览器或 24 小时后需重新授权。网页能查看人设、草稿、会话并请求本机 Agent 执行工作室任务，密钥配置仍只能在本机设置页操作。扩展菜单“断开在线面板”可撤销连接。旧版需在扩展管理页重新加载。

## 从我的 X 生成人设（1.3）

1.3 新增 `scripting` 权限，用于在**任务专用 X 标签页**提取页面正文，以及执行固定的滚动、展开原文动作。没有任意 JavaScript、任意 URL、通用点击、键盘输入或账号写入入口。新版本需要在扩展管理页重新加载；仅下载 ZIP 不会自动更新已经安装的扩展。

网页通过已有设备授权创建任务；扩展后台通过本机 WebSocket 领取动作，设备令牌不会进入网页、URL 或模型上下文。网页登录会话不能提交采集证据。浏览器关闭后需要重新授权并继续任务；不会静默重新登录 X。

本机安装专用运行时：`.venv/bin/python deployment/install_persona_codex.py`。使用已有 Codex ChatGPT 登录与选定模型。必要正文会发送给模型服务，网页能展示预览，不能宣传资料完全不离开电脑。原始采集材料保留 7 天；应用需要本人明确点击。
