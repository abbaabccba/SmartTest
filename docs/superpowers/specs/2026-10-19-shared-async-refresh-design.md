# 公共异步数据刷新、重试与有效结果展示

## 已确认目标

Coco 确认：分页失败最多请求 4 次，退避 1、2、4 秒，取消时停止；刷新失败不影响数据库已有有效数据展示。公共机制覆盖各数据业务，清理重复实现，不为 Jira 卡片增加专属重试或第二套任务管理。

本文件同时记录设计与执行清单。复用既有全局异步任务设计及 Web 数据库边界；当前持久化 owner 为 MySQL，以根 AGENTS.md 为准。历史文档中的 SQLite 描述不构成本次回退依据。

## 唯一职责边界

- `core/async_tasks.py` 保持进程内唯一任务调度、进度与协作式取消 owner。不重跑整个 runner，避免重复写入、发布、邮件或其他业务动作。
- 公共只读请求重试能力复用现有 requests/Atlassian 运输层与成熟 Tenacity 策略能力；依赖同步进入开发、运行及打包声明。Jira、Confluence gateway 读取走同一策略，删除局部重试循环及依赖层重叠重试。具体模块/接口名称在实施后记录为实际名称。
- 业务 owner 保留查询条件、权限、快照身份、事务、有效结果指针和来源版本。统一机制不猜测存储归属，不增加第二份查询结果、任务队列或缓存 owner。
- 前后端公共刷新反馈明确区分任务状态和有效结果可用性。服务端判定旧结果是否仍有效，前端不能从浏览器缓存推断有效性。

## 请求策略

首次请求计入总数，总请求不超过 4 次。第 1、2、3 次失败后分别等待 1、2、4 秒，无随机延迟。仅对可重复的远端读取生效；不重试业务写动作、数据库事务、认证或整个异步任务。

可重试：连接中断、代理连接错误、连接/读取超时、HTTP 502/503/504。HTTP 429 遵从有效 `Retry-After`，缺失或无效时使用上述固定间隔，总次数仍不超过 4。无效查询、HTTP 401/403、证书配置错误和其他确定性业务异常不重试。最后失败保留原异常及原错误分类。

任务的取消信号贯穿任务工作单元和只读请求。取消立即唤醒退避等待，不再启动新页、重试或其他读取；成功返回的在途请求也必须在处理/发布边界检查取消。同步 requests 已发出的在途 socket 无法由协作式 token 强杀，按现有网络超时退出；不承诺硬中断，也不在本次更换运输层。

任务取消、最后一次请求失败、新快照被替代时均不得将部分结果发布为有效结果。已有分页预算、任务调度、刷新权限、时间范围和定时计划保持原 owner 管理。

## 结果展示契约

刷新状态（排队、执行、失败、取消）和数据状态分别表达：

| 数据状态 | 刷新状态 | 展示 |
|---|---|---|
| 存在匹配的有效数据库结果 | 失败/取消 | 继续展示有效结果，同时显示本次刷新状态及错误 |
| 不存在匹配的有效结果 | 失败/取消 | 明确无当前条件有效结果，展示失败原因 |
| 存在有效结果 | 执行中 | 保留有效结果，显示刷新进度 |
| 成功零条 | 完成 | 展示有效空结果 |
| 数据库读取异常 | 任意 | 明确数据库异常，不伪装成无结果或自动远端回退 |

Jira 继续核对认证账号、卡片、已应用条件、definition/fingerprint、两期 JQL 及完整 period views；Projects/Release 等继续核对既有授权会话快照、scope/revision。失配旧范围不能作为当前条件结果。失败不覆盖 active 指针，不删除旧结果；GET 不触发远端刷新。

## 接入与清理范围

1. 公共取消/重试入口与 gateway：Jira 普通/全量/活动分页、详情和元数据读取；Confluence catalog/页面/历史/用户读取。明确排除创建、修改、上传等写动作。
2. 公共任务调用链：Jira 三类统计、Projects 后台刷新、Confluence 详情同步、Release、定时刷新；复合审查/邮件只对其读取步骤使用策略，不重跑动作。
3. 公共结果反馈与消费：Jira/Dashboard、Projects、Release 复用数据可用性与刷新反馈契约，不各自复制失败时隐藏结果的分支。
4. 清理被公共策略替代的 Jira 502、Confluence 用户局部重试及重复状态处理；保留正式阶段日志与必要回归测试。

