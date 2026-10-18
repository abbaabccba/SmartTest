# SmartTest Web 长期开发设计

> 当前状态（2026-10-16）：Web 已正式切换为 MySQL 持久存储与 Redis 可丢弃缓存。当前运行配置、迁移和测试入口见 [backend README](backend/README.md)，长期数据库与缓存规则见 [AGENTS.md](../AGENTS.md)。以下各阶段设计保留历史原文；其中 SQLite 描述仅反映当时实现，不代表当前运行架构。

## 1. 文档定位

本文档是 SmartTest Web 长期开发的产品与架构基线，记录已经由 Coco 确认的目标、数据权威、权限原则、模块边界、交付顺序和验收标准。后续采用“单模块设计、单模块实现、单模块验收，最后整合”的方式推进，不反复重新整理已经确认的共同背景。

本文档不授权一次性实现全部模块。每个模块开始前仍需形成该模块的具体设计与验收标准并由 Coco 确认；模块设计不得改变本文档已经确定的产品边界。

## 2. 产品定位

SmartTest Web 的长期定位是面向测试团队的完整管理平台，而不只是数据看板。平台最终覆盖：

- Home 管理工作台；
- Confluence 项目聚合与项目进度；
- 项目阶段审查与历史回溯；
- 人员、组织和数据权限；
- 人员工作量分析；
- 管理岗客观参考指标；
- Jira、Redmine 等第三方缺陷/任务数据；
- 后续接入 SmartTest 测试任务、执行和报告数据；
- 现有 Wi-Fi Database。

当前阶段以访问、聚合、分析和 Web 自有历史记录为主，尽量不修改 Confluence、Jira、Redmine 等第三方平台数据。

## 3. 已有基础

### 3.1 Web 产品边界

现有 `web/` 已采用以下依赖方向：

```text
web/frontend -> web/backend -> core
```

- 前端使用 Vite、SmartTest 主题样式、Chart.js、ExcelJS 和 jsPDF；
- 后端使用 FastAPI；
- 浏览器只调用 Web API，不直接访问 `core/`；
- Web 后端负责认证、权限、请求编排、传输模型和 Web 自有持久化；
- 核心业务继续由 `core/` 中的既有 owner 负责；
- 现有 Wi-Fi Database 保持只读查询边界。

### 3.2 可复用能力

后续开发必须先复用仓库已有能力，除非 Coco 明确要求独立封装：

- `core/config/personnel.json`：人员、职级、汇报关系、产品线分配、专业领域、系统角色和第三方账号映射；
- 现有 SmartTest 认证体系：账号身份和登录逻辑；
- `core/tools/common/project_weekly_audit/`：Confluence 跨 Space 项目发现、稳定项目身份、项目过滤、页面历史、周更新审查、手动/定时审查、审查结果和报告；
- 既有 Jira、Redmine 集成；
- 既有公共调度、日志和配置能力；
- 后续既有 SmartTest 测试任务、运行、步骤和报告 owner；
- 现有 Wi-Fi Database 查询和图表能力。

Web 不建立平行的人员清单、项目清单、账号体系、Confluence 采集器、审查器、调度器或测试执行模型。

## 4. 总体架构

采用模块化单体，保持一套 Web 前端、一套 Web 后端和统一部署边界。模块内部独立，跨模块只通过明确接口协作。

```text
Web Frontend
  -> Web Backend
       -> 身份与权限模块
       -> Home 聚合模块
       -> 项目模块
       -> 审查与历史模块
       -> 人员模块
       -> 工作量模块
       -> 参考指标模块
       -> Wi-Fi Database 模块
       -> core 既有业务 owner
       -> Web 自有数据库
```

### 4.1 Web 后端职责

- 复用现有认证并识别当前账号；
- 将账号映射到 `personnel.json` 人员；
- 计算页面和数据范围权限；
- 编排 Confluence、Jira、Redmine、SmartTest 等数据；
- 将核心模型转换为稳定的 Web API 契约；
- 保存 Web 自有的项目快照、阶段审查、指标结果和审计记录；
- 对外部依赖失败、超时、权限不足和部分数据缺失提供明确状态；
- 禁止浏览器绕过后端直接访问第三方平台或 `core/`。

### 4.2 Web 前端职责

- 路由、布局、交互和展示状态；
- 通过 API 获取已经过权限过滤的数据；
- 显示加载、空数据、部分可用、失败和无权限状态；
- 不推导管理范围，不自行拼接第三方业务关系，不保存权威业务结果；
- 使用接近 Fluent UI 的统一视觉体系。

## 5. 数据权威与读写边界

### 5.1 项目数据

- Confluence 是项目主数据源；
- 项目档案、阶段、计划、进度、负责人、里程碑和审查材料以 Confluence 为主；
- Jira、Redmine 等第三方缺陷/任务平台为辅助数据源；
- SmartTest 后续提供测试任务、执行量、结果和报告数据；
- 多来源发生冲突时不静默覆盖，页面应展示来源和更新时间；
- 当前阶段不通过 Web 修改第三方平台数据。

项目范围直接复用现有 Confluence 跨 Space 发现、稳定身份和过滤规则，不维护 Web 专用项目清单。

### 5.2 人员数据

`core/config/personnel.json` 是人员身份、组织关系、职级、产品线分配和系统角色的唯一来源。Web 不复制人员档案。当前人员模块以读取和展示为主，人员配置编辑不在当前范围。

### 5.3 Web 自有数据

以下信息由 Web 自有数据库持久化：

- 定期项目快照；
- 管理岗执行阶段审查时形成的正式节点快照；
- 审查人、审查时间、审查结论、意见和证据引用；
- 快照对应的数据来源、采集时间和统计口径版本；
- 工作量与客观参考指标的周期性计算结果；
- 与敏感操作相关的审计记录。

Web 自有数据库与 Wi-Fi Database 分离。Wi-Fi Database 继续只接受参数化只读查询，不承担项目历史或审查数据写入。

## 6. 身份与权限

### 6.1 身份来源

- 复用现有 SmartTest 账号登录和身份体系；
- 登录账号匹配 `personnel.json` 的 `account`；
- 未匹配到有效人员配置的账号不得默认获得业务数据权限；
- 认证、会话和人员身份由后端确认，前端不得声明身份。

### 6.2 I 岗权限

I 岗默认只能查看：

- 自己的工作量和客观参考指标；
- 自己参与或负责的项目；
- 与自己业务范围相关的审查和历史信息；
- 角色授权的公共业务数据。

### 6.3 M 岗权限

M 岗可见范围由以下关系取并集并去重：

1. 根据 `reports_to` 递归得到直接和间接下属；
2. 若本人是产品线负责人，加入该产品线分配的全部人员。

高级 M 岗通过汇报树自然覆盖下级管理岗及其团队。M 岗只能查看其负责范围内人员、项目、工作量和参考指标。权限结果应保留命中原因，如“汇报关系”或“产品线负责人”。

### 6.4 强制约束

- 数据范围必须在后端执行；
- 页面、API、统计和导出复用同一权限结果；
- 不信任前端传入的人员账号、产品线或管理范围；
- 不允许通过直接请求 API 或导出绕过权限；
- 权限不足返回明确但不泄露目标数据的结果。

## 7. 业务模块

### 7.1 身份与权限模块

目标：建立所有后续模块共同使用的当前用户和数据范围基础。

主要能力：

- 复用现有登录；
- 当前用户资料；
- I/M 岗识别；
- 汇报树和产品线管理范围；
- 页面、API 和导出权限；
- 权限原因与诊断信息；
- 无匹配、停用人员和配置异常处理。

### 7.2 人员模块

主要能力：

- 人员目录和搜索；
- 部门、团队、Division、职级、岗位筛选；
- 汇报关系和组织树；
- 产品线负责人及成员；
- 人员详情、参与项目、工作量和参考指标入口；
- 只展示当前用户有权查看的人员。

当前不通过 Web 编辑 `personnel.json`。

### 7.3 项目模块

主要能力：

- 复用 Confluence 跨 Space 项目发现；
- 按产品线、年份、项目状态、支持模式、阶段、负责人筛选；
- 项目列表、状态、阶段、进度、负责人、更新时间和风险摘要；
- 项目详情，包括档案、阶段、里程碑、更新情况、成员、缺陷/任务和测试数据；
- 保留来源链接，允许回到 Confluence、Jira 或 Redmine 核查；
- 当前不修改第三方项目数据。

### 7.4 审查与历史模块

采用自动快照与人工阶段节点相结合：

- 定期任务采集项目快照，形成连续趋势；
- 管理岗执行项目审查时保存正式阶段节点；
- 人工节点不可覆盖，后续变化形成新记录；
- 支持项目、阶段和时间维度回溯；
- 记录审查人、结论、意见、证据、来源、采集时间和口径版本；
- 复用现有 Confluence 审查规则、调度和项目模型；
- 外部数据读取失败时记录失败事实，不以旧数据冒充新快照。

### 7.5 工作量模块

