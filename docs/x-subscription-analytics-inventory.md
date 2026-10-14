# X Subscription Analytics 实测字段清单

记录日期：2026-10-13。来源是已登录的 @kkplayit 浏览器中 Subscription → Analytics 的实际页面。这里只记录页面可见字段与已观察到的请求结构，不保存 Cookie、请求头、访问令牌或账号原始数据。字段是否长期可用仍需在采集与相同日期范围核对时确认。

| 页面 | 页面路径 | 时间范围 / 筛选 | 可见字段 | 当前可用状态 | 读取方式与待核对点 |
| --- | --- | --- | --- | --- | --- |
| Overview | `/i/account_analytics/overview` | 7D、2W、4W、3M、1Y；可选图表指标，支持 CSV | Impressions、Engagement rate、Engagements、Profile visits、Replies、Likes、Reposts、Bookmarks、Shares、Followers、Verified followers、Active followers；日/小时趋势 | 实际页面有值 | 已观察到 `accountOverviewDailyQuery` GraphQL 请求及 200 JSON 响应；见下方安全结构。需核对每个字段的时间窗口、总量与趋势数组。 |
| Audience | `/i/account_analytics/audience` | 7D、28D、3M；指标可选 Likes、Impressions、Bookmarks、Shares、New follows、Replies、Reposts、Profile visits | Age、Active times、Following、Gender、Country、Device | 实际页面及 `audienceOverviewDataQuery` 有值；部分卡片显示 `Not enough data yet` | 按所选指标和时间范围保存受众分布；缺数据不可写零。热力图可能使用独立时间窗口。 |
| Content | `/i/account_analytics/content` | Posts、Posts and replies、Replies、Community posts；7D、2W、4W、3M；排序、CSV | 帖子列表的 Date、Impressions、Likes、Replies、Reposts；单条详情链接 | 3M Posts 查询返回 828 个结果项，其中 808 个带可解析帖文和指标，19 个为可见性限制包装，1 个无结果 | `contentPageQuery` 请求含 `max_results=1000`、`query_page_size=100`；必须记录覆盖区间、筛选条件、上限和异常项，不能据此推断全部历史均已同步。Posts 筛选中也出现以 `@` 开头的内容，分类不能只靠过滤器。 |
| Content detail | `/i/account_analytics/content/{post_id}` | 单条历史帖子；Activity Breakdown 有 Realtime Last Hour / Last 48 Hours、First 48 Hours | Impressions、Likes、Replies、Reposts、Engagement rate、Profile visits、New follows、Bookmarks、Shares、Media views；Audience Insights 中 Age、Country、Gender、Device、Following | 2026-07-27 的帖子在 2026-10-13 仍可查看详情；所见 Media views 为 `-` | 已观察到详情、总指标、首 48 小时、最近 48 小时、实时和受众的独立请求。单条历史样本仍不足以推断全部历史可补齐；`-` 应记为未知。 |
| Video | `/i/account_analytics/video` | 48H、7D、2W、4W、3M、1Y；CSV | Views、Watch Time (hours)、Completion Rate、Average Watch Time、Video Completion；视频列表 Date / Views | 实际页面有 2 条视频 | 页面 DOM 已核对。需核对视频字段请求与汇总/单条口径。 |
| Live | `/i/account_analytics/live` | 页面可选筛选/排序 | 直播列表及指标 | 所见账号暂无直播 | 页面 DOM 已核对。暂无可验证的真实指标样本。 |
| Spaces | `/i/account_analytics/spaces` | 页面可选筛选/排序 | Spaces 列表及指标 | 所见账号暂无 Space | 页面 DOM 已核对。暂无可验证的真实指标样本。 |

## 已观察到的请求结构

Overview 页面调用 `/i/api/graphql/2hqAR3h2xhN1cUrUBZypyg/accountOverviewDailyQuery`。查询变量的**键名**是 `current_from`、`current_from_iso`、`current_to`、`current_to_iso`、`prev_from`、`prev_from_iso`、`prev_to`、`prev_to_iso`、`backfill_from`、`backfill_to`、`show_realtime_active_followers`、`show_verified_followers`。只保留路径和键名，不记录真实请求值或鉴权材料。观察到的响应根路径为 `data.viewer_v2.user_results.result`，其下有 `current_time_series`、`hourly_backfill`、`legacy_current_follow_metrics`、`legacy_previous_follow_metrics`、`previous_totals`、`realtime_active_followers`、`relationship_counts`、`verified_follower_count`。

