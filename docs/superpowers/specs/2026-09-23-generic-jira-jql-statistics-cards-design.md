# 通用 Jira JQL 统计卡片设计

## 目标与边界

Self-Test、Task、Customer Feedback 三张卡片本质一致：读取 Jira 页面已应用的用户 JQL，与卡片声明的固定 JQL 求交集，异步查询 Jira，把成功结果原子写入账号加卡片范围的 SQLite 快照，再由 Jira 页面和 Dashboard 挂载同一个统计组件展示。

本轮以重构现有 Self-Test 为起点建立唯一通用流程，不在旧实现旁边增加第二套 service、router、任务、缓存、统计或前端组件。三张卡片只保留声明式查询与少量显示元数据差异。完成迁移后删除 Self-Test 专属分支、Customer 占位组件、重复偏好与展示状态，以及为兼容新旧路径产生的临时包装。

本轮不提供用户在线编辑卡片定义，不新增数据库配置表，不改变 Jira 页面共享过滤器的发布语义，不引入 Dashboard 专用查询或统计 owner，也不改变认证、凭据失效和普通登出的生命周期。

## 周期对比扩展

三张卡片在既有当前周期统计之外，统一增加上一周期对比数据。用户交互、筛选条件、产品线切换、指标切换、卡片注册和 Dashboard 引用均保持不变；前端不增加对比开关或第二套控件。对比数据只改变时间范围，用户 JQL、卡片固定业务条件、creator Group、Channel、Issue Type、Wireless 优先规则和聚合算法必须与当前周期完全相同。

周期改为自然时间边界，并采用左闭右开区间避免一条 issue 同时进入两个周期：

- Weekly：本周一至当前时间；对比上周一至本周一。
- Monthly：本月一日至当前时间；对比上月一日至本月一日。
- Quarterly：本季度首日至当前时间；对比上季度首日至本季度首日。
- Yearly：本年一月一日至当前时间；对比上一年一月一日至本年一月一日。

当前周期结束条件继续使用 `created <= now()`；上一周期使用明确的结束日期和 `created < current_period_start`。不得用 `-7d`、`-30d`、`-3M` 或按天数估算自然周期。

## 唯一模型

Core 提供一个通用 Jira JQL 统计卡片 owner，并以不可变定义注册卡片。定义只包含：

- `card_key`、标题和首项指标文案；
- 卡片固定 JQL；
- 五个产品线所需的声明式 JQL 范围及 Wireless 优先条件；
- 固定规则 fingerprint。

不为 Self-Test、Task、Customer Feedback 建立子类或专属 service。卡片注册表只保存数据，不执行查询、不持有状态。通用 owner 负责 JQL 组合、远端验证与分页、人员及产品线统计、计数守恒和统计 payload。Web 层只按 allowlist 解析 `card_key` 并把对应定义交给同一个 owner。

前端只保留一个参数化组件。`card_key` 决定 API scope、偏好 scope、可丢弃展示缓存键、标题和首项指标文案；轮询、周期控件、产品线切换、指标切换、图表绘制、失败与取消呈现全部共用。

## JQL 流程

页面共享过滤器只发布用户条件。每张卡片的最终查询唯一由 Core 组合：

```text
effective_jql = compose_jql(applied_user_jql, card_fixed_jql(selected_period))
```

`compose_jql` 继续负责括号隔离和 `ORDER BY` 保留。校验、执行、SQLite 保存和快照有效性判断必须使用同一个 `effective_jql`，其他层不得追加、删减或重写条件。Weekly、Monthly、Quarterly、Yearly 由公共周期 owner 同时产生 current 与 previous 两个明确范围。查询流程对同一份用户条件和卡片固定业务条件分别组合两条 effective JQL；除时间范围外，两条 JQL 必须一致。

一个 Refresh 根任务顺序或受控并发执行 current 与 previous 两次 Jira 只读查询。采用两次独立查询，不查询两个周期的大集合后在本地拆分，保证每条 JQL 都能直接在 Jira 中审计和核对。任一查询、group 解析或聚合失败时，本次 generation 整体失败并保留旧有效快照，不允许只激活一半结果。

固定定义可由多个声明式子句组成，但最终只能形成一个可审计的卡片固定 JQL。Wireless 优先条件同时参与固定 JQL和通用归类过程，禁止在 router 或前端增加卡片名称判断。互斥条件保证一条 issue 最多进入一个产品线；Wireless 命中后不再计入原项目产品线。