当前综合以下数据：

- Confluence：项目投入、更新、计划推进和周报活动；
- Jira：任务/缺陷创建、跟进、处理和完成情况。

后续加入：

- SmartTest 测试任务量；
- 用例执行量；
- 自动化执行量；
- 结果、报告和其他已确认的测试事实。

计算原则：

- 通过 `personnel.json` 的账号及第三方映射统一人员身份；
- 不将不同来源条目简单相加；
- 先保存和展示分来源原始指标，再根据未来确认的规则形成综合工作量；
- 每项指标带统计周期、来源、更新时间和口径版本；
- 缺少可靠数据时显示缺失，不估算、不生成模拟值。

### 7.6 客观参考指标模块

当前只为管理岗提供客观参考，不构成正式绩效考核：

- 不生成综合绩效分数；
- 不设置绩效等级；
- 不公开人员排名；
- 不直接关联奖金、奖惩或人事结论；
- 分来源展示工作量、及时性、完成情况、缺陷跟进和后续测试量等客观事实；
- 支持按周期、人员、团队和产品线查看趋势；
- 明确标注“参考指标”、统计口径、来源和更新时间。

未来只有在领导意图和业务规则明确后，才单独设计权重、评分、确认、申诉和归档机制。

### 7.7 Home 管理工作台

Home 是上述模块的权限化聚合视图，不独立拥有业务数据。

建议区域：

- 项目总数、进行中、待审查、延期和风险项目；
- 项目阶段分布和进度趋势；
- 当前用户有权查看的人员负载；
- Confluence 与 Jira 分来源工作量摘要；
- 管理岗客观参考指标趋势；
- 待审项目、即将到期、逾期和数据异常；
- 最近审查、项目状态变化和数据同步状态。

所有卡片和图表支持跳转到对应模块，不能在 Home 重复实现计算逻辑。

### 7.8 Wi-Fi Database 模块

保留现有 Peak Throughput、RVR、RVO、筛选、图表和导出能力。后续只做统一导航、认证、权限、视觉和运行环境整合，不改变其只读数据库 owner。

## 8. 快照与历史模型原则

正式字段在模块设计时确定，但必须满足以下原则：

- 项目使用现有稳定 Confluence 项目身份；
- 快照拥有唯一身份、采集时间和业务周期；
- 区分 `scheduled` 自动快照与 `manual_review` 人工审查节点；
- 保存规范化结果和必要的来源引用，不无界复制第三方完整页面；
- 人工审查节点不可原地修改历史事实，修订形成新版本并关联前一版本；
- 指标必须保存口径版本，以便未来规则变化后仍能解释历史结果；
- 定义保留、归档、备份和恢复策略后才能进入服务器正式运行；
- 敏感数据按最小化原则保存。

## 9. API 原则

- 按业务模块组织路由，不继续把所有页面逻辑集中在单个 `app.js` 或单个 FastAPI 文件；
- API 使用稳定传输模型，不直接暴露内部 Python 对象；
- 列表接口支持服务端分页、筛选和排序；
- 所有业务接口先完成认证与数据范围过滤；
- 聚合结果返回来源状态、数据时间和部分失败信息；
- 写入 Web 自有历史时支持幂等或明确的重复检测；
- 第三方异常不转换成成功空数据；
- 日志不记录密码、Token、Cookie 或第三方敏感响应。

## 10. 前端设计体系

- 整体视觉接近 Fluent UI：中性背景、清晰层级、轻边框、低阴影和语义色；
- 当前视觉基础采用已确认的 TemplateMo 608 布局与样式，并在运行代码中统一使用 SmartTest 命名；
- 复用 SmartTest 主题样式和语义 token，避免同时存在两套组件语言；
- 建立统一的导航、页面标题、筛选栏、统计卡、表格、状态标记、图表、空状态、错误状态和详情布局；
- 支持明暗主题时使用统一语义色；
- 桌面优先并保留合理响应式布局；
- 关键操作支持键盘和清晰焦点；
- 不用颜色作为状态的唯一表达。

## 11. 非功能需求

### 11.1 安全

- 后端强制认证和数据范围；
- 第三方凭据沿用现有安全 owner，不进入前端存储；
- 防止查询参数越权、导出越权和对象直接引用越权；
- Web 自有写操作保留审计记录；
- 错误响应不泄露连接串、凭据或内部堆栈。

### 11.2 稳定性

- 区分加载、无数据、无权限、配置缺失、外部系统失败和部分成功；
- 外部调用采用有界超时；
- 缓存和历史数据必须显示时间，不冒充实时数据；
- 定时任务可观察、可重试且避免重复写入；
- 服务器部署前完成数据库迁移、备份和恢复验证。

### 11.3 可维护性

- 每个业务行为只有一个 owner；
- 复用决策优先于新增代码；
- 模块之间使用明确接口，不读取对方内部存储；
- 保留能够保护权限、历史、数据口径和关键业务契约的测试；
- 不保留探索性测试、源码形状断言或重复等价用例。

## 12. Web 持久会话与账号级前端偏好

### 12.1 目标与边界

Web 登录会话与用户偏好由 Web 后端统一持久化，支持单台 Server 重启恢复、同一账号多设备同时登录，以及跨设备同步网页配置。继续复用现有 `LdapAuthenticator`，不引入 Redis、Keycloak 或第二套账号体系。

- 登录会话采用最后活动时间起 90 天滑动过期；主动退出、账号禁用或会话撤销立即失效。
- 每台设备持有独立会话；退出当前设备不影响其他设备，并提供撤销账号全部会话的后端能力。
- Cookie 只保存高强度随机 token；SQLite 只保存 token 哈希、账号身份、会话时间和撤销状态。
- 浏览器缓存、响应、日志和 SQLite session 表不得保存 LDAP 明文密码、Token 或 Cookie；LDAP 密码仅交给统一服务端 credential owner：Windows Credential Manager 持久化，Linux 以外部主密钥进行 AEAD 加密后将密文存入 Web SQLite。
- Server 重启后由持久 session 的 credential reference 恢复 Confluence/Jira 服务端凭据；仅在凭据缺失、主密钥不可用或解密失败时要求用户重新验证。
- Web 自有 SQLite 数据库位于既有 app-data 目录，启用 WAL、外键、busy timeout 和受控 schema 迁移；不写入源码目录或网络共享盘。

### 12.2 持久会话模型

`web_sessions` 保存会话唯一标识、`token_hash`、规范化账号、显示名、可选头像缓存、创建时间、最后访问时间、过期时间和撤销时间。原始 token 只存在于 `HttpOnly`、`Secure`、`SameSite=Lax` Cookie。

登录成功创建新的设备会话；普通请求验证 token 哈希并按有限频率更新最后访问与过期时间，避免每个请求都写库。退出当前设备撤销当前记录；退出全部设备按当前认证账号撤销全部有效记录。过期记录由有界清理机制删除。

### 12.3 账号级偏好模型

`user_preferences` 以当前认证账号、页面 `scope` 和配置 `key` 唯一定位，保存 JSON 值、schema 版本和更新时间。后端只从认证会话取得账号，不信任前端提交的用户名；写入使用原子 upsert，多设备修改采用最后一次写入生效。

保存主题、筛选勾选、搜索条件、排序、分页、页签、列显示、折叠状态及其他非敏感表单偏好。密码、Token、Cookie、临时凭据、文件上传、查询结果、报告、日志、运行进度和业务提交结果禁止进入偏好存储。

### 12.4 前端自动持久化机制

公共 `PreferenceStore` 只作用于页面配置区域和筛选区域，不盲扫所有表单。公共页面或组件在容器级声明偏好区域；区域内新增标准 `input`、`textarea`、`select`、checkbox、radio、switch、tab、折叠和排序控件后，由事件委托自动保存和恢复，不允许页面再编写独立缓存调用。

- 页面 `scope` 默认取稳定路由；控件 `key` 优先取 `name`，其次取 `id`，公共自定义组件使用自身稳定语义标识。
- 标准控件默认持久化，无需逐控件增加缓存代码；明确的临时控件可退出自动持久化。
- 密码、敏感命名字段、隐藏字段、文件、按钮及禁止类型由公共边界强制排除。
- 动态新增控件同样自动恢复与监听；自定义控件只允许在公共组件层增加一次适配，不逐实例接入。
- 选择、布尔、页签和排序立即保存；文本、搜索和日期输入防抖保存；成组筛选合并写入。
- 服务端偏好是跨设备权威数据；浏览器只允许使用内存或有界本地副本加速，写入失败必须显示未同步状态并保留重试能力。
- 自动化测试扫描偏好区域内可编辑控件；缺少稳定 `name`/`id`、未被公共组件适配且未明确排除时测试失败。

### 12.5 迁移与验收清单

