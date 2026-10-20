# Core 文本标记唯一渲染入口

## Coco 已确认的边界

在 core 下封装唯一公共入口；不用单例，不按文档格式再建业务工具类、注册器、工厂或平行渲染封装。解决 Jira 评论目前直接展示 Wiki 标记的问题，复用现有 Markdown 能力。数据库原文、Download、统计、采集和其他业务保持不变。

## 公共契约（拟议）

新增 `core/markup.py`，只公开：

```python
render_markup(source: str, *, source_format='markdown') -> str
```

- `source_format` 只支持明确的 `markdown`、`jira-wiki`；不自动猜测格式，不引入其他文档格式。
- Markdown 复用已声明的 `Markdown==3.10.2` 及既有 `extra`、`sane_lists` 扩展。
- Jira Wiki 使用 `jira2markdown==0.5.3` 转换后交给同一个 Markdown 入口，不自写 Wiki 正则解析器。
- 每次调用独立解析，避免跨请求残留或跨线程共享可变解析状态；没有单例对象或全局文档缓存。
- 返回 HTML 片段，公共入口只负责转换，不表示可直接信任并插入浏览器；Web 输出边界负责净化。
- 不支持的格式明确失败，不静默猜测或改用其他格式；库不支持的 Wiki 宏不另写业务兜底。

## 复用与调用方

1. `core/email/outlook/renderer.py` 的现有 Markdown 分支改调用公共入口，移除直接转换调用；原 HTML 分支、CID 图片重写、模板及纯文本字段保持不变。
2. Jira 已有按需动作接口对字符串评论显式使用 `jira-wiki`；返回正文原文及独立的已净化 HTML 展示字段。持久层不保存渲染结果。
3. 当前结构化正文仍按已确认的安全文本方式显示；本次不新增 ADF 渲染或修改审查中的文本提取。
4. 前端现有动作列表仅将服务端该展示字段用于富文本，其余正文继续安全文本显示；Verify、附件元信息和其他卡片不改。
5. 下载仍使用数据库原始正文和原始 payload，服务端 Download 分页无需渲染正文。

## Web 输出安全与样式

在现有 Jira 详情响应边界直接使用 `nh3==0.3.7` 净化转换结果，不再建另一层文档渲染业务封装。

- 保留表格、段落、列表、代码、换行、链接和受限文本颜色；删除脚本、事件属性、任意样式、iframe、图片等主动资源。
- 正文图片不自动加载，附件不预取、不下载本体。现附件元信息链接保持仅用户点击访问。
- 链接只接受 http/https，使用新标签页和 `noopener noreferrer`；不允许危险协议或相对资源触发请求。
- 浮层富文本 CSS 沿用现有字体、文字、边框和表面 token，表格可滚动；外部明确文本颜色不被公共主题强制覆盖。
- 不把渲染正文加入卡片统计响应或 Redis，不新增远端请求，不改变已确认的版本与账号校验。

净化放在 Web 边界是为了保留邮件本地图片→CID 链及原 HTML 行为；公共入口不增加邮件/Web 策略参数组合。

## 依赖与限制

- `jira2markdown 0.5.3`：MIT，Python >=3.9，依赖 pyparsing >=3。官方仍标 Alpha；本次仅承诺已验证的表格、单元格换行、Wiki 链接和颜色语法，不承诺全部 Jira 宏保真。
- `nh3 0.3.7`：MIT，Ammonia HTML 净化绑定，提供 Windows/Linux wheel。
- 更新后端运行依赖与项目虚拟环境初始化清单，核验传递依赖；公共入口在邮件路径避免无条件加载 Wiki 转换器。
- 检查 Desktop PyInstaller 分析和 runtime import smoke 链，声明实际需要的依赖，不在普通调试中重建安装包。
- 官方来源：<https://github.com/catcombo/jira2markdown>、<https://github.com/messense/nh3>、<https://python-markdown.github.io/reference/>。

## 执行清单与验收

1. Mason 增加唯一函数并接入现邮件 Markdown 分支，删除旧直接转换入口，不新建格式类。
2. 声明并安装已确认依赖，验证许可证、版本及运行/打包声明链。
3. Jira 原按需详情出口转换和净化；前端复用现浮层显示富文本，下载和数据库原文不变。
4. 覆盖表格/换行/链接/颜色、注入/危险 URL/图片不加载、并发不串状态、邮件 Markdown/CID/模板/HTML 回归及原文下载一致性。
5. 运行受影响 Core/后端/前端测试、lint/build、必要 import smoke 与 diff check；已有基线失败独立列示，不改原业务迎合断言。
6. 对已保存的真实评论做只读展示核验，不重新采集；环境可用时实页验证亮暗、表格和链接。
7. Atlas 按 diff 与测试验收，报告实际支持范围和未验证项；不提交、不推送。

这是本任务唯一设计与执行清单文档；确认后直接 Atlas + Mason 实施，不另建实施计划或再次选择执行方式。