GET 只重放 SQLite，不访问 Jira。Refresh 才执行远端查询。Reuse 仅在账号、卡片、用户 JQL、固定 JQL、周期和 definition fingerprint 全部一致时恢复已有有效结果。

## 卡片定义

### Self-Test

- `issuetype = Bug`。
- `"Channel of Reporter" = "Self-Test"`。
- Bugs/Invalid 使用下述统一的创建人组与项目交集；Comments/Verify保留原活动来源和作者统计。
- 基础问题归属按公共scope与原优先级选择实际命中的产品线，不再使用外层五组 OR 或非无线 creator NOT 排除。
- 指标为 `Bugs / Resolved / P0 / Invalid`。
- 保持当前结果口径、项目映射、人员维度和未归类计数，不因通用化改变现有 Self-Test 数据。

### Task

- `issuetype = Task`。
- 基础问题/Invalid使用与Self-Test相同的创建人组+项目边界；活动指标保持原机制。
- 多组创建人保留原先级，但候选产品线必须同时满足该线creator/project及卡片来源。
- 不增加 Channel、resolution、labels 或额外项目排除。
- 指标为 `Tasks / Resolved / P0 / Invalid`。

### Customer Feedback

- `issuetype = Bug`。
- `"Channel of Reporter" = "Customer-Feedback"`。
- China Operator、Smart Device、TV、Global Operator & STB 的来源条件仅为 `"Channel of Reporter" = "Customer-Feedback"`，不追加无线 labels 或反向 labels 排除；`issuetype = Bug`、既有授权及时间段条件保持；基础问题不限定创建人，各普通线使用对应项目。
- Wireless Connection 使用以下 labels，按 Jira 实际 label 值生成 `IN` 条件：
  - `Customer_W1`
  - `Customer_W1U`
  - `Customer_W2L`
  - `customer_w2`
  - `Customer_w1u`
  - `customer-w2`
  - `customer-w2L`
  - `customer_w1d`
- Wireless 分支排除 resolution：`Invalid Case`、`Cannot Reproduce`、`HW Fix`、`Won't Fix`、`Won't Do`；`resolution is EMPTY` 保留。
- Wireless 分支排除项目 `RD SW Platform`、`Wireless Project`。
- Customer 基础问题与 Invalid 不限定 creator：四普通线按各自 project + Bug + Channel，Wireless 保留四项目及上述 labels/resolution/项目排除。符合 Wireless 来源者优先归 Wireless，否则按 project 归普通线；保留真实 creator 人员行，即使不属 QA，也不额外查询其用户组。Self-Test/Task 继续公共 creator-group + project 范围。
- Coco于2026-10-19明确保留Comments/Verify原机制，不套本次基础产品线范围。活动全集继续不限定issue creator；Customer仍为Bug+Channel AND（原无线labels/resolution/项目排除分支 OR 项目IPTV/SH/TV/OTT），不可简化为全项目Bug+Channel。作者及既有作者产品线计数、布局不改。
- `core/jira/jql.py:customer_feedback_jql()` 是来源 owner；原 definition fingerprint 随业务 JQL 变化，既有 API 同时核对 JQL/fingerprint，旧年度快照不能作为新口径有效结果；本次不强制远端刷新、不迁移或删除数据。
- 指标为 `Bugs / Resolved / P0 / Invalid`。

上述 JQL 由字段、值、互斥分支和周期声明确定性拼接。用户提供的示例仅作为业务条件来源，不作为可直接执行的原始字符串保存。

## 数据、任务与缓存边界

继续复用现有 Jira analytics SQLite owner。查询状态、issue membership、统计 payload、active/pending generation 和 definition fingerprint 均以 `(account, card_key)` 隔离。普通登出与会话过期保留账号有效结果；明确凭据失效才走既有统一删除入口。

一个 Refresh 对应一个根任务。任务内部完成两套远端查询、必要的 creator group 解析、通用统计和一次原子激活；不得为对比数据或每张卡片建立新的任务 registry。进度总量是两次查询的合计，取消同时终止尚未完成的当前/上一周期工作。失败、取消、profile group 读取失败或服务重启 interrupted 均不覆盖旧有效结果。任务查询与取消继续按真实 session 加 `card_key` 授权，另一会话只能读取持久结果，不能取得或操作原任务。