- [ ] 以失败测试固定 SQLite schema、迁移、WAL/并发、原子 upsert、损坏/不可用错误边界。
- [ ] 用持久 `SessionStore` 替换 `InMemorySessionStore`，覆盖重启恢复、90 天滑动过期、有限续期、当前设备退出、全部设备撤销和过期清理。
- [ ] 通过统一 credential owner 持久化服务端凭据，验证重启恢复；凭据缺失、主密钥不可用或解密失败时返回明确的重新验证状态。
- [ ] 新增认证账号下的偏好读取、批量 upsert、scope reset API，覆盖越权、类型、大小、敏感字段和并发边界。
- [ ] 实现公共前端 `PreferenceStore`、偏好区域自动发现、动态控件恢复、事件委托、防抖、合并保存、失败重试和未同步提示。
- [ ] 迁移主题、Confluence 更多筛选及 Wi-Fi 三个页面筛选，删除被替代的 `localStorage`/`sessionStorage` 页面缓存逻辑。
- [ ] Settings 只接入已有真实业务含义的配置；不得为模板中的账号资料、2FA、删除账号等占位控件伪造业务能力。
- [ ] 验证同账号跨浏览器/设备恢复、不同账号隔离、最后写入生效、Server 重启恢复及敏感数据不落盘。
- [ ] 运行前端聚焦测试、完整前端测试/lint/build、后端聚焦测试、完整 Web 后端测试和 `git diff --check`。

### 12.6 Confluence 账号权限、页面发现与筛选修复

Confluence 项目事实继续复用搬移前的页面发现 owner，不维护只从 `Project Status Report` 向下搜索的平行逻辑。目录入口为 Status 页面时，必须先上溯项目根，再从兄弟分支定位 `Basic Information`；缓存解析后的 entry/root page ID，不依赖目录 URL 是否直接携带 `pageId`。

- 全文搜索范围保持项目、人员、Space key、全部 Project Space 字段值和 Confluence identity，页面文案使用 `Project / Person / Field Search`。
- Product Space 过滤值继续使用 DOPL、SDPL、TV、OOPL；下拉分别显示 `China Operator Business`、`Smart Device Business`、`TV Business`、`Global Operator & STB Business`。空的旧快照显示名不得覆盖 Core 已有显示名。
- 未选择 Product Space 时，Current Stage 下拉显示四个产品线源数据 Stage 的并集；选择一个或多个 Product Space 时，显示所选产品线 Stage 的并集。Stage 域不随年份、状态等其他筛选条件收缩，选择当前项目不存在的 Stage 只返回零匹配，不报错；名称保留源数据原文。
- 不实现 `personnel.json` 的 I/M 范围过滤。使用当前登录账号访问 Confluence，项目事实快照按规范化账号隔离；无权访问的 Space、项目或页面直接排除，不向页面返回权限错误，也不得向其他账号泄露已有快照。
- Server 重启只读取当前账号自己的历史快照；需要刷新时从该 session 的服务端 credential owner 恢复凭据，仅在恢复不可用或解密失败时返回重新验证状态。
- API 已命中但责任人员采集失败时，页面仍展示项目基础结果并标记责任信息不可用，不得因 owner hierarchy 为空而显示成零项目。

执行清单：

- [ ] 以失败测试复现 Status 与 Basic Information 为兄弟页、目录 URL 缺少 pageId 和旧快照空显示名覆盖问题。
- [ ] 复用既有 `discover_project_pages()` 上溯与遍历语义，删除或收敛平行页面发现逻辑，并缓存解析后的稳定页面 ID。
- [ ] 将 ProjectFactStore/Web owner/API 查询切换为认证账号命名空间，验证账号隔离、重启恢复与无权项目静默排除。
- [ ] 建立按 Product Space 独立保存的 Stage 域及选中 Space 并集契约，验证全集、子集和无匹配不报错。
- [ ] 修复四个 Product Space 显示名、全文搜索文案及无责任人员项目的可见降级展示。
- [ ] 用真实四个 Product Space 刷新验证页面发现、权限和 Stage 域；完成后移除或降级临时诊断，不保留重复日志。
- [ ] 运行 Core 项目事实/旧发现/logging 边界测试、Web 后端、前端测试/lint/build、产品边界检查和 `git diff --check`。

### 12.7 Confluence 首次缓存与目录优先加载

Confluence 筛选器只依赖四个 Project Space 目录页，不得等待所有项目的 `Basic Information` 与责任人员详情采集完成。首次账号缓存采用目录优先的两阶段流程：

1. 当前账号成功 LDAP 登录后，若其账号命名空间没有项目事实快照，立即启动后台初始化，不等待用户进入 Confluence 页面。
2. 后台任务以四个相互独立的有界请求并行读取当前账号可访问的四个 Project Space 目录；`display/<space>/<title>` 复用 atlassian-python-api 的 `get_page_by_title` 单次定向调用并仅展开 `body.view,version`，`pageId` URL 继续使用 `get_page_by_id`，不得手写 HTTP。
3. 每个目录请求完成后立即解析并原子保存同一账号级 `catalog_loading` 部分快照，携带 completed/pending/total 进度；最终快照按稳定空间顺序合并为 `catalog_ready`。部分筛选项立即可见、可选，但最终完成前禁用 Apply。页面加载、筛选勾选、Reset 和普通轮询不得读取 `Basic Information`、责任人员或项目页面图。
4. 用户点击 `Apply Filters` 后，先在账号目录缓存中完成过滤；只有匹配项目才进入既有页面发现 owner 并读取 `Basic Information` 与责任人员详情。零匹配不得发起详情请求；已有有效详情缓存直接复用。
5. 单个 Space 或项目无权限时静默排除；单个命中项目详情慢、失败或无权限不得阻塞其他匹配结果。详情不可用的项目保持基础结果可见并标记责任信息不可用。
6. Server 重启后直接读取当前账号已有目录与详情缓存，不访问 Confluence。目录缓存不存在时从持久 session 的服务端 credential owner 恢复凭据并初始化；凭据不可用或解密失败时返回明确的 `reauthentication_required`，前端显示重新验证提示，不保持无限 Loading。
7. 账号重新登录或验证后自动启动缺失目录缓存初始化；前端轮询始终显式使用 `details=false`，持续到终态、页面销毁或请求代次切换，不设固定次数上限。目录最终完成后启用 Apply；失败必须进入终态。Apply 详情请求使用独立状态，不得把筛选器重新置为 Loading。
8. Preference scope 去除路由开头 `/`，例如 `confluence.html`，不得请求 `/api/preferences//confluence.html`。

执行清单：

- [ ] 以慢详情 fake client 复现四目录已完成但筛选器仍等待全部项目详情的问题，固定目录阶段先返回的时序契约。
- [ ] 将项目事实收敛为并行目录快照与 Apply 按需详情两个独立入口，保留账号隔离、Stage 域、权限静默排除和失败事实。
- [ ] 验证页面加载只发起四个目录请求，目录完成即保存；Apply 只读取过滤命中的项目详情，零匹配和缓存命中不访问详情。
- [ ] 登录成功时只为缺失账号快照启动 single-flight 初始化；已有快照不访问 Confluence。
- [ ] 修复无缓存且持久凭据不可用或解密失败时的前端重新验证提示，避免无限 Loading。
- [ ] 规范化 Preference scope，并验证既有账号偏好读写不产生双斜杠。
- [ ] 记录目录阶段与详情阶段的安全耗时和数量，不输出账号、凭据、页面正文、人员或项目明细。
- [ ] 运行慢详情时序、账号隔离、Core/Web/前端、logging/产品边界和 `git diff --check` 验收。

### 12.8 服务端持久凭据

- 浏览器仍只保存随机 session Cookie；LDAP 密码仅由服务端凭据 owner 持久化，并以每个 session 独立的 credential reference 关联现有 SQLite session。
- Windows 复用 `core/credentials/windows.py` 的 Windows Credential Manager owner；Ubuntu/Linux 使用 `cryptography` AEAD，加密后的 ciphertext、nonce 与 key version 存入现有 Web SQLite，主密钥只从 `SMARTTEST_WEB_CREDENTIAL_KEY` 外部配置读取，禁止写入数据库或日志。
- server 重启后，持久 session 通过 credential reference 恢复 Jira/Confluence 共用凭据；多设备 session 相互独立。单设备 logout 只删除自己的凭据；logout-all、过期清理删除对应全部凭据。
- 主密钥缺失、格式错误、版本不匹配或解密失败时安全失败，不回退到明文、浏览器存储或第二套账号体系，也不记录密码、token、ciphertext 或主密钥。

## 13. 模块开发顺序

### 13.1 Jira 报告工作台

本模块已由 Coco 通过 `web/design/reference/jira-confluence-report-workspace.html` 确认视觉布局，开发与验收均以该本地 HTML 为桌面端视觉基准。

范围与边界：

