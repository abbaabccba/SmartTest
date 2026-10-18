# Jira 卡片年度 SQLite 快照与本地周期切分设计

## 目标

Jira 统计卡片每次取得的数据覆盖当前年度及其上一年度。远端数据一次落入 SQLite 后，Weekly、Monthly、Quarterly、Yearly 只在本地按既有周期规则切分并聚合，不因用户切换周期再次请求 Jira。

本设计覆盖 Self-Test、Task、Customer Feedback 三张 Jira 卡片。保留卡片各自固定 JQL、已应用用户条件、FAE-QA 成员过滤、Comments、Verify、异步任务、年度比较和既有账号隔离。

## 边界

SQLite 是年度集合、分析事实、统计快照和周期偏好的唯一持久 owner。进程内存仅保留任务、进度和取消状态，不保存可复用 issue 集合或周期统计。

年度缓存身份由以下信息共同决定：认证账号、卡片键、已应用用户 JQL、卡片固定条件及版本、FAE-QA roster fingerprint、年度覆盖起止范围。任一身份不匹配即不可复用。

年度覆盖包含两个连续的自然年度窗口：当前 Yearly 周期和其 Previous 周期。所有 Week、Month、Quarter 的当前/上一期范围均从这两个窗口内计算。跨出覆盖范围或进入新的年度窗口时，旧年度快照失效，只有显式刷新或 Apply 才新建远端任务。

## 数据模型

现有 `jira_analytics_snapshots` 继续拥有卡片快照状态、有效 JQL、比较 JQL、统计 JSON 和任务归属。现有 `jira_analytics_snapshot_issues` 继续关联年度命中的 issue。

新增年度分析事实 owner，按 `snapshot_id + issue_id` 保存：

- issue 创建时间和产品线归属所需字段；
- creator 身份；
- 评论作者及评论数量的紧凑统计记录；
- 最后一次 `Resolved -> Verified` 的验证人和发生时间；
- 已解析的 FAE-QA roster 归属。

不保存评论正文、完整 changelog、JQL 正文副本以外的敏感内容，也不以原始 Jira payload 作为卡片持久数据模型。事实记录只包含本地统计所需的身份、时间和计数。

## 远端加载与分梯度

每张卡片使用两组年度 JQL：Bugs/Comments（及 Invalid）保留 creator 的五个 FAE-QA 组条件；Verify 使用同一用户条件、卡片业务固定条件和年度窗口，不附加 creator QA 条件。用户自行输入的 creator 条件仍按原样保留。

Verify 遍历其独立集合中每个 issue 的最后一次 `Resolved -> Verified`，只计入 FAE-QA 验证人，按 issue 产品线归属。非 QA 创建但 QA 验证的 issue 必须计入 Verify，不进入基础指标；最后验证人为非 QA 时不回退到更早的 QA 验证记录。

一次年度刷新只创建一个既有卡片异步任务。任务固定按下列顺序执行，并逐层写入同一个 pending 年度快照：

1. 当前年度基础字段和 comments；
2. 上一年度基础字段和 comments；
3. 独立 Verify 集合的当前年度 changelog；
4. 独立 Verify 集合的上一年度 changelog。

基础层完成后可生成 Bugs 和 Comments 预览；Verify 层尚未完成时该指标明确为 Loading，不能以 0 代替。每层完成后更新同一 SQLite pending 快照。任务完成后才激活完整年度快照。

批量请求继续复用 `JiraGateway.search_all_payloads`：基础层请求 `CORE_FIELDS + comment`，Verify 层请求 `key + created + project` 并展开 `changelog`，以便独立集合记录 issue 创建时间和产品线；每页 100 条，既有最多 4 个分页请求并发。禁止回退为逐 issue `get_issue()`、`issue_get_comments()` 或 changelog 请求。

同一任务内 FAE-QA 组归属按账号缓存一次；不得在四层聚合中重复调用 `user_groups`。该缓存只存在任务生命周期，最终 roster 归属写入 SQLite 年度事实。

## 本地周期切分

GET statistics 从匹配的 active 年度快照读取事实，根据既有 `jira_period_ranges` 计算四个周期各自当前期与上一期边界，以 issue 创建时间筛选事实并一次返回完整四周期 payload。周期控件只选择该前端本地 payload，不保存账号+卡片周期偏好，也不调用接口。

Comments 和 Verify 延续现有业务定义：它们归入其 issue 所在周期，不以评论创建时间或验证发生时间二次过滤。此规则保证年度事实可直接按 issue 创建时间切分，并与原有卡片统计一致。

切换周期不创建 `JiraAnalyticsService.search`，不创建远端任务，不访问 Jira。没有匹配年度快照时返回 `no_snapshot`；页面提示 Apply 或 Refresh，不自动发起查询。

## 接口与页面

`POST /api/jira/cards/{card_key}/query` 只负责显式 Apply 的最大年度 snapshot 刷新。它不接受 period preference 或 reuse 意图。远端集合不再因 Week、Month、Quarter 或 Year 的展示选择改变。

`GET /api/jira/cards/{card_key}/statistics` 只执行 SQLite 切分与聚合，一次返回 week/month/quarter/year 四套本地视图；每套继续携带当前/上一期统计、ranges、availability，顶层携带 `state` 和 query 元数据。

前端周期切换从已取得的四周期 payload 同步更新周期、统计和 Current JQL，零 API、零数据库读取。若年度快照正在加载，页面展示已完成层级的本地预览；未完成指标维持 Loading。账户切换、登出和条件变更继续清理可丢弃前端展示 payload，不删除账号的有效 SQLite 年度快照。

## 失效、失败与取消

新年度任务不得覆盖旧 active 年度快照。取消或失败时，有匹配旧 active 年度快照则继续重放旧完整统计；首次加载没有 active 快照时保留已完成层级的 pending 预览，并标出未完成或失败的指标。

只有显式 Refresh、已应用条件改变、卡片/roster fingerprint 改变、年度范围不覆盖当前时间时允许创建新远端任务。GET、周期切换、页面重新进入和本地缓存读取都不得启动远端查询。

## 移除项

- 移除周期切换在 `/query` 内构造 period JQL、创建快照和启动远端任务的路径。
- 移除按周、月、季度分别构造卡片远端 JQL 并重复创建快照的路径。
- 移除只为当前周期而存在的重复 compare JQL、重复统计与重复任务代码。
- 不保留进程内年度结果缓存、第二套统计 store 或逐 issue 获取的兼容分支。

## 验收

- 首次年度任务最多产生当前/上一年度各一轮基础请求与 Verify 请求，严格按四层顺序。
- 已有匹配年度快照时切换四种周期，Jira Gateway 的搜索调用数不增加，且不创建异步任务。
- 本地 Week、Month、Quarter、Yearly 的统计与对应旧周期 JQL 的统计结果一致。
- Bugs/Comments/Invalid 的 creator QA 边界不变；Verify 查询不附加 creator QA，仅按最后验证人的 FAE-QA 归属计数。
- 非 QA creator + QA verifier 的 issue 仅计入 Verify，非 QA verifier 不展示；Verify 仍按 issue 创建时间切分当前/上一期。
- 年度范围、账号、卡片、用户 JQL、fixed JQL、roster fingerprint 不匹配时不复用。
- 失败、取消、旧任务覆盖保护、重启后的 SQLite 重放和前端 Loading 状态均有回归测试。
- 移除旧周期远端查询路径后，运行 Core Jira、Web API、前端卡片测试、边界检查和 `git diff --check`。