`current_time_series` 每项包含 `count`、`engagement_type`、`is_engaging_user_verified`、毫秒 `timestamp`；`hourly_backfill` 包含 `count`、`engagement_type`、毫秒 `timestamp`。两者日期可能重叠，不能直接相加。所见 `engagement_type` 包括 `Displayed`、`Fav`、`Reply`、`Retweet`、`Bookmark`、`Share`、`ProfilePic`、`Follow`、`Unfollow` 等。`legacy_current_follow_metrics` 的每项是 `metric_values[{metric_type, metric_value}]` 加 `timestamp.iso8601_time`。曾将 `current_time_series` 按类别直接求和，结果与页面卡片不完全相同；卡片口径和实时回填关系尚待实测核对，不能发布该求和结果为精确总量。

Content 列表调用 `contentPageQuery`，变量键为 `from_time`、`to_time`、`max_results`、`query_page_size`、`requested_metrics`。结果在 `data.viewer_v2.user_results.result.tweets_results[]`；可解析项含帖子 ID、`details.created_at_ms`、`details.full_text`、`organic_metrics_total[{metric_type,metric_value}]`。所见 13 类指标为 `Impressions`、`Likes`、`Engagements`、`Bookmark`、`Share`、`Follows`、`Replies`、`Retweets`、`ProfileVisits`、`DetailExpands`、`UrlClicks`、`HashtagClicks`、`PermalinkClicks`。本次 828 项中，808 项为可解析 `Tweet`、19 项为只有类型而没有正文或指标的 `TweetWithVisibilityResults`、1 项无 `result`；解析器须分别记录缺口并继续，不能静默记作完整覆盖。

在同一已登录页面临时观察请求调用方式：切换到 `Replies` 的 3M 筛选时，`contentPageQuery` 经 `XMLHttpRequest` 发起并收到 HTTP 200，响应文本约 87.4 万字符；当时对 `window.fetch` 的同样范围探针未观察到该请求。临时探针只记录请求路径、响应长度和状态，已从页面移除。连接助手的自动采集应观察 XHR 响应，不能只监听 `fetch`；切换筛选时可能复用前端缓存，必须核对实际请求条件与读取时间。

再次在 `Replies` 的 2W 筛选核对到请求方法为 GET，变量中的 `from_time`、`to_time` 为字符串，`max_results` 为数字，两时间边界相差约 14 天；HTTP 状态为 200。此次临时探针也已移除。连接助手因此按 `contentPageQuery` 的实际 URL 与变量结构截取响应，而不是按页面上的按钮文字推断读取范围。

Content 的日期控件允许选择较早的自定义窗口。实际在 `Replies` 选 2026-06-01 至 2026-06-14 后，页面 URL 带 `from=2026-06-01&to=2026-06-14`，同次 `contentPageQuery` 请求的 `from_time=2026-06-01T00:00:00.000Z`、`to_time=2026-06-14T23:59:59.999Z`、`max_results=1000`，方法为 GET。检查后已恢复原来的 2W 筛选。历史补采可以使用这组已实测的 URL 参数，但必须以实际请求边界为准，不能只凭日历显示判定采集成功。

Content detail 分别调用 `postDetailsProviderQuery`（正文与公开计数）、`postDetailsProviderMetricsTotalQuery`（11 类累计指标）、`postDetailsProviderFirst48HoursDataQuery`、`postDetailsProviderLast48HoursOrganicDataQuery`、`postDetailsProviderRealtimeDataQuery` 和 `postDetailsProviderAudienceQuery`。前两项以 `rest_id` 查询；累计指标另带 `requested_metrics`、`from_time`、`to_time`。时间序列项含 `metric_values` 与 `timestamp.iso8601_time`，某些指标只有 `metric_type` 而无 `metric_value`，应记未知而非零。详情受众项含国家或年龄、性别、设备、关系等维度及 `count`、`engagement_type`、毫秒时间戳；其分布不是粉丝画像。

Audience 页面调用 `audienceOverviewDataQuery`，变量键为 `from_time_incl`、`to_time_excl`、`dimensions`、`heatmap_from_time_incl`、`heatmap_to_time_excl`、`requested_metrics`。响应包含 `organic_metrics_time_series`（指标值及 ISO 时间）、`uec_country_metrics_daily_time_series_count`（国家、行为、计数、毫秒时间）和 `uec_metrics_daily_time_series_count`（年龄、性别、设备、关注关系、行为、计数、毫秒时间）。所见 28 天 Likes 页面仅国家有可见分布，其余多个卡片显示样本不足；只有在核对筛选指标和分母后才能重建百分比。

## 实施边界

