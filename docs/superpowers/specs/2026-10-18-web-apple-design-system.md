# Web Apple 风格设计系统改造

## 目标与边界

SmartTest Web 采用“macOS 生产力应用为主体、Apple 官网排版为补充”的统一视觉语言。改造覆盖全部 Web 页面及共享组件，不仅限于 Jira 卡片。

本次只改变展示层：CSS token、公共组件样式、页面空间布局，以及为绑定公共样式所必需的前端 DOM class。不得改变 API、数据库、缓存、认证、任务调度、数据获取、筛选规则、状态 owner、按钮业务动作或路由语义。

Apple 官方规范只作为设计原则来源，不复制 Apple 专有 CSS、素材或受限设计资源。实现使用项目自有 CSS 与现有图标资源。

## 设计原则

1. 内容层保持安静：页面背景、数据区、表格和卡片使用标准实体表面，不到处叠加玻璃。
2. 功能层适度悬浮：导航、工具栏、分段控件、弹层和临时交互可使用半透明材质和轻微模糊。
3. 以留白、排版和材质层级划分区域，减少横线、粗描边、彩色外框和重复容器。
4. 信息密度面向桌面生产力：紧凑但不拥挤，常用数据同屏展示；页面标题和空状态可借用 Apple 官网更宽松的节奏。
5. 一个强调色只表达交互选择；产品线色与告警色仅保留业务语义，不作为普通装饰。
6. 明暗主题、键盘焦点、减少动态效果、减少透明度和高对比度必须同时可用。

这些原则对应 Apple Human Interface Guidelines 的 macOS、Materials、Color、Buttons、Sidebars 与 Focus and selection 指导：大屏保持舒适信息密度；玻璃用于控件和导航层而非内容层；控件使用一致尺寸；文本输入保留清晰焦点环；侧栏保持浅层级和熟悉图标。

## 公共 Owner

### 1. Foundations

`web/frontend/src/smarttest-theme.css` 继续是 Web 全局视觉 owner，扩展为四类 token：

- `--st-space-*`：4/8/12/16/24/32/48 的空间节奏。
- `--st-radius-*`：控件、卡片、弹层和胶囊圆角。
- `--st-surface-*`：页面、内容、抬升、导航、选中和遮罩材质。
- `--st-text-*`、`--st-border-*`、`--st-shadow-*`、`--st-control-*`：文字层级、边界、阴影与控件尺寸。

以上为拟议名称；实施时优先迁移现有 `--bg-*`、`--text-*`、`--border-color` 和 `--accent*` 的值与引用，不并行维护两套长期 token。迁移完成后旧 token 若无引用应删除。

字体使用系统栈 `-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", sans-serif`；不打包或冒用 Apple 字体文件。数字密集区域使用 `font-variant-numeric: tabular-nums`。

### 2. Layout

公共页面壳负责三层空间：

- 导航层：现有顶部导航改为半透明功能层，滚动内容从其下方经过；不增加新的路由或侧栏业务。
- 页面层：统一最大内容宽度、左右安全边距、标题区和区块间距。
- 内容层：Dashboard 保留网格，Projects/Jira/Tools 保留各自内容结构，仅统一容器、分栏、间距和响应式断点。

宽屏优先展示更多内容，窄屏按现有 DOM 顺序折叠；不因视觉改造改变卡片顺序、筛选器归属或数据加载时机。

### 3. Controls

现有语义 class 继续作为公共入口，不创建页面专用副本：

- `.button`：统一高度、圆角、内边距、主次操作、危险操作、disabled、loading、hover、active、focus-visible。
- `.form-control`：统一 input、textarea、select、date、search 的表面、字体、占位符、焦点环和错误态。
- `.role-segments`、周期选择等：收敛为同一分段控件视觉 owner；业务选项和值不变。
- 标签与 badge：普通分类使用中性色胶囊；状态与告警才使用语义颜色。
- icon button：无装饰图片按钮使用统一方形点击区、轻量 hover 表面和 tooltip/aria-label。
- 多选、菜单、弹层、分页和异步反馈复用相同控件 token。

### 4. Content surfaces

- `.card` 成为标准内容表面：弱边界、低阴影、统一圆角；禁止每个子区再画分隔框。
- 表格和列表以行间距、选中背景和 hover 背景表达层级，减少纵横网格线。
- 图表容器使用内容表面，不使用功能层玻璃；图例、轴文字和 tooltip 接入公共主题 token。
- 空状态、错误、加载与成功反馈保留原业务文案和状态，只更新呈现。
- 滚动条仅在精确指针设备上使用窄轨道、圆形滑块与 hover 加深；不得隐藏可滚动性，Firefox 与 WebKit 分别适配。