- 顶部导航保留 `Jira` 和 `Confluence` 两个独立入口，现有页面、主题切换和 Wi-Fi Data 行为不变；
- Jira 使用“左侧报告目录 + 右侧完整报告预览”及 Client 同口径 JQL 输入；
- Jira 复用 Client 现有 Jira 审查、报告和导出业务 owner；
- 浏览器只访问 Web API，第三方认证、访问、审查和报告生成逻辑不得复制到前端或 Web 后端；
- 当前只读展示，不通过 Web 修改 Jira 或 Confluence；
- 报告正文在页面中直接展示，报告工具栏提供来源跳转和 `Download`；下载必须复用后端权限范围与既有导出结果；
- 不生成模拟业务数据；加载、无报告、无权限、配置缺失、外部失败和部分成功必须有明确页面状态；
- 桌面端尽可能复现已确认 HTML，窄屏维持现有 SmartTest 响应式视觉语言。

执行清单：

- [x] 通过失败测试确定 Jira Web API 契约、只读边界、错误状态和下载行为；
- [x] 复用 Client 对应报告导出 owner，Web 后端仅增加本地只读报告目录与传输适配，不复制第三方访问、审查或导出逻辑；
- [x] 浏览器登录复用下沉到 Core 的 Client LDAP owner；Web 使用 HTTP-only/Secure cookie 标识持久 session，密码不进入前端或 SQLite session 表，仅由 Windows Credential Manager 或 Linux AEAD credential owner 持久化；
- [ ] 后续接入 `personnel.json` 人员映射及 I/M 岗数据范围，并让列表、正文和下载共用同一权限结果；
- [x] 通过失败测试确定 Jira 报告工作台、筛选、报告选择、正文展示和下载交互；
- [x] 实现 Jira 页面并与本地视觉基准比对；
- [x] 验证加载、空数据、无权限、配置缺失、外部失败和部分成功；
- [x] 验证现有 Dashboard 页面、主题切换、移动导航和 Wi-Fi Data 无回归；
- [x] 运行前端测试、lint、build，后端聚焦测试和 `git diff --check`；
- [x] 用实际浏览器截图与本地 HTML 对照，矫正导航、间距、尺寸、颜色、卡片、工具栏和响应式差异。

当前限制：Web 已具备复用 Client LDAP 规则的持久浏览器会话及服务端 credential owner，但尚未接入 `personnel.json` 与 I/M 岗权限范围。Jira 模块仍只读取 Client 已导出的本地 XLSX，不使用会话凭据执行 Jira 查询或审查；API 保留明确的 `unauthorized` 状态，正式人员范围过滤由后续权限阶段接入。

Jira 执行限制：Client 的新审查链路依赖 `JiraAuditBridge` 持有的运行时临时凭据，并经过 `resolve_audit_input`、`JiraAuditService.run`、人工确认后才允许 `export_audit_xlsx`。Web 当前不能安全复用该临时凭据会话，因此 Jira 页面的 JQL 动作只按导出文件中保存的“JQL 查询条件”精确定位 Client 已生成报告，不执行新查询或审查；新审查继续由 Client 发起。

### 13.2 Confluence 全项目 QA 责任事实（Core 阶段）

Core 项目事实是 Client 与 Web 共用的唯一业务 owner。该 owner 从全部已配置 Product Space 读取项目目录，不套用现有周审查的 A/B、年份、项目状态或 Stage1/2/3 资格过滤；现有 Client 周审查入口与规则保持不变。

执行清单：

- [x] 保留 Product Space 每个实际表头、原始值与标准化字段，并单列未知表头差异；
- [x] 从 `Basic Information` 精确提取 `Major FAE QA`、`FAE QA`、`QA Reviewer`，保留多人及源数据提供的 Confluence 稳定身份；
- [x] 使用 Web SQLite 保存项目最新当前态、动态属性、人员关系、来源页面和账号可见集合；旧 app-data JSON 仅允许一次性迁移读取，不参与运行或双写；
- [x] 增量刷新复用未变化项目；失败时保留旧事实并标记 stale，目录缺失项目标记 inactive 而不删除；
- [x] 提供只读本地查询与全字段 facets，不在查询时访问 Confluence；
- [x] 通过全目录、未知列、角色多人、首次快照、零变化、单项目变化、局部失败、inactive、本地过滤与损坏/schema 拒绝测试；
- [ ] 待 Coco 根据真实 Confluence 页面确认未知表头差异清单后，决定是否增加新的标准字段映射；
- [ ] 待项目只读 Web 模块开发时，通过 Web 权限适配公开该 Core 查询契约。

当前字段证据：仓库测试夹具可确认 `Page/页面`、`Project ID`、`Date of Commercial approval`、`Support Mode`、`Project Status`、`Current Stage`。除这些已有证据外不猜测别名；其他实际表头原样保存并进入 `field_discrepancies`。

Confluence Web 接入清单：

- [x] Web API 只读取 `ConfluenceCurrentStateRepository` 并复用 Core 本地查询规则，查询过程不访问 Confluence；
- [x] 返回快照状态/时间、全量动态 facets、项目、字段差异及 stale/failed/inactive 数量；
- [x] 明确展示 `no_snapshot`、`schema_error` 与 `partial_success`；
- [x] Confluence 页面移除 Report Type、报告目录、报告预览、来源报告跳转和 Download；
- [x] 按本地事实动态绘制全部 Product Space 表字段筛选、独立 `Product Space` 来源维度、项目/人员搜索及基础项目结果；
- [x] 保留未知字段及原始表头标签，不在 Web 中猜测或合并别名；
- [x] `Apply Filters` 只刷新下方 `角色 → 人员 → 该人员全部项目 → 项目基本信息`，不重建顶部筛选控件；
- [x] 顶部筛选结构固定保留全部已确认字段，当前快照无字段值时显示空选项；
- [x] 常用筛选固定为 `Product Space`、`Date of Commercial approval`、`Project ID`、`Project Status`、`Current Stage`、`Project Owner`、`Support Mode`；不显示独立 `Year`；
- [x] 全部 Confluence 下拉筛选统一为多选：同字段取 OR、跨字段与项目/人员搜索取 AND；API 使用重复 `field.<key>` 参数。`Product Space` 稳定值保持 DOPL/SDPL/TV/OOPL，显示名直接来自 Core `PRODUCT_LINES.display_name`，Web 不维护映射；
- [x] 顶部 facets 在快照加载完成后保持固定；勾选、取消、全选和清空仅更新浏览器本地选择，Apply/Reset 只刷新下方责任层级，不用过滤响应收缩顶部选项；Core 与 Web 请求边界通过 `core.logging.smart_log` 记录有界筛选、命中/排除计数及返回层级汇总，不记录搜索内容、人员身份或项目明细；
- [x] `Date of Commercial approval` 复用 `ProjectCollectionFilter.years` 和现有商业批准日期年份解析，选项显示年份但字段名保持原字符；
- [x] 其他真实 Product Space 字段进入“更多筛选”，勾选后显示、取消后移除并清值；只将启用字段 key 保存到浏览器 `localStorage`，不保存业务事实；
- [x] 页面先显示 SQLite 当前态；后台同步期间展示进度并禁用 Apply/Review，级联选项由当前数据库结果返回，失败时保留最后成功时间与 stale 信息；
- [x] 数据库无当前账号可见集合时，从统一服务端 credential owner 恢复凭据并以轻量 CQL 初始化可见关系；项目详情按 page ID/version 增量更新全局当前态，并发同步在项目级 single-flight 去重；
- [x] 首次查询立即返回本地空结构并启动后台目录同步；完成的 Space/项目可渐进写入。前端有界轮询现有 job，页面离开、Cancel 或组件销毁停止当前轮询；revision 增长且筛选上下文未变化时自动刷新结果与 facets；
- [ ] Web 项目审查动作后续直接调用 `ConfluenceAuditService.run` 与 `export_project_audit_xlsx_by_product_line`；当前阶段仅把公共会话接入事实缓存刷新，不改变审查按钮边界；
- [ ] 后续按 Coco 确认的管理视图实现组织层级、项目卡片与详情布局。

四个 Product Space 的实测字段证据（2026-08-26，Atlas 使用 Chrome 核对）：

- 共同字段：`Page/Project Link`、`Project ID`、`ODM`、`OEM/Operator`、`Key Part Number`、`Project Status`、`Current Stage`、`Project Owner`、`Support Mode`、`Date of Kick Off`、`planned closure`、`actual closure`、`Commercial approval`；
- `DOPL` 缺少 `Launch OS`；
- `SDPL` 缺少 `MP Time`、`Launch Time`、`Next Target`、`Next Target Date`；
- `OOPL` 额外包含 `Sum`；
- `Major PM` 当前在四个 Product Space 表中均不存在，因此保留空筛选结构，不推断来源；
- 四个 Product Space 的字段顺序不同。Core 保存每个表的原始表头顺序和原始值，Web 不以某一个 Space 的顺序覆盖其他 Space。

审查调用边界：Client 的 `ConfluenceAuditBridge.startAudit` 仍通过其登录会话构造 `ConfluenceAuditService`，按 `ProjectCollectionFilter` 执行既有 Stage1/2/3 审查点并导出。Web 持久 session 与服务端 credential owner 已可供后续工具复用，但本阶段只授权 Confluence 责任事实缓存刷新；未把 Web 审查按钮接入审查与导出链路，也未改变 Jira 行为。

