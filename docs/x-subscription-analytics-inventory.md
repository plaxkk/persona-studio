# X Subscription Analytics 实测字段清单

记录日期：2026-10-13。来源是已登录的 @kkplayit 浏览器中 Subscription → Analytics 的实际页面。这里只记录页面可见字段与已观察到的请求结构，不保存 Cookie、请求头、访问令牌或账号原始数据。字段是否长期可用仍需在采集与相同日期范围核对时确认。

| 页面 | 页面路径 | 时间范围 / 筛选 | 可见字段 | 当前可用状态 | 读取方式与待核对点 |
| --- | --- | --- | --- | --- | --- |
| Overview | `/i/account_analytics/overview` | 7D、2W、4W、3M、1Y；可选图表指标，支持 CSV | Impressions、Engagement rate、Engagements、Profile visits、Replies、Likes、Reposts、Bookmarks、Shares、Followers、Verified followers、Active followers；日/小时趋势 | 实际页面有值 | 已观察到 `accountOverviewDailyQuery` GraphQL 请求及 200 JSON 响应；见下方安全结构。需核对每个字段的时间窗口、总量与趋势数组。 |
| Audience | `/i/account_analytics/audience` | 7D、28D、3M；指标可选 Likes、Impressions、Bookmarks、Shares、New follows、Replies、Reposts、Profile visits | Age、Active times、Following、Gender、Country、Device | 页面可打开；部分卡片显示 `Not enough data yet` | 页面 DOM 已核对。需逐卡片确认请求、缺数据表达、分母和时间范围；不能把缺少样本当零。 |
| Content | `/i/account_analytics/content` | Posts、Posts and replies、Replies、Community posts；7D、2W、4W、3M；排序、CSV | 帖子列表的 Date、Impressions、Likes、Replies、Reposts；单条详情链接 | 实际列表有值 | 页面 DOM 已核对。Posts 筛选中也出现以 `@` 开头的内容，分类不能只靠过滤器；保存 X 原分类与本机推文类型两个字段。需核对分页、排序与历史覆盖范围。 |
| Content detail | `/i/account_analytics/content/{post_id}` | 单条历史帖子；Activity Breakdown 有 Realtime Last Hour / Last 48 Hours、First 48 Hours | Impressions、Likes、Replies、Reposts、Engagement rate、Profile visits、New follows、Bookmarks、Shares、Media views；Audience Insights 中 Age、Country、Gender、Device、Following | 2026-07-27 的帖子在 2026-10-13 仍可查看详情；Media views 在所见样本为 `-` | 页面 DOM 已核对。需要按帖子 ID 读取实际请求，保存读取时间；`-` 应记为未知。单条样本只能证明该历史详情可见，不能推断全部历史可补齐。 |
| Video | `/i/account_analytics/video` | 48H、7D、2W、4W、3M、1Y；CSV | Views、Watch Time (hours)、Completion Rate、Average Watch Time、Video Completion；视频列表 Date / Views | 实际页面有 2 条视频 | 页面 DOM 已核对。需核对视频字段请求与汇总/单条口径。 |
| Live | `/i/account_analytics/live` | 页面可选筛选/排序 | 直播列表及指标 | 所见账号暂无直播 | 页面 DOM 已核对。暂无可验证的真实指标样本。 |
| Spaces | `/i/account_analytics/spaces` | 页面可选筛选/排序 | Spaces 列表及指标 | 所见账号暂无 Space | 页面 DOM 已核对。暂无可验证的真实指标样本。 |

## 已观察到的请求结构

Overview 页面调用 `/i/api/graphql/2hqAR3h2xhN1cUrUBZypyg/accountOverviewDailyQuery`。查询变量的**键名**是 `current_from`、`current_from_iso`、`current_to`、`current_to_iso`、`prev_from`、`prev_from_iso`、`prev_to`、`prev_to_iso`、`backfill_from`、`backfill_to`、`show_realtime_active_followers`、`show_verified_followers`。只保留路径和键名，不记录真实请求值或鉴权材料。观察到的响应根路径为 `data.viewer_v2.user_results.result`，其下有 `current_time_series`、`hourly_backfill`、`legacy_current_follow_metrics`、`legacy_previous_follow_metrics`、`previous_totals`、`realtime_active_followers`、`relationship_counts`、`verified_follower_count`。

## 实施边界

- 采集器按页面、接口版本、查询窗口保存原始业务响应与规范化指标；凭据只留在本机私密配置，网页、扩展与 Notion 不保存服务端密钥。
- 每个指标带实际采集时间、目标日期范围、来源页面/请求及解析版本。较晚补读的值不能回填成 24/72 小时当时快照。
- 最近 28 天优先补齐；历史帖子逐页继续并显示已覆盖日期和数量。`Content detail` 的历史可读性需要按样本持续验证，不能把部分可读写成全部可读。
- Supabase 的项目连接和断线补传尚未配置、实现或验收。本清单是实施输入，不代表迭代 03B 功能已上线。