### 5. Product-line visuals

`product-line-theme.css` 与现有动画 owner 保留。产品线颜色、图案和动效仍表示业务身份，但外层按钮尺寸、焦点、选中材质和布局服从公共控件体系。不得因全局主题覆盖产品线可读性或动画生命周期。

## CSS 与 JS 结构

优先通过现有 class 和级联完成改造。只有当前 DOM 无法表达 toolbar、field、group、list row 等通用语义时，才在对应展示 owner 中增加 class；不得在 JS 中写颜色、阴影、圆角或页面尺寸。

拟议 CSS 分层：

1. `smarttest-theme.css`：token、reset、页面壳与基础组件。
2. `product-line-theme.css`：产品线语义视觉与动画衔接。
3. `widgets/*.css`：组件特有几何和图表布局，只引用公共 token。
4. 页面局部规则：仅处理该页面独有布局，不重定义 button、input、card 等公共外观。

不引入 React、Tailwind、第三方 Apple UI 库或运行时 CSS-in-JS；复用当前原生 JS/CSS 架构，避免依赖与业务重写。

## 覆盖范围

- 登录页与登录背景。
- Dashboard 导航、布局、卡片和编辑态。
- Jira 筛选器、统计卡片、工作台、分页和详情区。
- Projects 筛选器、摘要、阶段分组、项目列表和详情。
- Audit Email、Tools、Settings、Analytics、Inbox、Test Management 等静态或工具页面。
- 公共下拉、多选、按钮、输入、表格、反馈、下载、主题切换和滚动条。

## 实施顺序

1. 基线盘点：建立页面/组件视觉清单与 Chrome 截图基线，不改业务。
2. Foundations：收敛颜色、字体、间距、圆角、材质、阴影和动效 token。
3. Shell：统一 body、导航、页面宽度、标题区和响应式空间。
4. Controls：按钮、输入、选择器、标签、分段控件、菜单、滚动条。
5. Content：卡片、列表、表格、图表表面、状态反馈与弹层。
6. Pages：逐页删除覆盖公共体系的旧样式，保留业务独有布局与产品线视觉。
7. Cleanup：删除无引用 token、重复规则、失败尝试和临时视觉探针。

每一步都在同一分支连续完成，但按层验证，避免一次替换导致无法定位视觉回归。

## 验收标准

1. 所有 Web 页面使用同一字体、空间、圆角、表面、控件与焦点体系。
2. 导航与临时控件层可使用适度玻璃；数据内容层无玻璃滥用。
3. 页面不再依靠大量横线、粗边框和重复嵌套卡片划分区域。
4. 按钮、输入框、选择器、标签、表格、滚动条和导航在亮暗主题下均一致。
5. Dashboard、Jira、Projects 的业务结构、数据、筛选、任务、刷新权限和交互结果与改造前一致。
6. 产品线视觉身份保留，且不覆盖公共可访问性状态。
7. 360px、768px、1280px、1440px 视口无横向溢出或关键操作不可达。
8. 键盘焦点清晰，文本对比可读；`prefers-reduced-motion` 与 `prefers-reduced-transparency` 有静态替代。
9. 全量前后端测试、前端 lint/build、Chrome 关键页面视觉检查与 `git diff --check` 通过。

## 执行清单

- [x] 记录现有公共 class、重复样式和关键页面入口；Chrome 连接不可用，截图基线未取得。
- [x] 收敛并测试全局 token，覆盖亮暗主题及辅助功能媒体查询。
- [x] 改造页面壳、顶部导航、页面标题与全局空间布局。
- [x] 改造按钮、图标按钮、标签、输入、选择器、多选、分段控件和滚动条。
- [x] 改造卡片、表格、列表、弹层、反馈和图表容器。
- [x] 通过共享 class 级联覆盖 Dashboard、Jira、Projects 与其余工具页面。
- [x] 验证业务事件、API 调用次数、状态恢复和展示数据未改变。
- [ ] 在真实 Chrome 完成亮暗主题、响应式、键盘和辅助功能视觉验收。
- [x] 删除外部字体和重复基础效果，未增加临时诊断，完成自动化全套验证。