### 13.3 Confluence QA 责任汇总呈现

Confluence 页面在既有账号隔离、筛选、Apply、缓存与权限结果之上呈现 SmartTest QA 责任汇总，不建立第二份人员或项目数据模型。页面仅使用筛选 API 返回的 `projects` 与 `ownerHierarchy` 计算匹配项目数、唯一 QA 人数、产品线数、平均每人项目分配数，并用口径说明卡明确平均值按“角色人员的项目分配总数 / 唯一人员数”计算。

- `Role workload` 使用单一横向 Chart.js 条形图；标题右侧固定提供四个产品线 Switch，并保留角色切换。图表只统计当前产品线与当前角色的人员项目数，Y 轴为可读人员名，X 轴为项目数，按项目数降序，图表区域内部有界滚动；不得再将不同产品线人员混合排行。
- `Projects by Product Lines` 固定展示四个完整产品线容器；每个产品线内按项目 `Customer` 属性形成二级分组并按项目数降序，Customer 为空时归入 `Unassigned`。该层不再使用 `Major FAE QA` 分组。
- 每个产品线标题栏内固定展示 Stage Labels，不显示 `Current Stage` 标题，也不拆出独立背景行；按当前过滤结果统计该产品线内各 Stage 的项目数，每个实际出现的 Stage 使用一个同时包含状态名与项目数的独立 Label，Stage 为空时归入 `Unspecified`。产品线标题栏和 Labels 始终保留，折叠只影响 Customer 与项目内容。Stage 的低饱和度背景色和前景色由 Core 项目封装统一定义，Web 只消费后端返回的颜色，不维护第二套阶段色板。
- Customer 分组内的项目使用单列横向 ListView；默认行横向展示项目名称、Project ID、产品线、状态、阶段和 Support Mode，悬停后在行内追加横向详情带展示 Customer、QA 角色及其他项目属性，窄屏允许自动换行。
- 责任明细使用自定义卡片折叠：角色层显示角色、人数、分配数；人员层显示可读名称、项目数和产品线标签；展开后显示紧凑项目列表。不得使用原生 `details/summary` 或浏览器 disclosure triangle。
- presentation 不显示 Confluence identity。名称缺失或仅等于 identity 时显示 `Unknown member`；源数据中本身可读的自由文本（包括 NA/TBD）保持原样。
- 每个项目、每个责任角色属性按一对多展开：以 `<br>` 分隔逻辑段；段内 `ri:user` 或带稳定身份的用户链接各自形成一条人员—项目关系，无列表分隔符的尾随文字仅为职责说明，逗号、分号、`、`、`，` 明确引出的纯文本则继续作为额外人员。无结构化用户的段沿用可读纯文本人员回退（包括 NA/TBD）。同一 identity 在该项目角色内只计一次，纯文本人员按规范化名称去重。
- 图表、卡片和汇总只使用真实筛选结果，不引入项目等级或虚构指标；空结果及 API 错误沿用既有页面状态语义。

### 13.4 Web 静态 Shell 与导航单一 owner

Dashboard、Projects、Jira、Confluence、Wi-Fi Data、Settings 以及可直接访问的 Inbox、Analytics HTML 入口都静态持有同一套 Shell；JavaScript 不创建、替换或补写顶部导航。品牌统一为 `FAE QA Data Center`，主导航固定为 Dashboard、Projects、Jira、Confluence、Wi-Fi Data、Settings，Inbox 与 Analytics 保留页面入口但不进入主导航。

- `smarttest-portal.js` 只保留主题、移动菜单、Greeting、Inbox、Kanban 等公共交互，不插入导航项。
- `main.js` 只在已有 Shell 上挂接认证、账号级偏好和 Wi-Fi 路由启动；Report 与 Wi-Fi main 只把各自业务内容挂载到既有 `main.main-content`。
- Wi-Fi 业务视图由单一 `wifi-database.js` owner 管理；不保留 `createApp` 动态 Shell 或前端页面内路由机制。

本地当前态刷新边界：Web 查询只读 SQLite 中的全局项目当前态和当前账号可见集合，命中时不访问 Confluence。无本地当前态且已登录时，请求不等待第三方网络；进程内 single-flight 后台任务从服务端 credential owner 恢复凭据，通过 `ConfluenceClient` 更新账号可见集合和全局项目当前态。凭据不写入响应、日志、浏览器存储或 SQLite session 表；Windows Credential Manager 或 Linux AEAD 密文存储独立持久化凭据，服务退出不持久后台任务。

### 13.5 Confluence 当前态数据中心重构（待 Coco 审查）

本阶段将 Confluence 从“查询时抓取并解析页面”调整为“后台同步最新状态、Web 查询本地结构化数据”。只关心 Confluence 当前最新信息，不保存页面正文历史、属性历史或项目历史快照。Confluence `version.number` 和 `version.when` 仅用于判断本地当前态是否需要更新，不形成可查询的业务历史。

#### 13.5.1 数据获取边界

- `core/confluence/` 是 Confluence 访问的唯一 owner，优先复用已声明依赖 `atlassian-python-api` 提供的 CQL、页面、子页面、用户和附件 API；业务模块不得直接创建另一套 HTTP Client。
- 数据发现优先使用 CQL/REST API，只请求当前步骤需要的轻量字段。账号项目可见性只读取 `id`、`title`、`space`、`version` 等元数据，不展开正文。
- 只有 `atlassian-python-api` 未封装目标 REST API 时，才允许在 `core/confluence/` 内通过其受维护会话调用 Confluence REST endpoint；不得把直接请求分散到 Web 或审查规则模块。
- 只有 REST API 无法提供目标内容，且已有真实页面证明必须解析渲染结果时，才使用页面内容/HTML 解析作为备用手段。备用路径必须返回与 API 路径相同的 Core 模型，并记录来源和失败原因，不得静默切换口径。
- 浏览器前端只访问 SmartTest Web API，不直接访问、登录或解析 Confluence。

#### 13.5.2 Core 分层与复用

Confluence 业务按以下单一职责分层，不保留 Web 专用采集器或第二套项目模型：

1. `ConfluenceClient`：认证、CQL 分页、轻量元数据查询、按 page ID 获取当前页面、用户显示名解析和必要的 REST 备用访问。
2. 项目发现与可见性：识别四个 Product Space 中的项目，建立稳定 `project_page_id`，并以当前账号执行轻量 CQL 得到该账号当前可见的项目集合。
3. 当前态映射：把 Confluence 当前页面映射成稳定项目字段、动态原始属性、人员角色关系和来源页面元数据；未知字段原样保存，不要求数据库 schema 随每个 Confluence 新表头修改。
4. 同步服务：负责账号可见性刷新、项目当前态增量刷新、并发去重、失败隔离、取消和同步进度；Web 只调用该服务，不编排页面级抓取细节。
5. 本地查询：只查询数据库，并在账号可见集合内执行 facets、过滤、全文搜索、责任汇总和审查输入组装；查询路径不等待 Confluence。
6. 项目审查：统一读取当前态项目模型和集中规则定义，产出责任事实、每周审查结果及后续新增审查事实；不得各自重复发现、下载或解析同一页面。

#### 13.5.3 当前态数据库模型

Web SQLite 增加 Confluence 当前态存储，至少包含以下逻辑实体；实际表名可沿用仓库命名规范，但职责不得合并：

- `confluence_projects`：每个项目一行，以稳定的 Confluence 项目 page ID 为主键，保存 Product Space、显示名、项目链接、当前页面版本、更新时间和当前同步状态。
- `confluence_project_attributes`：按项目和稳定字段 key 保存动态属性，保留原始表头、原始值与规范化值；新增 Confluence 表头不要求修改 Web 前端或数据库表结构。
- `confluence_project_people`：按项目、审查身份/角色和人员记录一对多关系，保存稳定 identity 与已解析的页面显示名；同一项目的同一角色中每个人单独计数。
- `confluence_project_pages`：记录项目所需当前来源页的 page ID、页面类型、当前版本、更新时间和解析状态，用于按版本增量获取 `Basic Information`、周报及后续审查页面。
- `confluence_account_project_access`：只保存 `account_id -> project_page_id` 当前可见关系和检查时间；项目内容不按账号复制。
- `confluence_sync_state`：保存账号/Space 的最近成功同步时间、当前 revision、结果和有限错误摘要；不保存凭据、正文历史或旧属性版本。

数据库更新规则：

- 同一项目无论由哪个有权限账号读取，都更新同一份项目当前态；仅当 Confluence 当前版本更新时重新获取和解析所需页面。
- 一个账号完整分页查询成功后，事务性替换该账号的可见项目集合，避免分页过程中暴露半套权限结果。
- 某账号看不到项目只表示删除该账号的可见关系，不得据此删除全局项目数据或判断项目已失效。
- 单项目同步失败时保留上一次成功的当前态并标记 stale；不得用空数据覆盖成功结果。权限查询整体失败时保留旧可见集合并明确标记刷新失败。
- 数据库 schema 使用受控迁移、外键、唯一约束和事务；多账号或后台任务命中同一项目时按 page ID single-flight，避免重复解析和相互覆盖。

