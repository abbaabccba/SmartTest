# SmartTest Web Apple 风格规范、复合与切换

更新日期：2026-10-19。本文是 Web 视觉的唯一规范与执行清单，覆盖设计、图标和动画。Coco 已确认执行，公共样式与素材收敛已实现；实际环境验收范围见第 10 节，未测环境不标记完成。

## 1. 目标、范围与现行依据

公共界面统一复刻 Apple 的视觉与交互语言：以 macOS 生产力应用为主体，Apple 官网排版为补充。复刻包含布局、字体、材质、控件、图标、选中/焦点反馈、状态切换和动画，不以更换配色与圆角代替完整复刻。

此前 Web 文档中的 Fluent UI、TemplateMo 视觉规则作废，不作为后续开发或验收依据。`web/frontend/TEMPLATE_ORIGIN.md` 仅保留素材来源与许可记录。桌面 Client 的 QML/FluentUI 技术实现不在本次 Web 风格整理范围内。

实现只改变展示层及其必要 DOM class，不改变 API、数据库、认证、查询、权限、任务调度、缓存、筛选条件、状态 owner、路由和按钮业务动作。

明确保留本文第 4 节的五个产品线身份风格，以及既有登录 PCB/globe 背景动画机制。登录背景是独立登录场景装饰，不是第六个产品线皮肤；登录表单、卡片、按钮、图标仍消费唯一 Apple 公共控件。Jira 玻璃卡片与复杂下载按钮特效不在保留范围。

### 官方参考与项目决策

