# Codex 写作链路

设置 → 写作引擎 → Codex → 测试连接 → 切换到 Codex。使用现有 `codex login` 的 ChatGPT 登录，不需要复制登录凭据或配置 DeepSeek Key。测试时从本机 Codex 配置读取模型和 reasoning，验证成功后固定在写作配置中；修改本机模型后重新测试即可同步。此功能与 Codex 的人设采集任务独立。

支持原创、回复、互动判断、试聊。每个任务保存引擎和模型快照，切换只影响新任务。失败返回 Codex 错误，不回退其他供应商。取消和超时沿用工作队列的子进程组终止机制。

当前复用已验证的 macOS Codex CLI 0.156.1 隔离环境；不支持的平台、CLI 版本或非 ChatGPT 登录会阻止启动。使用临时目录、ephemeral 会话、结构化输出；不载入个人 AGENTS、插件、skills、hooks，不接人设采集 MCP，也不开放 shell、浏览器、文件操作或账号写入工具。登录状态和模型额度以真实连接测试为准。

本机界面构建后重启 API 与 worker；在线面板另需发布 desktop 构建。旧版浏览器连接助手需要重新加载新版，才能允许 Codex 的测试与切换路由。

验证：`pytest tests/test_codex_writing.py tests/test_inspiration.py tests/test_studio.py tests/test_desktop.py`；`python tests/codex_writing_isolation.py` 用本地模拟模型检查真实 CLI 工具清单，不向模型供应商发送私人材料。

调用模式参考 [OpenAI Codex 非交互模式文档](https://developers.openai.com/codex/noninteractive/)。
