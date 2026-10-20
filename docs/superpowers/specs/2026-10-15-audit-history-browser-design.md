# 定期审查历史数据浏览（已批准方案落地记录）

Coco 已确认本方案及执行清单，本文记录实施边界，不增加审批步骤。

## 页面与接口

页面按“审查与报告 → Jira 历史数据 → Confluence 历史数据 → 执行历史”排列。两个历史组件独立展示，不使用 tabs；各自日期范围查询、倒序分页，每页 10 期。Confluence 每期一行，Core PRODUCT_LINES 的四条产品线横向分组，每组显示项目数、通过、未通过；点期次显示各线已保存项目明细。新历史组件无动画、无悬停变色；已有导航菜单保持不变。期次行可查看该期 SQLite 已保存明细，Confluence 按产品线展开对应项目。

扩展既有 `AuditEmailHistory` 与 `/api/audit-email` owner，只读 `audit_email_runs`，复用现有 run 详情接口，不增加 schema、历史库、远端请求或邮件发送。Coco 已明确批准所有已登录账号共享定期审查历史：列表、汇总、详情、报告来源验证及附件读取一致共享，未登录仍禁止访问。create/save/调度领取/执行身份和复查基线仍沿用原账号 owner，不改为共享执行。列表按已保存日期倒序，日期缺失置后，不补年份；同日期按保存顺序倒序。日期筛选只匹配已记录的完整日期，截图月日不推断年份。

Jira 列为审查日期、来源、总数、修改后通过数、修改后通过率、通过、未通过、通过率。总数/通过/未通过/通过率读取本期 `summary.jira`。“修改后通过数”和“修改后通过率”分别读取已保存的 `reports.jira.remediation.currentSummary.passed/rate`，代表上期原 JQL 范围本次复审的通过数与通过率，不是按 updated 时间筛选；无复审汇总才显示“未记录”。点行分别展示本期新建审查与上期本次复审的完整统计和各自已保存 issue 明细，禁止混用两套通过数。10/9 实际本期为 74/60/14，已保存复审为 123/97/26（18:01:35），二者范围不同。此前仅检查 summary.jira 误判该数据未保存，现已核对真实 SQLite payload 并纠正展示，不改审查流程或回填历史。

Confluence 列为审查日期、产品线、审查项目数、通过、未通过。项目数从已保存 projects 按产品线计数；通过/未通过沿用已保存更新检查点 [updated,total] 口径，并明确标注检查点而非项目。截图没有 projects，则项目数和明细显示“未记录”；截图原值保留并标注来源。

## 执行清单

- [x] 扩展 SQLite owner 的登录账号共享读取、独立日期过滤与分页列表，保留按账号执行边界。
- [x] 增加只读 GET 接口及前端 API 接入。
- [x] 实现两独立列表与已保存明细浏览，保留现有触发和执行历史。
- [x] 验证分页、日期、缺失数据、共享读取与执行隔离及前端交互；运行测试、构建和 diff 检查。

## 查询诊断

沿用 `core.logging.smart_log` 正式输出 `audit_email_history`，记录当前数据库路径、账号、类型、数据库候选期次数、登录账号共享可见期次数、日期筛选前后行数、offset、日期与返回条数，不记录凭据、会话或 SQL/JQL。根因只读核对：公共截图4期、真实执行5期均归属 ping.xiong，旧账号隔离使其他账号只见公共截图。Coco 已批准改为所有登录账号共享读取；无数据迁移、历史回填或账号归属改写。

## Coco 后续确认：删除截图补录

停止 `AuditEmailHistory.__init__` 的截图种子写入，不在启动时自动清理生产数据。此次经批准操作先备份既有 SQLite，定点删除 screenshot-0807/0814/0824/0828 四条已证实补录记录；前三个真实 run 只移除两类报告 sourceIds 中共 11 个已删除 seed 引用。真实 run、自身引用、summary、已保存 HTML 和附件均保留。历史 HTML 为当时发送报告的原始存档，保留当时对比内容，不伪造或重新渲染历史。

## 年度按人员汇总的只读调查

历史 Jira issue 的 creator 仅为显示名字符串；独立 jira_issues 已缓存 creator_identity/account/display_name，映射来源为 Jira PersonRef。人员配置已有账号和产品线 assignments；现有年度事实 owner 可复用，但审查历史本身未保存稳定 creator 身份，且跨周及复审存在重复 issue。年度审查统计需另行确定时间、去重和身份映射口径，本轮不新增年度功能。

清理执行结果：已删除4条截图run，3条真实run仅清理11个sourceIds引用，保留5条真实run与32个附件，HTML/summary/附件字段哈希一致；重开owner种子仍为0、数据库integrity_check通过。备份：`C:/Users/SH171300-1522/AppData/Local/Amlogic/SmartTest/web/backups/smarttest-web-before-remove-screenshot-seeds-20261015-165620.db`。

人员映射核对：当前5期真实审查共666条issue、666个唯一key，均可在jira_issues当前缓存匹配稳定creator_identity/account，显示名也一致。缓存139893条issue的三类creator字段均非空；当前FAE-QA名册83人均有account、81人有assignments。以上仅证明当前缓存可关联，不证明全年审查覆盖完整或历史时点身份可追溯。

## Coco 已批准：同周按创建人保存违规数/创建数

复用 `summarize_audit` 与邮件报告 owner：同一已冻结新建 Jira 审查范围内先按 issue key 去重，再按真实 creator identity（缺失时使用明确 account）聚合 `people`，保存 identity/account/displayName/createdCount/violatedCount。不得按显示名猜账号；不同明确身份同名不合并。summary.period 从本次冻结 scope 的 startDate/endDate传入；复审使用基线自身 scope，禁止套用当前新建周。

邮件 HTML 与实际 XLSX 附件的同一人员表原“违规 Jira 数量”列同步改“违规数/创建数”（如2/10），创建人文字与违规 Jira 号链接保持，只展示有违规人的行，不增加零违规行。人员分组仅在既有 `core/jira/audit/models.py:group_creator_issues` 复用，不引入 exporter 到 email 的反向依赖。`IssueAuditResult` 最小追加稳定身份字段，在 `JiraAuditUseCase._audit_one` 从原始 PersonRef传递，保持规则、AI判定和调度不变。未来执行随现有 SQLite summary整体持久化，不回填既有生产历史，不发送测试邮件，不新增年度页或年度指标。
