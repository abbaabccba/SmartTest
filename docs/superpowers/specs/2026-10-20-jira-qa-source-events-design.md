# Jira QA 原始动作获取与入库（第一阶段）

## Coco 已确认的目标与范围

本阶段先获取并保存原始数据，后续再讨论明细浮层展示，不改前端。

- 保存 QA 评论的原文、作者、原始 ID、创建及更新时间。
- 保存 QA 上传文件、图片的名称、上传时间、作者及元信息，不下载文件或缩略图本体。
- 保存 QA 全部 Verify 操作历史，包括作者、时间、原历史 ID、前后状态。
- 非 QA 动作不写入本阶段证据；QA 身份复用现有人员配置。
- 卡片统计口径、查询范围、刷新时间、手动刷新权限和其他功能保持不变。

这里的“全部历史”指现有查询范围内 Jira 的全部 QA Verify 历史，不扩大为全站动作检索。现有活动查询按 issue.created 的当前年/上一年和已应用 JQL 取 issue，动作本身不另加时间截断。

## 现有 owner 与复用

- `core/jira/gateway.py`：复用评论分页补全和附件元信息获取；Verify 当前在 changelog 不完整时重取一次，仍不完整则失败。实施时核对现有 SDK 与目标 Jira 支持的完整历史分页能力，不写自定义协议或静默接受截断数据。
- `core/jira/services/jql_statistics_cards.py:load_activity_qa_roster()`：复用 `personnel.json` 中 FAE-QA employees 的账号，按现有规范化账号匹配作者；没有产品线分配的 QA 仍保留，归属为空。
- `core/jira/mapper.py:last_verification()`：当前 Verify 谓词为 status 变化、fromString 含 resolved、toString 含 verified（大小写无关）且时间可解析。扩展全部历史解析并复用同一谓词，现有最后一次 Verify 计数不变。
- `web/backend/smarttest_web/jira/analytics_refresh.py:refresh_metric()`、`JiraAnalyticsRepository`：沿用 pending 写入、页事实、失败/取消、activate 有效指针发布机制。
- 不使用通用 `replace_comments/attachments()` 写 QA 子集；这些入口表示完整 issue 详情，删除重写会污染其他用途。

## 拟议存储

在现有 MySQL metadata 与 analytics repository 下新增一张 `jira_analytics_qa_events`，不新增数据库、缓存或独立存储 owner。

| 字段 | 内容 |
|---|---|
| snapshot_id | 原统计来源快照 |
| issue_id | 原 Jira issue ID |
| event_kind | comment / attachment / verify |
| source_id | Jira 原 comment / attachment / history ID |
| 作者字段 | identity、account、display name，保留来源信息 |
| 动作时间 | 原始带时区时间；评论同时保留创建、更新时间 |
| payload_json | 所需原始评论正文、附件元信息或匹配的 Verify 状态变更 |

完整唯一键为 snapshot_id + issue_id + event_kind + source_id。三段 ID 使用现有 String(191)、类型 String(16)，utf8mb4 最大约 2356 字节，小于 MySQL 3072 字节；超限报错，不截断。外键指向现有 snapshot。Verify 一条 history 一条证据，保存该 history 内符合原谓词的 status items；不使用时间拼接虚构 ID。

MySQL 为应用内唯一持久 owner；Jira 为远端原始来源。本阶段数据要求跨重启保留，与有效统计版本一致；不能从数量事实反向重建。按快照关联隔离账号、卡片、查询与版本，依赖当次 QA roster 及证据格式版本。正文只在证据表，不塞进每次聚合加载的 fact_json；不进入 Redis 展示缓存。

生命周期沿用 analytics snapshot，不新增定期回收。当前未找到既有定期回收入口，因此持续采集会增长，首期不擅自删除历史；后续保留策略另议。一次读取一页 issue，在事务内批量写其 QA 动作，负载随页内动作数量增长，不把全站历史一次载入内存。

## 采集与发布

1. 保留现有查询窗口和固定条件。Comment 页获取完整评论及 attachment 元信息；Verify 页获取完整 changelog。
2. 只有附件自身 author 能匹配现有 QA 账号时，才保存上传动作。QA 评论引用非 QA 文件不算 QA 上传；QA 独立上传也记录。缺乏作者证据时不猜测，不根据显示名或时间相近推断。
3. 评论原文保留已有文件引用，不另外解析或构造评论—附件关联。附件只保存元信息，不访问 content/thumbnail 文件地址。
4. QA 原始动作和该页原统计事实在同一 pending 事务写入；沿用原 native ID 幂等写入，不改变数量 builder 的结果。
5. 元数据记录本次 QA roster/归属快照和证据格式版本；QA 身份不以未来配置回算本次历史。
6. 完整采集成功后，沿用 activate 原子发布。失败、取消、旧请求被取代均不发布不完整证据，旧有效快照继续保留。
7. 旧快照没有证据时表示未采集，不能解释成零条。由之后原有合法刷新采集，不在应用启动、GET 或打开页面时补历史。

## 调度和兼容边界

现有每日北京时间 05:00、共享 12 workers、每页 100 issues、手动 Comment 允许/Verify 不允许等控制点均不改变，定义入口为 daily_refresh.py 与 analytics_pages.py。

本阶段增加 Alembic 后继 revision（实施时确认实际 head），只建证据表；应用普通启动仍只检查版本，不自动执行迁移，不修改通用详情历史。迁移或远端历史能力不具备时如实报告，不引入 SQLite 或截断历史兜底。

## 执行清单

1. Mason 核对当前代码、SDK 与测试环境，确定完整 changelog 获取能力及附件作者字段。
2. 增加统一 QA 动作解析，复用 QA roster 与 Verify 谓词，保留原统计结果。
3. 增加 schema/Alembic 与原 repository 的批量证据写入，接入现有 comment/verify 刷新发布。
4. 验证 QA/非 QA/未分配 QA，评论原文原时间，完整分页，多次 Verify、最新操作者非 QA 但历史含 QA，QA 独立附件及引用非 QA 文件，不下载本体。
5. 验证事务、幂等、取消/失败/被取代不发布、账号及查询隔离、迁移和外键、现统计逐项不变。
6. Atlas 按实际 diff、测试及数据库读回证据验收。运行不了隔离 MySQL 或实际 Jira 验证时明确列为未验证，不能宣称已完成真实入库。

## 本阶段不做

不修改浮层、Download、前端布局；不扩大 JQL；不调整 Comments / Verify 计数、刷新权限、调度或并发；不下载附件；不自动迁移正式库；不提交或推送。
