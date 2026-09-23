# 人格工作室架构审查（2026-09-23）

决定：在 MIT 上游基础上独立产品化，保留来源、许可证与 NOTICE。模块化单体 + 独立 worker，单用户、单虚拟人格、单 X 账号。首版自动读取和生成，最终操作由用户在 X 完成。

| 优先级 | 证据 | 问题与决定 | 验收 |
|---|---|---|---|
| P0 | scripts/automation_runner.py: rank_items 捕获异常后 return items | 风险失败开放；改为返回空候选，生产新服务不导入写适配器 | 风险异常无候选、无外部写入 |
| P0 | scripts/x_adapter.py 含 create_tweet/favorite_tweet/retweet/follow | 与半自动边界不一致；保留为旧工具，不加载进 studio | 生产依赖扫描与 mock 禁写测试 |
| P0 | installer、bridge、factory.json、env 各有配置 | owner 和模式多个真相来源；统一 SQLite 配置，密钥仅由专用存储读取 | 切换引擎/重启保持配置 |
| P1 | schedule_posts.random_times 用 replace(hour=26) | 已复现 ValueError；修复跨日时间计算 | 午夜与时区测试 |
| P1 | App.jsx setFeature 在请求前修改 state | API 失败后界面失真；服务端确认 + 可读错误 | 拒绝/断网状态不误报 |
| P1 | admin_server /api/health 固定 ok=true | 存活不等于账号/模型就绪；分开 liveness/readiness | 缺凭据不会显示已就绪 |
| P1 | rate/check increment 在 adapter 前执行 | 失败和 dry-run 混用真实发送配额；新服务使用原子生成任务预算 | 并发预算、取消、失败尝试有记录 |
| P1 | deployment/hermes_bridge.py history=[] | 重启丢聊天，Telegram 单独配置；新服务统一会话与 owner | 重启会话测试、非 owner 拒绝 |
| P1 | pending 仅新增/取消 | 缺少编辑保护和人工交接结果；加入草稿版本、乐观并发、跳转事件、用户确认 | 旧生成不覆盖手工修改 |
| P1 | legacy SQLite 无显式 schema_version | ALTER 异常吞掉；采用版本化迁移与导入标记 | 重复迁移不重复，保留原库 |
| P2 | persona_distill 固定风格禁词 | 风格不应当安全规则；资料作为风格参考，风险边界单独执行 | 不同人格可不同表达 |
| P2 | scripts/installer、deployment 写死目录/运行时 | 局部补丁难分发；统一 STUDIO_STATE_DIR / PORT 与 launchd/systemd | 干净临时目录启动 |

## 保留的基础
FastAPI/Pydantic 的 API 校验、React/Vite 前端、SQLite WAL/FTS、persona_distill 的净化和检索、x_signal 的引用/回复识别与注入信号、twikit 的读取能力。没有理由为个人版引入微服务、Redis、多租户和任意插件上传。

## 新边界
Web → API → 持久化任务 → worker → EngineAdapter 或 XReader。引擎环境仅含模型凭据；Telegram/X 凭据不进入提示词或引擎。XReader 不暴露写入方法。首次/迁移启动暂停。稿件打开 X ≠ 发布，用户确认 ≠ 平台验证。

## 交付限制
真实模型、X 和 Telegram 验收需要用户凭据。模拟来源在测试目录，与生产数据隔离。X 搜索/通知不能保证覆盖全部互动。预填回复不作为必需能力，复制文案 + 打开原帖为稳定路径。