SQLite 的一个卡片快照同时保存 current 与 previous 的真实 issue membership、统计 payload、effective JQL 和日期范围，并作为不可分割的有效结果激活。快照 fingerprint 必须包含两套周期条件，使旧的单周期快照不被误当作对比结果；GET 仍只重放 SQLite，不补查 Jira。payload 在每个产品线人员行中提供当前四指标和对应 previous 四指标，并在顶层提供 current/previous 的可显示日期范围、总数、未归类数和缺失 creator 数。前端不得自行推断日期或用当前结果倒算上一期。

浏览器 sessionStorage 只保存按账号加 `card_key` 隔离的可丢弃展示 payload，用于同步首帧。任何查询、导出或业务动作不得从中读取权威 issue ID。Jira 页面与 Dashboard 读取同一 API 和同一 SQLite 快照；Dashboard 不启动独立查询，不保存第二份结果。

## 重构与删除范围

- 把 `jira_api.py` 中 `card_key == "self-test"`、Self-Test 专属 import 和单卡片 allowlist 替换为一次注册表解析。
- 把 `team_bug_service` 中可复用的 JQL、聚合、产品线与指标模型迁入通用卡片 owner；保留 Group 常量与 Jira/产品线业务常量的单一来源。
- 删除只服务 Self-Test 的 service 包装、重复 fingerprint 组装和专属统计 builder 注入。
- 删除 `createJiraCustomerPlaceholder`，三张卡片统一通过同一个组件挂载。
- 将固定的 `jiraSelfTest` 展示缓存键改为通用 `card_key` scope；账号切换和注销仍由全局生命周期统一清理。
- Dashboard 卡片注册只声明组件和 `card_key`，不复制 Jira 页面模板、查询或状态处理。
- 迁移完成后不保留新旧双读、双写、fallback 或废弃 API；已有有效 Self-Test SQLite 数据原位兼容，只有 definition fingerprint 不匹配时按既有规则返回 `no_snapshot`，不得自动发起远端查询。

新增抽象必须替换至少 Self-Test 与两张新卡片的重复需求。禁止创建只有转发作用的基类层级、每卡片一个文件的薄包装或新的通用 repository。交付时报告生产代码新增、删除和净变化；重构部分以净减少为目标，新业务增加不得掩盖可删除冗余。

## 前端布局

Jira 页面展示三张共享卡片：Self Test、Task、Customer Feedback。每张卡片拥有独立周期选择和结果状态，但共用页面已应用的用户 JQL。标题分别为：

- `Self Test Jira Statistics by Product Line`
- `Task Jira Statistics by Product Line`
- `Customer Feedback Jira Statistics by Product Line`

自定义标题、标签与展示描述统一拼写为 `Self Test`；Jira 原始字段值 `"Channel of Reporter" = "Self-Test"`、字段匹配及内部标识 `self-test` 保持原样，不做展示文案替换。

Dashboard 可分别引用三张卡片，引用的仍是同一组件实例类型与对应 `card_key` 数据，不建立 Dashboard 变体。既有卡片高度、滚动、产品线、指标切换和图表机制保持共享。

共享排行组件将当前值与上一周期值绘制为等宽、并列的横条，共用数值坐标：

- 白主题当前/上期使用 `#9DD5FA` / `#FFD3AE`，黑主题使用 `#789AAF` / `#B99B85`；Projects 保持既有浅蓝色。
- 每根非零柱显示完整的 `数量 · 占比%`。分母是本图该周期所有人的数量合计，两个周期分别计算，四舍五入后的总和约为 100%；零总量不生成 NaN。
- 标签优先在柱内右端，短柱放不下时完整伸向右侧，不隐藏、不截断，也不改变数值比例或柱长。当前周期外侧差值接在完整标签之后，避免重叠。
- 当前周期外侧只显示 `↑ 数量`、`↓ 数量` 或 `No change`；tooltip 保留日期范围、原始数量及增减数量，去除环比百分比。
- 白主题标签使用清晰深色，黑主题使用浅色。主题切换即时更新柱色和标签，不重查、不重置筛选、布局或柱体动画；`prefers-reduced-motion` 下禁用条形过渡。
- 只在当前或上一周期出现的人员仍保留，按当前值、上一周期值与既有姓名规则排序。