#### 13.5.4 同步与 Web 响应流程

- 登录恢复、Server 重启或进入页面时，Web 立即使用该账号最近一次成功的可见集合和数据库当前态响应，不等待 Confluence。
- 后台每 5–15 分钟使用仍有效的账号凭据刷新该账号可见集合；不同账号只影响可见关系，不产生项目内容副本。
- 点击 `Apply Filters` 只有一个动作入口：先立即返回本地数据库结果，同时为当前可见且命中过滤范围的项目触发增量同步。同步期间显示进度和 Cancel，禁用 Apply/Review；Cancel 只中止该次网络同步，不清空已经返回的本地结果。
- 同步任务按项目独立并发，谁先完成谁先提交当前态。每次提交增加数据 revision；前端在用户未修改当前筛选上下文时自动重新查询，更新项目结果和筛选 facets。
- 用户修改筛选条件后不再用旧任务结果覆盖当前视图，但旧任务已经成功取得的项目最新态仍可写入公共数据库。
- 后台同步失败不阻塞本地查询；页面展示最后成功更新时间、同步状态和有限错误摘要，并允许下一次 Apply 或周期任务重试。

#### 13.5.5 项目属性与审查规则唯一 owner

`core/tools/common/project_weekly_audit/` 继续作为项目审查业务 owner，但需要从抓取流程中拆出集中规则定义。所有从 Confluence 提取业务事实的规则必须可在同一位置检索，包括：

- Project Space 原始字段与标准字段映射、日期/Stage/Support Mode 等规范化规则；
- `Basic Information` 页面定位规则；
- `Major FAE QA`、`FAE QA`、`QA Reviewer` 等人员角色提取、多人展开、identity 去重和显示名解析规则；
- 现有每周审查的项目资格、审查周期、目标页面、静态判断、语义判断、证据和结果规则；
- 后续新增的项目属性或审查项。

规则层只接收 `ConfluenceClient` 返回的 Core 页面模型或数据库当前态模型，不持有账号凭据、不直接发网络请求、不写 Web 响应。每条规则需声明稳定 rule key、输入页面/字段、输出字段、确定性判断和失败语义；规则实现版本只用于定位解析口径，不保留项目历史结果。

责任事实与每周审查共享项目发现、页面定位、人员解析和字段规范化能力，但保持各自结果模型，避免把人员汇总规则硬编码进周审服务或把周审资格条件套到全项目责任事实。

#### 13.5.6 需要补充的工程约束与验收

- **权限安全**：所有 Web 查询必须先关联 `confluence_account_project_access`；任何全局项目缓存都不能绕过账号可见关系返回。
- **动态字段**：新增 Product Space 属性只需同步后进入属性表和 facets，不要求修改数据库列、API 字段清单或前端控件代码。
- **性能**：可见性查询不下载正文；本地 Apply 在数据库查询完成后立即响应；详细同步不占用请求线程。
- **一致性**：账号可见集合原子替换，项目当前态按项目事务提交，页面与属性不能出现跨版本拼接。
- **可观察性**：记录同步 job、账号匿名标识、Space、项目总数/完成数/失败数、耗时和结果；不记录密码、页面正文、搜索词或人员敏感明细。
- **数据恢复**：SQLite 备份、迁移和损坏恢复纳入 Web 数据库既有机制；恢复后允许后台重新构建 Confluence 当前态。
- **测试**：覆盖轻量 CQL 分页与权限过滤、账号可见集合替换、多账号共享项目数据、动态未知字段、页面版本未变化跳过、单项目失败保留旧值、多人角色展开、周审规则复用、并发去重、取消、后台更新后 Web revision 刷新，以及查询路径不访问 Confluence。
- **迁移**：现有账号隔离 JSON 只作为迁移输入；迁移成功后数据库成为唯一查询 owner，不长期维护 JSON 与数据库双写。

实施边界：本节通过审查后再拆分执行清单。本阶段不修改 Confluence，不引入历史数据仓库，不引入 Redis，不新增第二个手动刷新按钮，也不改变 Jira 或 Wi-Fi Data 行为。

#### 13.5.7 已批准实施清单

- [x] 以测试先行扩展 `ConfluenceClient`：增加轻量 CQL 元数据分页和必要的受维护 REST 备用入口，移除业务层重复请求与不必要的正文传递。
- [x] 建立 SQLite Confluence 当前态 schema、迁移和 repository，覆盖项目、动态属性、人员关系、来源页面、账号可见关系与同步状态；不实现历史表和 JSON/数据库长期双写。
- [x] 将现有账号 JSON 当前事实迁移为数据库当前态；迁移后所有 Web 查询只读数据库，旧 JSON owner 和账号级项目内容副本退出运行链路。
- [x] 整理项目发现、动态字段映射、页面定位和人员解析，使同步服务按 page ID/version 增量更新同一份全局项目数据。
- [x] 实现账号轻量可见性刷新、事务性集合替换、项目级 single-flight、有限并发、取消、失败保留和 5–15 分钟后台同步。
- [x] 调整 Web Apply：立即返回账号权限范围内的本地结果，同时启动当前范围增量同步；同步期间显示进度/Cancel并禁用 Apply/Review，revision 更新后在筛选上下文未变化时自动刷新结果与 facets。
- [x] 将 Owner/多人角色、Basic Information 和现有每周审查的提取规则集中到项目审查 owner，共享项目模型、页面定位、字段规范化和人员解析，删除重复采集、转换与中间数据传递。
- [x] 删除被数据库、同步服务和集中规则替代的 JSON 刷新、查询时详情抓取、Web 编排和兼容分支；检查净生产代码增长，拒绝并行 owner 和无必要包装。
- [x] 完成 Core、数据库、Web API、前端状态、日志/边界测试、迁移测试、lint/build、`git diff --check` 和最高可行的实际账号验证；未得到 Coco 功能确认前不提交。

### 13.6 Tools 页面与全局过滤器单例

本模块把人工周审查从业务浏览页迁移到独立 `Tools` 页面，并建立两个互不混用的全局过滤器单例。Confluence 与 Jira 的字段语义、候选来源和查询 owner 不同，禁止合并成一份通用 filters 字典；“全局”表示同一账号当前会话内，所有消费页面读取各自唯一的已应用过滤状态。

#### 13.6.1 页面归属

- 顶部和移动端主导航固定新增 `Tools`；所有静态 Shell 同步持有该入口。
- `Tools` 页面承载 Jira 周审查、Confluence 周审查和定期审查邮件入口。现有审查 API、任务、进度、取消、导出和邮件调度 owner 保持不变。
- Jira 页面本阶段保留空白占位，不展示 Release Workbench 或审查控件。
- Projects 页面只保留项目事实、责任展示和对全局 Confluence 过滤器的消费，不再拥有周审查控件。
- Settings 删除定期审查邮件入口；现有 `audit-email.html` 继续作为定期审查邮件管理页面，由 Tools 进入。

#### 13.6.2 Confluence 全局过滤器单例

- 复用已经确认的全部 Confluence 动态字段、常用/更多筛选、搜索、Apply、Reset、候选加载和 SQLite 查询快照规则，不新增另一套字段模型。
- 后端 `ConfluenceQuerySnapshotRepository` 继续作为当前 session 已应用条件和精确项目 ID 范围的唯一权威 owner；前端只保留未提交控件状态和可丢弃展示状态。
- Projects 与 Tools 的 Confluence 周审查读取同一快照。Tools 可展示和修改同一组筛选控件；Apply 先更新该唯一快照，审查再严格按快照项目范围执行。
- 页面切换只重放 SQLite 快照，不自动远程读取详情，不从前端 payload、URL 或本地展示缓存获取权威项目 ID。

#### 13.6.3 Jira 全局过滤器单例

- Jira 单例固定提供 `Project`、`Type`、`Status`、`Current User`、`Resolution` 五个下拉字段，并保留 JQL 参数输入入口；不得擅自增加字段、推断默认值或把 Confluence 字段混入 Jira。
- 下拉候选复用 Jira gateway/cache 已有元数据和账号权限边界；浏览器不直接访问 Jira。JQL 保持原始用户输入，经现有 Jira 审查解析 owner 校验和传递。
- 后端为当前 session 保存唯一的 Jira 已应用过滤快照；Tools 的 Jira 周审查和后续 Jira 页面只消费该快照。前端偏好仅恢复未提交控件，不成为审查范围 owner。
- 下拉条件与 JQL 都属于 Jira 单例的输入；执行审查时由 Jira owner 在现有查询/解析边界内组合，前端不拼接 JQL。

#### 13.6.4 状态与生命周期