## 执行与验收清单

- [x] 公共请求策略、取消等待与单一重试预算；依赖声明完整。
- [x] 全部上述 gateway 读取及异步调用链接入，删除重复循环；不重跑写动作。
- [x] 公共结果状态和展示消费统一，失败保留匹配数据库结果，失配明确无当前结果。
- [x] 回归：4 次上限、1/2/4 等待、429、确定性错误、等待中取消、在途返回后取消、部分结果不发布。
- [x] 回归：Jira/Confluence、Projects/Release/定时刷新，以及跨账号、scope/version、GET 不刷新边界。
- [x] 前端测试、lint/build、日志与边界检查、`git diff --check`；不访问真实远端大查询，不用正式库写入验证。
- [x] 最终列出实际入口、接入清单、删除项、依赖与生产代码净增；缺隔离测试库时如实报告验证限制。

本次不修改代理配置，不新增分布式队列、自动任务重跑、强制刷新或数据迁移；不改变长期设备流、测试执行与邮件发送语义。

## 实际落地与验收（2026-10-19）

- 公共只读入口：`core/read_requests.py::read_request`。`READ_MAX_ATTEMPTS=4`、`READ_RETRY_DELAYS=(1,2,4)`；Tenacity 9.2.1 负责重试预算、异常判定和最终原异常传播。429 支持秒数与 HTTP-date；无效/过期 header 回到固定间隔。证书、认证、权限、查询/业务异常不重试。
- 取消 owner：`core.async_tasks.current_cancellation/cancellation_context/check_cancellation` 与 `CancellationToken.wait`。`AsyncTaskManager._run` 绑定真实任务上下文，不重新执行 runner。Jira全量线程池使用独立 `copy_context()`，活动分页线程绑定 job token；Confluence catalog 使用已有父子任务关系，取消传播不靠全局可变 token。
- Jira gateway：普通/全量/metric搜索、评论/changelog补齐、详情、字段/filter/建议/用户及附件元数据读取均调用同一入口；创建、更新、上传仍调用原API一次。原502独立循环删除。
- Confluence gateway：catalog CQL分页、页面/历史/父子页、用户读取均接入；删除用户独立3次0.2秒重试与废弃 `_status/sleep`。Atlassian客户端显式关闭 `backoff_and_retry`、`retry_with_header`，避免依赖重试叠加。
- 接入链：Jira卡片/日刷新、Projects后台与Confluence详情子任务、Release两种同步、catalog父子任务、审查中的读取。Confluence角色名/页面发现等best-effort边界保留取消原异常；缓存发布前检查取消。邮件、审查runner、数据库和写动作不整体重跑。
- 数据契约：业务API返回 `dataState=ready/partial/none`。Jira只将匹配active的完整period views标ready，执行中可显示partial但失败不将pending部分结果当有效结果；active优先。Projects依据原query结果判定，Release依据已验证会话scope的数据库读取判定。有效空结果仍ready。
- 前端公共反馈：`web/frontend/src/async-feedback.js::refreshFeedback`。Jira、Projects、Release/Workbench共用失败/取消且数据ready时的“Showing saved data.”说明，不把浏览器缓存当权威。没有匹配数据时不作此声明。
- 依赖：`web/backend/requirements.txt`、`core/devtools/scripts/init_venv.py`；desktop main.spec、portable TOOL_HIDDEN_IMPORTS/TOOL_SMOKE_MODULES 声明Tenacity，正常静态import链包含公共入口。本轮未重建desktop安装包。

验证：核心/后端聚焦191项通过，涵盖公共策略、真实任务退避取消、线程token传播、写动作不重跑、Jira匹配/失配旧结果、GET不远端、Release真实路由scope重放、Projects可用性和daily取消。前端完整315项通过，随后失配scope的partial替换回归加入，受影响Jira文件31项通过（当前共316项），lint/build均退出0；产品边界CLI与diff check通过。完整backend及MySQL持久化集成因缺少 `SMARTTEST_TEST_DB_*` 隔离测试库配置阻塞；未使用正式库替代。未执行真实远端请求或修改正式数据。

保留限制：在途同步socket只能按原超时结束；测试未覆盖真实代理故障下的时延及打包后运行。公共策略不改变并发预算、调度时刻、筛选/授权边界或远端刷新入口。