颜色和图表行为由共享样式与排行组件负责，不为单张卡片增加专属绘制分支。卡片不展示动态 JQL；卡片响应不返回展示用 JQL 字段，真实执行 JQL 与 SQLite 快照匹配继续由后端 owner 保存和使用。

## 验证与验收

- Core：三个定义生成确定性 fixed/effective JQL；用户 JQL、周期和 `ORDER BY` 组合正确；Customer Wireless labels、resolution、project 排除与互斥归类正确。
- 统计：三张卡片统一按 creator 展示人员；Wireless 优先；同一 issue 不重复；总数、五线合计和明确未归类计数保持守恒。
- 周期：四个 current/previous 自然周期边界正确且互不重叠；闰年、跨年、季度切换和周一边界有确定性测试；两条 JQL 除时间条件外完全一致。
- Web：三个 `card_key` 共用 API、任务和 SQLite owner；账号、卡片和 session 隔离；GET 无远端；Refresh、Reuse、失败、取消、旧任务防覆盖和服务重启行为一致。
- 兼容：现有 Self-Test 结果口径不变；旧有效快照按 fingerprint 规则恢复；Customer 占位偏好迁入正式卡片 scope，不产生第二份 owner。
- 前端：Jira 页面三卡片与 Dashboard 三种引用均使用共享组件；周期、轮询、首帧缓存、账号切换、空结果和错误呈现一致。
- 对比图：所有指标和卡片共用等宽并列横条；当前/上一周期数值、日期、各周期内部占比及外侧数量差一致；仅当前人员、仅上期人员、上期为零、持平和减少均正确呈现。
- 质量：删除专属分支、占位和重复封装；无临时诊断、废弃路径和未使用样式；后端、Core、前端完整相关测试、lint、build 与 `git diff --check` 通过。

## 非目标

- 不开放自定义卡片编辑器或任意 JQL 卡片创建。
- 不修改 Jira 原生数据、创建 Issue 或写回字段。
- 不为 Dashboard 增加独立 Apply、Refresh 或远端查询入口。
- 不新增第三方依赖，不改变认证与凭据生命周期。

## 2026-10-19 基础范围与活动指标的最终区别

`core/jira/jql.py::JIRA_PRODUCT_LINE_SCOPES` 为三类卡片基础范围的唯一声明，`product_line_jql/card_product_jql/creator_product_line`共同消费它：

| 产品线 | Self-Test/Task 创建人组（Customer 不限定） | 项目 |
|---|---|---|
| China Operator | fae-iptv-qa | IPTV |
| Smart Device | fae-SH-qa | SH |
| TV | fae-tv-qa | TV |
| Global Operator & STB | fae-stb-qa | OTT |
| Wireless Connection | fae-wifi-qa | IPTV、TV、SH、OTT |

Self-Test/Task 基础远端全集是五个“creator组 AND project AND 卡片来源”分支的OR；Customer 为五个“project AND 卡片来源”分支的OR，不含 creator 条件，不再追加外层五组全集或creator NOT条件。Invalid在对应基础范围追加resolution=Invalid。各卡issue type、Channel、用户条件与ORDER BY保留。Self-Test/Task 多组创建人仍遵循既有优先顺序；Customer 按实际来源及 project 归属，Wireless 优先。基础 fingerprint 包含生成后的 productScope，本次 Customer 范围变化自然使旧基础快照失配，不强制刷新或迁移；活动来源及作者归属不变。

Current JQL按指标区分：Bugs/Tasks/Components与Invalid显示对应产品线、当前展示周期的基础范围；Comments/Verify显示实际执行的年度活动来源查询，各产品线相同，切换周/月/季不会伪造一条未执行的活动查询。既有`aggregate_annual_facts`按issue.createdAt将年度facts选入展示周期，不按comment/verify事件发生时间重新筛选；此聚合规则未改。

基础definition fingerprint纳入完整productScope；既有API仍同时校验fingerprint和两期真实JQL，旧基础快照自然失效，不改schema、不强刷、不删数据。活动范围和作者归属未改，comment/verify metric_source_key继续按原card+query+roster+metric生成，可复用匹配旧活动facts；不人为升级活动版本。