- 两个单例按认证账号和 session 隔离；账号切换、注销或 session 失效时不得复用上一账号状态。
- 每个单例只有一个 Apply/Reset 入口和一个已应用 revision。任一页面提交后，其他消费页面下次显示或收到 revision 更新时读取同一状态。
- Confluence 和 Jira 单例互不覆盖、互不级联；一个单例失败不得清空另一个单例最后有效快照。
- 所有审查继续遵守账号可见范围。缺少有效快照、候选不可用、凭据失效或输入非法时明确返回失败状态，不静默回退到全量数据。

#### 13.6.5 已批准实施清单

- [x] 以测试先行建立两个 session 级过滤快照契约；Confluence 扩展现有 owner，Jira 新增与现有查询快照表一致的唯一 scope，不建立平行存储。
- [x] 以测试先行抽取可被 Projects/Tools 消费的 Confluence 单例前端控制器，保留现有动态 facets、Apply/Reset、缓存优先和审查快照边界。
- [x] 以测试先行实现 Jira 单例的五个下拉候选、JQL 输入、Apply/Reset 与审查参数传递，候选和执行均复用现有 Jira owner。
- [x] 新建 Tools 页面并迁移两项周审查；迁移定期审查邮件入口，清理 Settings、Projects 和 Jira 的旧入口及重复状态。
- [x] Jira 页面改为空白占位；所有静态 Shell、Vite 多页入口、导航激活态和相关说明同步更新。
- [x] 运行前端聚焦/全量测试、lint、build，后端过滤快照与审查聚焦测试，执行源代码实际页面验证和 `git diff --check`；清理探索性测试、临时诊断和废弃实现。

### 13.7 可装配 Web AppShell

当前多页 HTML 分别复制顶部导航、移动菜单、主题控件、账号挂载点、二级导航和 Footer。新增页面只要漏掉任一隐含挂载点，`main.js` 就会跳过整个认证 Shell；Tools 首版同时缺少右侧主题/账号结构与移动端 Footer，导致账号信息不显示且业务页错误进入 `Please sign in.`。本模块从重复源头修复，不在 Tools 单页补 HTML。

#### 13.7.1 唯一 owner 与装配规则

- `AppShell` 是业务页 Brand、主导航、主题控件、账号菜单、移动菜单、可选二级导航、页面内容 slot 和 Footer 的唯一前端 owner。
- 主导航使用一份声明式清单维护稳定 `pageKey`、标题、URL 和图标；激活态只由入口声明的 `pageKey` 决定。`audit-email.html` 归属 `tools`，全部 Wi-Fi 子路由归属 `wifi`。
- 普通业务 HTML 只保留文档元数据、公共样式、Shell 根节点和页面入口脚本，不得复制 `.top-nav`、`.mobile-menu`、`.nav-menu`、主题、账号或 Footer 结构。
- 页面入口先同步装配 AppShell，再启动认证/偏好与业务组件。`authenticated-page` 只管理内容 slot 的账号生命周期，不创建或修补 Shell。
- Wi-Fi 二级导航通过 AppShell 的可选 slot 显示；Login 保持独立认证布局，不强行使用业务 Shell。
- Shell 缺少必要根节点时明确失败；不得因为某个可选页面组件缺失而跳过认证、主题或账号初始化。

#### 13.7.2 隔离与变更边界

- 页面业务模块只接收自己的 mount root、API 和 session，不查询或改写 Shell 内部 DOM。
- Shell 不导入 Jira、Confluence、Projects、邮件或 Wi-Fi 业务模块；页面入口负责组合，保持 `web/frontend -> web/backend -> core` 依赖方向。
- 账号切换只销毁并重挂当前业务组件；Shell 实例、导航定义和公共视觉结构保持唯一。
- 新增页面只新增入口和注册 `pageKey`，不得复制公共结构；新增导航项只修改导航清单和对应路由测试。

#### 13.7.3 已批准实施清单

- [x] 以 Tools 缺少账号挂载点导致认证 Shell 未启动为 RED，建立所有业务入口共享完整 Shell 的行为测试。
- [x] 建立唯一 AppShell 和导航清单，装配桌面/移动导航、主题、账号挂载点、内容 slot、可选 Wi-Fi 二级导航及 Footer。
- [x] 将 Dashboard、Projects、Jira、Tools、Settings、Inbox、Analytics、Audit Email 和 Wi-Fi 入口迁移为轻量页面宿主；Login 保持独立。
- [x] 删除业务 HTML 中重复 Shell 标记和手写激活态，保持现有页面入口、业务模块、认证、偏好和过滤器单例行为不变。
- [x] 运行 Shell/认证/页面入口聚焦测试、前端全量测试、lint、build、源代码浏览器验证与 `git diff --check`；确认 Tools 显示与其他页面一致的账号信息。

### 13.8 两个过滤器的物理唯一性

全仓只允许两处过滤器业务实现及两套对应持久状态：Confluence Filter 与 Jira Filter。前端可以在任意页面引用同一过滤器组件，页面可以在已应用边界内分页、排序、选择记录或打开 drilldown，但不得再定义第三套过滤字段、缓存、数据库 snapshot、页面偏好或参数拼装 owner。

#### 13.8.1 Confluence Filter

- `ConfluenceQuerySnapshotRepository` 是唯一持久 owner，保存 canonical filters、search、精确 `project_ids` 和 revision。
- Projects、Tools、Dashboard 引用同一前端组件、字段 schema、候选、Apply/Reset 和恢复逻辑；不得复制或适配另一套项目过滤器。
- 只有 Apply/Reset 修改单例。GET、页面进入、轮询、缓存刷新、分页和后台同步只能在当前快照边界内读取，不得创建或覆盖过滤快照。
- Dashboard 删除独立项目过滤器和 `release-dashboard` 过滤 snapshot；其列表、详情、分页及 drilldown 均受 Confluence 快照 `project_ids` 约束。
- Confluence 手动审查与邮件直接消费快照中的精确 IDs，不重新按前端条件求范围，也不写回过滤器。

#### 13.8.2 Jira Filter

- `JiraFilterSnapshotRepository` 是唯一持久 owner，保存 Project、Type、Status、Current User、Resolution、JQL 和 revision。
- Tools 及所有 Jira 数据消费者引用同一组件/schema；下拉为多选，JQL 只在后端唯一 owner 组合。
- 删除 `jira-release-workbench` 的过滤 snapshot 与独立过滤字段实现。Dashboard Jira 聚合、审查、邮件和后续 Jira 页面只消费 Jira 单例；drilldown 只携带稳定记录身份/分页游标，不保存过滤状态。
- Jira 候选复用既有 Jira cache/repository，空数据库也必须先完成 schema 初始化并返回完整五字段结构；候选读取不访问远端。

#### 13.8.3 前端引用与查询边界

- 过滤器组件可以多实例渲染，但共享同一后端 snapshot/revision；任一引用 Apply 后，其他引用重新显示时必须呈现相同已应用值。
- 页面 preference 不保存业务过滤值。纯展示偏好可使用过滤器级全局 UI scope，且不得覆盖 snapshot。
- 页面分页、排序、选中项、展开项和详情定位是瞬时视图状态，只能缩小或浏览单例边界内的数据，不能扩张范围或持久化为过滤器。
- Jira 与 Confluence 独立加载与报错；任一失败不得阻止另一过滤器显示已缓存候选和状态。

#### 13.8.4 已批准清理清单

- [x] 以跨 Projects/Tools/Dashboard 的状态不一致和 GET 覆盖 snapshot 为 RED，锁定只有 Apply/Reset 改 revision。
- [x] 收口 Confluence 唯一组件与 API，删除 route-scoped 业务 preference、GET 写 snapshot、Dashboard 项目过滤器和 `release-dashboard` 过滤持久化。
- [x] 收口 Jira 唯一组件与 API，删除 `jira-release-workbench` 过滤持久化和独立字段实现，修复多选、schema/candidate owner 与独立加载。
- [x] 让 Dashboard、两项审查、邮件及分页/drilldown 严格消费两个单例边界；删除重复字段映射、参数拼装和兼容路径。
- [x] 检查数据库与生产代码只剩两个过滤 scope/owner；完成前后端聚焦与全量测试、lint、build、实际浏览器跨页验证和 `git diff --check`。

采用“基础模块先行、业务模块逐个交付、Home 后聚合、最后统一整合”的顺序：

1. **Web 基础壳与设计体系**：模块化路由、布局、导航、状态组件和 API 基础；
2. **身份与权限模块**：复用登录、人员映射、I/M 岗和管理范围；
3. **人员只读模块**：验证人员身份、汇报树和产品线范围；
4. **项目只读模块**：复用 Confluence 项目发现、列表和详情；
5. **审查与历史模块**：Web 自有数据库、自动快照、人工阶段节点和回溯；
6. **Jira/Redmine 辅助聚合模块**：在项目与人员范围内关联缺陷和任务；
7. **工作量模块**：先按来源展示，再形成已确认的综合视图；
8. **客观参考指标模块**：只面向管理岗，不做正式绩效结论；
9. **Home 管理工作台**：聚合已完成模块，不提前制造临时数据流；
10. **Wi-Fi Database 整合**：统一认证、导航和视觉；
11. **SmartTest 测试量接入**：复用测试任务、运行和报告 owner；
12. **服务器化与最终整合**：数据库、调度、部署、备份、监控、安全和全链路验收。

