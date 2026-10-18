# Jira 卡片 Current JQL 展示设计

## 目标

所有 Jira 统计卡片增加一个只读文本区域，实时显示当前卡片所选 issue 集合对应的 Current JQL。JQL 随用户已应用条件、卡片类型、产品线和时间周期变化；Invalid 增加自身属性约束，Comments、Verify 不增加指标条件。

本功能只解释当前本地拆分视图对应的 Jira 范围，不改变现有年度数据一次获取、MySQL 快照持久化和前端本地交互方式，也不显示 Previous JQL。

## 权威边界

- Core 继续是 JQL 业务规则的唯一 owner。前端不拼接 JQL，后端接口不复制卡片或产品线规则。
- 现有 `core.jira.services.filter_service.compose_jql` 继续负责组合用户 JQL 与固定条件，保留 `OR` 和 `ORDER BY` 的既有处理。
- 现有 `core.jira.services.jql_statistics_cards.card_definition`、`effective_card_jql_pair`、`core.product_lines` 和 FAE-QA 分组定义继续提供卡片、周期、产品线及人员范围。
- MySQL 查询快照仍是有效结果 owner；展示 JQL 不是新的资源 ID 集合，不参与审查、导出或批量动作。

## JQL 规则

Core 入口 `current_card_jql_by_product_line_and_metric(card_key, user_jql, period, today=None)` 一次返回五个固定产品线、四个指标的 Current JQL。

每条 JQL 组合：

```text
用户已应用条件
AND 卡片固定条件
AND 产品线条件
AND Current 时间条件
```

卡片固定条件沿用现有定义：

- `self-test`：Bug 与 Self-Test Channel 条件。
- `task`：Task 条件。
- `customer-feedback`：Customer Feedback 现有业务条件。

产品线条件复用当前本地拆分语义：

- China Operator、Smart Device、TV、Global Operator & STB 使用 `ProductLine.jira_project_keys` 形成 project 条件，并排除归属 Wireless Connection 的 creator，保持 Wireless 优先归类规则。
- Wireless Connection 使用现有 `fae-wifi-qa` creator group 条件。
- 产品线名称、项目键和组名均从现有 owner 读取，不在 Web 层建立映射表。

周期条件只生成 Current 范围，复用 `jira_period_ranges` / `jira_period_condition`。Comments、Verify 与首项 Bugs/Tasks/Component/s 使用同一基础 JQL；Invalid 在同周期、同产品线基础上额外组合 `resolution = "Invalid"`。指标键复用现有 `bugCount`、`commentCount`、`verifyCount`、`invalidCount`。

## 接口与数据流

现有 `GET /api/jira/cards/{card_key}/statistics` 及查询响应一次返回同一年度快照派生的四套视图：

```json
{
  "periods": {
    "week": { "productLines": [], "currentJqlByProductLineAndMetric": {} },
    "month": { "productLines": [], "currentJqlByProductLineAndMetric": {} },
    "quarter": { "productLines": [], "currentJqlByProductLineAndMetric": {} },
    "year": { "productLines": [], "currentJqlByProductLineAndMetric": {} }
  }
}
```

后端依据当前账号已发布的 `userJql` 和卡片 `card_key`，从同一个有效年度 snapshot 调用既有 `local_statistics` 分别派生 week/month/quarter/year，并为每套视图生成对应 Current JQL。接口不返回原始 issue，不恢复已刻意隐藏的 `activeJql`、`activeComparisonJql`、`userJql`，也不返回 Previous JQL。

产品线、周期和统计方式点击均从这份本地 payload 同步选择数据与 JQL，不访问 API、数据库或 Jira。Apply/刷新仍使用既有 query owner；任务完成后 GET 返回并原子替换完整四周期 payload。POST query 不再承担 period 偏好、reuse 或切换职责。Dashboard 与 Jira 页面继续挂载同一个 `createJiraStatisticsCard`，没有页面专用实现。

## 前端展示

`createRankingCard` 增加一个窄的选择变化回调，向 Jira 卡片报告当前 `productLine` 和 `mode`。共享排名组件不理解 JQL。

`createJiraStatisticsCard` 在周期控件下方绘制只读 Current JQL 区域：

- 使用等宽字体、自动换行并允许用户选择复制。
- 通过 `textContent` 写入外部文本，不通过 `innerHTML` 注入 JQL。
- 产品线或 mode 变化时选择 `currentJqlByProductLineAndMetric[productLine][mode]`。
- Comments、Verify 与基础指标映射为同一 JQL；Invalid 映射为带 resolution 约束的 JQL。
- 卡片响应或展示缓存校准时同步更新完整四周期 payload；可丢弃缓存仅服务首帧展示，后端响应仍负责校准。
- `no_snapshot` 状态显示 Apply 提示，不伪造当前集合。
- 刷新失败且旧结果继续展示时，保留旧结果对应的 JQL，不显示未发布范围。

## 变更范围与代码量

预计只修改：

- `core/jira/jql.py`
- `core/jira/services/jql_statistics_cards.py`
- `web/backend/smarttest_web/jira_api.py`
- `web/frontend/src/widgets/ranking-card.js`
- `web/frontend/src/widgets/jira-statistics-card.js`
- 对应卡片样式与现有测试文件

不新增 endpoint、数据库字段、Alembic 迁移、缓存 owner、远端请求或第三方依赖。生产代码净增目标不超过约 70 行；若实现证明确需明显超过该范围，停止并向 Coco 报告原因。

## 异常与安全

- 未知卡片或产品线继续沿用现有明确失败边界，不回退到无产品线限制的 JQL。
- 不记录完整 JQL，不改变现有日志内容。
- JQL 只在已认证卡片接口中返回；前端转义显示。
- JQL 映射缺项时显示不可用状态，不静默显示其他产品线范围。

## 验收标准

1. 三张 Jira 卡片均显示一条 Current JQL。
2. 五个产品线切换后文本立即同步，且不产生新网络请求。
3. 周、月、季度、年度切换后数据与文本立即同步，且不产生新网络请求。
4. Apply 新用户条件后文本包含新的用户条件。
5. Comments、Verify 与基础指标 JQL 相同；Invalid 增加 `resolution = "Invalid"`。
6. Dashboard 与 Jira 页面行为一致。
7. 不返回或显示 Previous JQL，不暴露内部查询状态字段。
8. 现有一次取数、本地拆分、MySQL 快照与任务隔离行为不变。

## 执行清单

- [x] 为产品线条件和 Current JQL 映射增加 Core 行为测试。
- [x] 实现 Core 唯一组合入口，复用现有 owner 与 `compose_jql`。
- [x] 为卡片接口字段和敏感内部字段边界增加后端测试。
- [x] 在现有卡片响应中加入四周期统计视图及各自产品线×指标 JQL 映射。
- [x] 为排名组件选择回调及 Jira 卡片同步行为增加前端测试。
- [x] 增加 Current JQL 只读文本区域及最小样式。
- [x] 验证三张卡片、周期、产品线和 mode 交互均只读取本地 payload，不触发额外查询。
- [x] 运行受影响测试、前端构建、真实 Web 页面检查及 `git diff --check`。