参考 Apple 官方 [Human Interface Guidelines](https://developer.apple.com/design/human-interface-guidelines/)、[Materials](https://developer.apple.com/design/human-interface-guidelines/materials)、[Icons](https://developer.apple.com/design/human-interface-guidelines/icons)、[Motion](https://developer.apple.com/design/human-interface-guidelines/motion) 和 [SF Symbols](https://developer.apple.com/sf-symbols/)。

这些资料用于确定形态、材质、图标与运动语言。本文的具体尺寸、时长、颜色和五风格保留边界是 SmartTest 项目决策，不声称它们都是 Apple 官方固定值。每组组件实施前记录实际参考页面或截图及状态，避免不同组件各自选择不同版本的效果。本文不新增多套可选 Apple 皮肤。

## 2. 公共基础规则

1. 数据内容区使用安静的实体表面；导航、工具栏、菜单与弹层可使用适度半透明材质，不能到处叠加玻璃。
2. 使用留白、文字层级和材质组织内容，减少彩色外框、粗描边、横线与重复嵌套卡片。
3. 桌面信息密度紧凑但不拥挤；保留页面现有业务结构、卡片顺序和响应式 DOM 顺序。
4. 公共强调色表达交互选择；状态色表达业务语义。五产品线身份色只作用于相应身份视觉。
5. 亮暗主题、键盘焦点、减少动态效果、减少透明度与高对比度需要同时验收。
6. 所有状态提示容器统一透明背景；语义文字、进度轨道与填充保留，不给空状态区绘制灰条。
7. 装饰不得用整张数据卡片的百分比高度截断控件；材质属于明确的完整容器，数据行数变化不改变控件区的装饰边界。

### 已有 token 基线

唯一公共基础 owner：`web/frontend/src/smarttest-theme.css`。以下为当前实现值，不是新拟议配置；像素按根字号 16px 换算。

| 控制点 | 当前值 | 用途 |
|---|---|---|
| `--st-space-1` 至 `--st-space-7` | 4/8/12/16/24/32/48px | 全局空间节奏 |
| `--st-radius-control` | `.625rem`，10px | 普通控件及产品线选项圆角 |
| `--st-radius-card` | `.875rem`，14px | 标准卡片 |
| `--st-radius-popover` / `--st-radius-pill` | `1rem` / `999px` | 弹层 / 胶囊 |
| `--st-control-height` / `--st-control-compact` | `2.375rem` / `2rem` | 标准 / 紧凑高度 |
| `--st-content-width` / `--st-safe-gutter` | `90rem` / `clamp(1rem, 3vw, 2.5rem)` | 内容宽度与安全边距 |
| `--st-surface-page` / `--st-surface-content` | 亮 `#f5f5f7` / `#ffffff`；暗 `#111113` / `#1c1c1e` | 页面与内容表面 |
| `--accent` | 亮 `#0071e3`；暗 `#0a84ff` | 公共交互强调色 |
| `--st-motion-state` / `--st-motion-easing` | `.16s` / `ease` | 公共状态与图标入场、菜单淡入/位移 |
| `--st-motion-panel` | `.22s` | 移动导航位移与遮罩过渡 |
| `--st-motion-progress` | `1.2s` | 不确定进度往复移动，不持续变色 |
| `--transition` | 引用公共状态时长与曲线 | 颜色、背景、边框、阴影、transform 过渡 |

字体使用系统栈 `-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", sans-serif`；数字密集区使用 `tabular-nums`。不随包分发 Apple 字体文件。

`--bg-*`、`--text-*`、`--border-color` 等现有引用可映射到公共 token，但不能与 `--st-*` 各自维护冲突值；清理无引用旧值，不为每页增加一套颜色表。

## 3. 公共样式只保留一份

| 层 | 唯一职责 | 禁止事项 |
|---|---|---|
| `smarttest-theme.css` | token、页面壳、基础控件、卡片、表格、状态、焦点与公共交互外观 | 在文件后部不断追加同一控件的修补规则 |
| `product-line-theme.css` | 五风格的纯色身份背景和图标 | 另造五套按钮尺寸、焦点或主题切换逻辑 |
| `widgets/*.css` 与页面 CSS | 组件独有布局、图表几何和空间关系 | 用页面前缀覆盖公共按钮、输入、卡片、分段控件外观 |
| 页面 JS | 绑定语义 class、状态与已有控件事件 | 写入颜色、阴影、圆角，复制图标素材或动画调度机制 |

公共控件各有一个基础定义，主次、危险、紧凑、选中、错误等差异在公共 owner 内定义语义变体；不同选择器可以共用同一规则，不要求所有元素只有一个 class。

后续若现有公共能力不足，应在公共 owner 扩展并由页面消费，不允许 `.projects .button`、`.jira .card` 等独立外观补丁。业务页允许设置排列、列宽、间距和内容高度，不将布局差异当作新皮肤。

统一对象包括按钮与图标按钮、输入与选择、多选、分段控件、菜单、弹层、分页、标签、表格、图表文字与 tooltip、空状态和异步反馈。错误色和成功色保留语义，状态不能仅靠颜色表达。

## 4. 唯一保留的五个专属风格

Coco 于 2026-10-19 最新确认：五产品线只保留纯色背景、身份图标与名称；后续已确认暗主题同步使用深色背景。Projects 产品线 summary、selected、普通按钮及 layout/card/badge/disclosure 共用对应主题的身份背景：亮主题使用浅底深色前景，暗主题使用深底近白前景；选中只消费公共边框状态，不另切背景。此前保留纹理、渐变、Logo 矩阵、设备/TV 图案、地球和 Wireless OFDM 背景动画的要求被本条明确取代；不再有产品线背景 DOM、素材加载或动画调度。公共控件的圆角、选中、焦点、disabled 与业务切换不变。

| 产品线标识 | 保留的身份视觉 | 背景与选中 |
|---|---|---|
| `China Operator` | 运营商身份图标、原专属纯色 | 复用 tone 与亮暗主题 background |
| `Smart Device` | 设备身份图标、原浅蓝纯色 | 亮暗主题分别配色，选中复用公共边框 |
| `TV` | 电视身份图标、原紫色身份纯色 | 复用 tone 与亮暗主题 background |
| `Global Operator & STB` | 地球身份图标、原蓝色身份纯色 | 复用 tone 与亮暗主题 background |
| `Wireless Connection` | 无线身份图标、原青色身份纯色 | 复用 tone 与亮暗主题 background |

`src/product-line-theme.css` 是五线纯色 surface 的唯一 owner，覆盖 layout/card/button/badge/disclosure；`bindProductLine` 仍只绑定展示语义。非登录页面不注入背景初始化脚本。公共图表方案 3/4 不受此次调整影响。

### 4.1 既有登录背景动画（明确保留）

Coco 于 2026-10-19 明确要求保留，禁止在公共样式清理时误删。`src/login-background.js` 是唯一背景选择、挂载与释放 owner，`src/login-motion.css` 拥有 PCB 动效；`src/login-main.js` 在启动时调用 `applyLoginBackground()`，`pagehide` 释放，BFCache `pageshow.persisted` 重挂。重复挂载沿用 WeakMap 去重，不另建动画调度或偏好存储。

- `LOGIN_BACKGROUNDS` 保留原 `pcb/globe` 顺序与选择规则。`smarttest.login.background` 使用 sessionStorage；有效值优先复用，否则按原 Math.random 选取，不把 PCB 猜作固定默认。
- 生产 `login.html` 的 `[data-login-board]` 保留 `data-motion="vivid"`；PCB 原 pulse/flow/trigger 为 5s，breath 为 4.8s，主题图片为 `public/images/login-pcb-white-cutout.png` 与 `login-pcb-dark-cutout.png`。
- PCB 按 `dark-theme` 切换图片，并通过 `theme:changed` 同步两段线路路径；`visibilitychange` 暂停隐藏页，`prefers-reduced-motion` 保持静态。销毁移除监听、图层及状态类。
- globe 懒加载 `src/login-globe.js`，由原共享 owner 收敛保留登录所需生命周期，继续复用未改动的 `global-globe.js`、地理数据与 `framing="login"`。离屏、隐藏、减少动态、主题、resize 和销毁规则保留；不依赖产品线语义绑定，也不为非登录页启动调度。
- `login-motion-preview.html` 与 `src/login-motion-preview.*` 是既有验收工具，保留 subtle/vivid/breath、主题与暂停操作；预览控件同样使用公共 card/form/button/分段外观。
- 背景资源、主入口和生命周期测试是正式保留项，不得再作为“非五产品线冗余装饰”删除。调整或取消此机制必须先取得 Coco 明确授权。

## 5. 风格复合与切换

复合顺序为：**Apple 公共基础 → 亮/暗主题取值 → 产品线身份视觉（适用元素）→ 交互与业务状态 → 可访问性约束**。

- 公共基础始终生效，产品线身份层是可选叠加，不整页切换为另一套控件风格。
- 沿用现有 `dark-theme`、主题偏好 owner 与 `[data-product-line][data-product-surface]` 绑定，不由页面自己监听并重建主题。
- 当前入口为 `theme.js` 的亮暗主题应用（现有本机键 `smarttest:theme`）、`product-line-semantics.js` 的 `bindProductLine()`；产品线无装饰生命周期，登录地球独立使用 `login-globe.js`；图表主题经已有 `theme:changed` 接入。继续复用现有账号偏好同步，不另建皮肤存储。
- 同一公共 token 随亮暗主题改变；产品线背景与前景使用各自亮暗主题 token，普通文字服从主题，纯色身份背景保持可读。
- 产品线切换只改变目标元素的身份绑定和既有内容；焦点、disabled、loading、选中等公共状态完整保留。
- 切换主题或产品线后，图标颜色、Canvas 文字与图表通过已有公共主题接入更新，不新增平行状态保存和消息通路。
- `reduced-motion`、隐藏、离屏、disabled 与销毁沿用动画 owner 的暂停和回收规则，恢复时不重复创建调度器。

**现状：**已有亮暗切换和产品线绑定；不存在已交付的多皮肤选择器。本文仅定义复合与切换边界，不把新增主题选择菜单、皮肤配置或偏好字段当作本轮实现任务。

## 6. 图标复刻

图标是正式复刻对象：覆盖导航、工具栏、按钮、筛选、展开/收起、复制、下载、状态、主题切换和空状态；五个产品线身份图标保留。

- 对齐 Apple 风格的轮廓、笔画重量、端点、光学尺寸、基线和相邻文字比例；同一语义只维护一份图形定义。
- 普通界面图标使用一致的单色/层级着色规则和公共尺寸、点击区；颜色继承公共主题和状态语义。
- 不让各页面各复制一段 SVG 或用 emoji 充当公共图标。优先复用仓库图标 owner；先盘点现有依赖与资产，再确定缺失图形的复用来源。
- 素材来源、许可和跨 Windows/macOS/Linux 浏览器可用性应随资产记录。以 SF Symbols 为形态和行为参考，不把 Web 实现等同于已经接入 Apple 原生符号 API；未确认使用条件的素材不直接打包。
- 装饰图标不重复朗读，独立操作图标有 `aria-label` 或等效名称，不能以 tooltip 代替键盘可达性。

**当前实现：**`src/icons.js` 是公共界面图标的唯一语义入口，复用官方 `lucide@1.59.0`（ISC），统一 `currentColor`、`1em` 尺寸及 `1.75` 笔画；页面不再复制普通内联 SVG。五个产品线身份图标保持；旧背景艺术素材退出生产。依赖、来源与许可记录在 `package.json`、`package-lock.json`、`public/THIRD_PARTY_NOTICES.md`。这是 Apple 风格 Web 适配，不声称使用原生 SF Symbols API 或逐像素相同。

## 7. 动画与交互复刻

公共动画包含 hover/press 反馈、选中切换、菜单/弹层开合、展开收起、主题过渡、加载进度、状态图标切换。每个效果要记录触发、运动属性、时长、曲线、结束态、取消/快速反复切换及减少动态效果的替代行为。

- 先对照确定的 Apple 参考复现运动关系和反馈节奏，再落到公共 token/组件；不把所有动作机械设成相同时长。
- 公共动效定义一份，页面只触发状态；不按页面复制 keyframes、RAF、ResizeObserver 或主题监听。
- 少量控件优先 CSS `opacity` / `transform`；不因复刻加入高频布局测量或反复全页重绘。
- 控件选中效果必须与真实状态一致；关闭或销毁后清理动画，快速切换无残留遮罩和重复监听。
- 五产品线背景无持续装饰动效；登录背景动画仍由第 4.1 节独立 owner 保留，公共状态反馈不受影响。
- 普通页面额外的霓虹、持续闪烁、弹跳和复杂下载按钮特效需清理或公共化；既有登录背景动画按第 4.1 节明确保留，不据此恢复登录控件旧外观。

**当前实现：**公共状态使用 `.16s` 反馈；弹层沿用已有 `hidden/aria-hidden`，用透明度、4px 位移、`@starting-style` 与离散 display 过渡完成开合；移动菜单沿用原 `active`，使用 `.22s` transform 位移。没有新增 JS 动画队列或偏好 owner。公共 `reduced-motion` 处理置于这些定义之后，取消图标、菜单、多选箭头、分段和进度动效；五产品线背景现为纯色，无背景调度。旧公共控件常驻漂浮、霓虹与渐变扫光已清理；登录 PCB/globe 已按 Coco 纠正恢复并登记为保留机制。未引入弹簧参数；浏览器不支持离散过渡时沿原显隐状态直接开合。

## 8. 统一前代码清单（验收对照）

以下保留实施前的静态盘点位置，用于对照清理范围；运行代码已按第 10 节完成收敛，表内旧行号不再作为当前位置。视觉验收单独记录。

| 项目 | 证据入口 | 尚未统一的内容 / 后续方向 |
|---|---|---|
| 公共 token 与基础样式 | `src/smarttest-theme.css:11`、`:2399`、`:2421` | 旧定义与后置覆盖仍共存；合并为单一有效定义，保留必要映射 |
| 按钮入口 | 同文件 `:936` `.btn`、`:1930` `.button`；`src/pages/inbox.js:8` | 两套基础命名和外观仍需收敛，业务语义变体归公共 owner |
| 登录控件 | 同文件 `:1545` `.login-form .form-input`、`:1547` `.login-sign-in` | 独立输入表面、蓝渐变提交按钮与尺寸覆盖，改为公共控件消费 |
| Jira 内容卡片 | `src/widgets/ranking-card.css:34` `.jira-statistics-shell`、`:60` 标题 | 独立玻璃 token、表面、阴影和标题渐变仍在，与普通 Project 卡片不同 |
| Jira 时间分段 | 同文件 `:3` `.jira-period-button-group`、`:74` `.jira-statistics-card .jira-period-fieldset` | 旧按钮与玻璃分段覆盖仍在；需与属性分段共同消费公共外观 |
| 产品线选项外框 | 同文件 `.segmented-ranking .product-line-segments` | 独立白色渐变、强阴影外框不是五产品线身份本身，需公共化 |
| 主题开关 | `src/smarttest-theme.css:469` `.theme-toggle`；`src/app-shell.js:14` | 独立装饰与图标动效需归入公共图标/交互风格 |
| 页面控件覆盖 | `src/smarttest-theme.css:2222` `.jira-native-filter .multi-select__control`、`:2229` `.jira-more-menu` | 分别存在高度/内边距、圆角/阴影覆盖，区分必要布局与公共外观 |
| JQL 与图表表面 | `src/widgets/ranking-card.css:100` `.jira-current-jql`、`:111` copy button、`:119` chart scroll | 多层玻璃背景、边框和阴影需纳入公共 panel/button/card 边界 |
| 图标 | `src/product-line-theme.css:60`、`:122` Lucide 资产；`src/app-shell.js:14`、`src/widgets/jira-statistics-card.js:13`、`src/pages/inbox.js:8` 内联 SVG | 无统一 Apple 图标 owner；建立语义映射，去重并完成形态对照 |
| 五风格以外的动画 | `src/login-background.js:34`、`:61`；`src/login-motion.css:26`；`src/smarttest-theme.css:157` | 登录 PCB/globe 与局部线路脉冲明确保留原 owner；公共 async 反馈已收敛，不得将登录机制随普通控件装饰清理 |
| 视觉验收 | Dashboard/Jira/Projects/Login 与其他页面 | 缺完整亮暗、响应式、图标和动画状态对照，不能以测试通过代替 |

表内路径均相对 `web/frontend/`，行号为 `55d56590` 的代码盘点位置；实施时沿现有 owner 和选择器定位，不凭文档行号机械修改。

### 本轮之前已完成的收敛

- 状态提示背景集中为一份透明定义，登录页与卡片不再各自覆盖。
- 产品线选项使用公共圆角与对应主题的纯色背景。
- Project/Jira 属性分段移除旧蓝描边胶囊及滑块定位，使用公共样式。
- 删除随卡片内容高度变化的局部蓝色 `::before` 装饰。

这些已完成项不等同于上述整个体系已统一。

## 9. 后续执行清单与验收

- [x] 移除 Web 总设计、局部手册中的旧视觉基线，统一引用本文；保留历史来源记录。
- [x] 明确五个保留风格及公共/专属边界，记录现有切换能力。
- [x] 将图标与动画列为正式复刻范围，区分现状和待实现项。
- [x] 静态盘点未统一处，记录真实 owner 和选择器。
- [x] Coco 已确认按本清单执行，未另建重复实施计划。
- [x] 收敛公共 token、按钮/输入/分段/卡片定义，删除旧实现而非后置修补。
- [x] 清理 Jira、登录及其他页面的公共外观覆盖，保留独有布局。
- [x] 统一公共图标语义、资产定义、尺寸、状态和可访问名称。
- [x] 实现公共交互动效，删除冗余装饰与重复动画机制；真实录制与逐帧参考对照仍属下项验收。
- [x] 五个身份风格按最新要求改为亮暗主题纯色，保留图标与交互；登录地球生命周期回归保留。
- [ ] 在真实 Chrome 验证各关键状态及 360/768/1280/1440px 视口，录制交互动效；Windows/macOS/Linux 的字体与图标差异如实记录。
- [x] 运行受影响测试、前端 lint/build、业务契约验证及 `git diff --check`；删除临时探针、失败尝试与无引用 token。

验收同时要求：公共外观只有一个 owner、无页面重定义、无未登记的额外专属皮肤；卡片尺寸随内容变化时不截断控件背景；所有交互与业务结果不变；图标和动画完成实际参考对照。自动化通过、文档勾选或静态扫描均不能单独证明复刻完成。

## 10. 本轮实现与验证记录

| 范围 | 实施结果 |
|---|---|
| 公共基础 | 亮暗 token、按钮、输入、卡片、表格、状态、分段、菜单、图标按钮与焦点各归公共定义；清理旧 `.btn` / `.form-input` 及后置冲突覆盖 |
| 页面外观 | 移除 Jira 玻璃表面、标题渐变、周期/JQL/图表专属装饰；登录页保留既有背景动画及布局，控件复用标准 card/form/button；其他页面的日期分段、危险按钮等消费公共语义变体 |
| 图标 | `icons.js` 集中普通图标；删除静态 external-link 重复资产，多选箭头与拖拽/关闭字符也走同一入口 |
| 动画与清理 | 公共时长与状态驱动的 CSS 动效；恢复并保留登录背景、预览入口、两张主题 PNG 与生命周期测试；未增加动画调度机制 |
| 五专属风格 | `product-line-theme.css` 统一纯色；删除产品线背景 owner/专属素材；`global-globe.js` 与地理数据仍供登录使用，身份图标保留 |
| 窄屏 | 产品线与属性组选项保留完整标签，在组内横滚；键盘可达最后一项，不依靠裁切隐藏内容 |

自动化：完整前端 `42` 个测试文件、`313` 项通过；ESLint、Vite build、`git diff --check` 通过；受影响的后端开发启动/产品边界单测 `39` 项及边界 CLI 通过。构建保留既有大 chunk 提示。本轮未改 Core 或后端业务，未以正式数据库代替缺失的隔离测试库。

Atlas 实页：Windows Chrome 检查 Dashboard 亮暗、周期切换，Projects 筛选区/多选弹层，Jira 公共控件与弹层反复开合；Jira 在 360/768/1280/1440px 下页面无横向溢出，Dashboard 360px 键盘可滚到 Wireless Connection。独立未登录页面检查登录亮暗表单，没有提交登录或触发 Search/Apply。暗色实际解析内容表面 `#1c1c1e`、主文字 `#f5f5f7`；1440px 移动菜单按钮隐藏。反复关闭 More 后为 `hidden=true`、`display:none`、`opacity:0`，无残留菜单。

登录动画恢复复验：生产登录页实际为 `data-login-background="pcb"`、`data-motion="vivid"`，PCB 动画层恰好一层，亮暗切换后表单与动画层均保留；预览页切换线路脉冲和暂停后状态正确。globe 分支、页面隐藏/销毁与 BFCache 重挂去重由已恢复的生命周期测试覆盖，未以静态截图替代该分支的动画性能验收。

限制：未在 macOS/Linux 实机验证；未录制全套动效逐帧对照；减少动态/透明度与高对比度规则完成静态级联审查，未切换系统辅助功能设置做实页验证。因此保留第 9 节完整视觉验收项，不能将本轮代码收敛报告等同于所有环境的逐像素复刻验收。

## 11. 周期对比统计图配色（2026-10-19 确认）

| 方案 | 名称 | 当前周期 Current period | 上一周期 Previous period | 使用约定 |
|---|---|---|---|---|
| 3 | 青灰 · 克制耐看 | `#258B88` | `#C9D2D8` | 当前启用，先使用一段时间 |
| 4 | 靛粉 · 柔和精致 | `#6267A6` | `#D8B5C4` | 备用；Coco 后续要求改用方案 4 时切换 |

两套方案来自本次八组配色对比图，编号固定。不会按时间自动切换，也不新增页面配置或配色选择控件。

公共颜色唯一入口为 `web/frontend/src/smarttest-theme.css` 的 `--ranking-current-bar` 和 `--ranking-previous-bar`，所有共享排行卡片直接消费这两个变量，不按业务设置配色开关。Project、Jira 及 Dashboard 中对应卡片统一使用方案 3；只有一个数据系列的 Project 使用当前周期颜色，具有两期对比的卡片分别使用两色。当前亮暗主题均使用方案 3 的原色，删除旧暗主题蓝青覆盖及 Jira 专属配色分支，页面和卡片不另设颜色。后续切到方案 4 时仅替换这两个变量并更新本文启用状态；保留方案 3 的色号记录。五个产品线专属风格不受此配色调整影响。