- 采集器按页面、接口版本、查询窗口保存原始业务响应与规范化指标；凭据只留在本机私密配置，网页、扩展与 Notion 不保存服务端密钥。
- 每个指标带实际采集时间、目标日期范围、来源页面/请求及解析版本。较晚补读的值不能回填成 24/72 小时当时快照。
- 最近 28 天优先补齐；历史帖子逐页继续并显示已覆盖日期和数量。`Content detail` 的历史可读性需要按样本持续验证，不能把部分可读写成全部可读。
- Supabase 的项目连接尚未配置或真实验收。本清单是实施输入，不代表迭代 03B 功能已上线。
- 本机已加入 Content 响应的纯解析器和 SQLite 第 5 版暂存迁移：单次查询记录原始业务响应、可解析逐帖指标、受限/缺失/重复项、查询上限与实际读取时间，任务 ID 重试幂等。暂存表的 `cloud_status=pending` 仅表示待同步；目前还没有浏览器端实际采集任务，不能标为已接通。
- 真实 3M Posts 响应的 `JSON.stringify` 长度约 87.4 万字符，UTF-8 约 91.1 万字节；按当前 Python WebSocket 入站检查的默认 ASCII 转义方式重新序列化，约 96.7 万字符，超过 95 万字符上限。因此，不能把 3M 原始响应作为单条连接助手消息传输。采集端须按较短日期窗口或有序分片传输，并在本机校验每片大小、顺序、总数、请求条件和完整性后入库；任何分片丢失都要标记该窗口未完成。窗口进一步拆分时仍需记录查询上限，不能把低于上限等同于完整历史。
- 连接助手 1.3.10 的开发分支已加入大结果分片传输：每片 Base64 载荷不超过 98,304 字节，整条结果不超过 8.5 MB，本机校验任务 ID、片序、片数和 SHA-256 后才交给原读取处理器；缺片、错序或校验失败不会入库。该版本只有传输能力，没有 Analytics 业务采集；1.3.11 的接入范围见下。
- 连接助手 1.3.11 的开发分支已把该分片协议接入 Content 业务采集：在新开的 X Analytics 页面提前监听 XHR，只保留 200 响应中的 `data`，不保留 Cookie、请求头和鉴权参数；核对已登录账号、实际查询窗口、读取时间和业务 JSON 结构后，再用任务 ID 幂等写入 SQLite。页面可手动发起最近 28 天或近 3 个月的读取，显示返回数量、解析缺口、查询上限及待传云端状态。模拟本机 Chrome XHR、超过 95 万字符的分片 WebSocket、数据库 2→6 迁移及前端界面已验证。**真实账号上的连接助手 1.3.11 尚未安装，真实 Analytics 读取、完整历史补采及 Supabase 写入核对均未验收；03B 不得标记为已上线。**
- 连接助手 1.3.12 的开发分支可读取单个自定义历史窗口，并按实际返回的 UTC 起止时间核对页面所选日期。本机可把不超过 365 天的历史范围拆成最多 28 天一段，按至少 5 分钟间隔排队；同一窗口已成功的任务不会重复入队，失败窗口可重试。最近 28 天的窗口在新连接助手在线时每日自动补读，临时失败至少隔一小时再试。实际账号中这些新任务尚未执行；查询达到 1000 项上限的窗口仍须细分，受限帖子仍是覆盖缺口，不能宣称历史已完整。
- 连接助手 1.3.13 的开发分支另行读取 Overview 默认 4W 页面：只截取 `accountOverviewDailyQuery` 的业务 `data`，核对本期与上期各约 28 天且边界相接。采集器兼容 XHR 与 Fetch 两种响应方式；Overview 实际使用哪一种仍需在真实账号核对。SQLite 第 8 版迁移按 `current_time_series`、`hourly_backfill`、`previous_totals`、两类历史关注序列分别保存分项及缺口，不把重叠时间点相加；页面只显示直接返回的粉丝相关标量、查询窗口和采集时间。Supabase 增加私有 Overview 运行与分项表，按运行 ID 和分项复合键去重补传。原始业务响应、解析版本和覆盖缺口保留，鉴权信息不入库。当前是模拟验收，仍需安装新版扩展后与 X 页面同范围逐项核对；不能据此声称 Overview 总量已对账或 03B 已上线。
- 已准备 `deployment/supabase_analytics.sql` 私有表结构和本机补传函数。项目地址在本机 SQLite 设置中保存，`SUPABASE_SECRET_KEY` 只写入本机权限为 0600 的私密配置；本机设置页可保存/移除连接，在线面板仅看状态。只接受 `https://*.supabase.co` 与新式 `sb_secret_` 密钥；HTTP 不跟随重定向。一次运行先按 ID upsert 原始业务响应与覆盖缺口，再分批按 `(run_id,post_id)` upsert 规范化指标；所有远端写入成功才在本地标已同步，失败留待退避重试。后台每 30 秒尝试一项并显示待传数、最近状态和错误码；模拟 503 与断线后的幂等补传已验证。真实项目尚未配置，真实写入与查询仍未验收。