模块顺序可以由 Coco 调整，但后置模块不得通过临时复制绕过尚未完成的基础 owner。

## 14. 单模块交付方式

每个模块使用同一流程：

1. 调查现有 owner、接口、数据和测试；
2. 写入或更新本文档中的模块设计与执行清单，不另建重复计划文档；
3. Coco 审核业务边界和验收标准；
4. 按风险选择 Atlas-only 或 Atlas + Mason；
5. 实现并执行聚焦测试；
6. Coco 在实际环境确认功能完整；
7. 清理冗余、临时诊断和探索残留；
8. 完成代码质量检查并交付；
9. 下一模块复用已经交付的公共能力。

## 15. 总体验收标准

### 15.1 功能验收

- 登录身份与人员映射正确；
- I 岗只能读取本人及参与项目范围；
- M 岗范围正确覆盖汇报树和负责产品线；
- API、页面、统计和导出不存在权限差异；
- Confluence 是项目主数据，Jira/Redmine/SmartTest 作为可追溯辅助来源；
- 第三方数据保持只读；
- 自动快照和人工审查节点可分别保存与回溯；
- 历史记录不被后续采集覆盖；
- 工作量和参考指标带来源、周期、更新时间和口径；
- Home 只聚合已有模块结果；
- Wi-Fi Database 现有能力无回归。

### 15.2 代码质量验收

- 没有重复人员、项目、认证、采集、审查、调度或测试模型；
- 前端不直接访问 `core/` 或第三方平台；
- 后端不复制已有核心业务实现；
- 模块 owner 和接口清晰；
- 没有临时模拟数据、调试输出、废弃尝试或无关改动；
- 聚焦测试、最高实际环境验证和 `git diff --check` 通过。

## 16. 当前明确不做

- 通过 Web 修改 Confluence、Jira 或 Redmine；
- 通过 Web 编辑 `personnel.json`；
- 建立独立于 SmartTest 的本地账号体系；
- 建立 Web 专用项目清单；
- 正式绩效分数、绩效等级、公开排名、奖惩或人事结论；
- 未经确认的第三方双向同步；
- 为未来可能性预先拆分微服务。

## 17. Jira 卡片滚动时间选择（Coco 已确认）

- Self-Test 固定 QA reporter、Bug、Self-Test Channel；移除年度条件。
- 各 Jira 卡片在产品线下提供 Week / Month / Quarter，默认 Month，互不影响产品线和统计维度。
- Core `JIRA_PERIOD_CONDITIONS` 集中声明最近 7 天、30 天和 3 个月；季度使用 Jira `startOfDay("-3M")`，由 Jira 自身时区确定日起边界，不换成 90 天、不自行计算日期。
- 用户条件与卡片固定条件/时间边界只经 `compose_jql` 生成 effective JQL，校验、执行、SQLite 保存使用同一字符串。
- Dashboard/Jira 同卡片共用接口和结果 owner。GET 从同一有效年度 snapshot 一次返回四周期本地视图且不查询远端；前端不保存 period preference。
- Customer 仍只占位，时间控件复用偏好 owner，不注册查询业务。Confluence 卡片不变。
- 显式 Apply 调用 POST query 刷新最大年度 snapshot；周期切换只在前端已有 payload 中同步选择数据与 Current JQL，零 API、无 reuse。GET 的命中路径只读已编译 userJql、Core 组合及 SQLite，不访问 fields/validate/gateway。当前有效 snapshot/最后 attempt 仍由现 query owner 维护，不篡改采集时间、不删失败历史，旧运行任务不得覆盖新选择。
- [x] 集中时间声明、选择恢复、共享查询、布局与耐久测试。
- [x] 完整前后端测试、lint、build、diffcheck 验收。

## 18. 主题首帧恢复（Coco 已确认）

- 单份共享 CSS 不变，Vite HTML owner 在 CSS 前统一注入轻量主题 bootstrap，覆盖 dev/build、登录及 Wi-Fi 共用 HTML 入口。
- 浏览器 `smarttest:theme` 仅为可丢弃首帧提示，无标记沿现默认 light，不猜 OS 主题。SQLite 账号偏好仍权威，异步恢复不同主题才校准。
- 共享 theme owner 统一 shell/settings 与登录旧入口的切换，立即更新标记；账号变更/注销按现生命周期清理标记，普通页面导航不清理。不延迟页面、不新增遮罩、业务缓存或主题 CSS 副本。
- [x] head 执行、切换立即写入、偏好校准与清理边界耐久测试。
- [x] 全前端、lint/build、入口顺序与 diffcheck 验证。

## 19. Jira 上期原过滤条件复查（Coco / Atlas 已批准收敛）

- 本期 Jira 与 Confluence 一周审查及历史对比保持不变；仅 Jira 正文与整改附件增加上期原问题比较，Confluence 不再有整改逻辑、结构问题明细或整改附件。
- Core fixed_weekly_audit_scope 统一给出北京时间本周周一到周五审查范围及明确上周范围；manual / scheduled 共用，定时周五18:00。09-18审查09-14到09-18、基线09-07到09-11，不能再向08-31错退。
- SQLite AuditEmailHistory 按 Core 传入明确上周范围、同账号选择最新 completed Jira 审查，不自行减七天，与 SMTP accepted 无关；不跳更旧报告、不用同期代替上期。没有原过滤条件不能重审；缺原规则明细仅不能逐项比较，不阻止同JQL汇总复审，不伪造零修复。
- 原过滤条件读取已保存 summary.jira.scope，即原 report.resolved.jql；通过既有 resolve/run 重新执行完整审查及既有并发 AI，不新增 exact-key recheck 或线程 owner，不用当前结果还原过去。
- 原问题按资源 ID + rule ID 比较；当前原过滤器审查未返回原资源或原规则不可用，明确无法核验，不能当作修复。重新执行失败保留原问题并显示无法核验。
- Jira 正文整改区沿既有全宽表格、浅蓝表头、边框与 padding 内联样式；正文和 XLSX 共用说明及数据，无基线、零问题均有可读说明表。原附件内容保留。
- Confluence 汇总和附件名称均复用 PRODUCT_LINES 的空间码/完整 Business 名定义，修复 KeyError 与旧空间码文件名，不另建转换字典。
- 使用既有 record/smart_log 打印基线选择原因/周期、SQLite scope 来源、resolve/run 进度、原/现资源规则数量、对比计数与输出；不打印密码或敏感内容。
- 用户收尾指定收件人为 fae.qa@amlogic.com；本轮不改 SMTP，不在开发时发送邮件、访问远端或调用 AI。
- [x] 删除 Jira exact-key / Confluence 整改机制和废弃测试；完成原过滤器复查与相邻 completed 基线。
- [x] HTML / XLSX、原过滤器集成、失败发送仍可作基线、Confluence 无整改、完整产品线附件名回归。
- [ ] 内网实际邮件样式由 Coco 验收。

## 20. 待后续模块确定

本轮§19补充（Coco已批准）：上期仅有原summary和JQL而缺findings时，仍执行保存JQL并展示上次原审查/本次重审四项汇总，正文不展示逐规则明细；原明细缺失明确说明不能逐项比较，完整本次明细调用既有Jira XLSX exporter。AI按完整自然语义复核全部违规，提示词提供动态完整JSON示例，strict parser保持；安全阶段日志仅记录资源key、模型/endpoint、耗时、类别/HTTP状态及返回key存在/匹配布尔值。验收后恢复手动与定时相同的Jira/Confluence双审查，收件人改fae.qa@amlogic.com；正文当期称本周，上周原JQL复查仍指上周，展示时间统一北京时间。audit_email_runs为唯一持久owner，按独立run保留summary/原JQL/时间/Jira规则目录及issue最终违规/AI状态、复审同结构结果和比较明细、Confluence项目与规则状态；只扩JSON，不新增表或年度统计业务，不补造旧9月14日缺失明细。精确临时历史清理由Atlas备份后执行，Mason不操作业务数据。

以下内容在对应模块启动时，根据实际环境和领导意图确定，不影响当前总体方向：

- 现有 SmartTest 认证在浏览器中的具体会话承载方式；
- Web 自有数据库产品及服务器部署拓扑；
- 自动快照周期、保留年限和归档策略；
- 人工审查的具体状态、角色和表单字段；
- Confluence、Jira、Redmine 指标的具体统计口径；
- SmartTest 测试量纳入工作量的时间和规则；
- 是否以及何时升级为正式绩效考核；
- 后续 Home 业务内容对现有 SmartTest 主题的扩展方式。
