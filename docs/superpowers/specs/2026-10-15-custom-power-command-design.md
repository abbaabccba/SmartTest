# 自定义上电 / 掉电命令

Coco 已在 2026-10-15 本轮对话确认方案及实施：提供客户可编辑入口，分别填写上电和掉电完整命令，确认 UI 后交由 Coco 简测。本记录不增加审批环节。

## 边界与接口

- 沿用 Test 页 Env 的 relay 配置 owner，新增 `custom_command` 类型和 `power_on_command`、`power_off_command` 两个字符串字段，沿用设备配置保存、恢复及运行链路。
- UI 展示“自定义命令/脚本”和两个全宽输入框，提示完整命令在运行测试进程的主机执行。
- relay factory 创建适配器，`pulse("power_on"/"power_off")` 选取对应命令；AC 既有循环不变。
- 复用 `core/testing/tool/dut_tool/command_batch.py` 的 `CommandRunner`，交由主机 shell 执行用户命令并等待结束。退出码 0 成功，非 0 抛错；通过 `core.logging.smart_log` 记录 stdout/stderr。客户脚本负责确认实际电源动作。
- 不新增重试、回退、超时字段或硬件探测，不改变 SNMP / USB 行为，不构建安装包。

## 执行清单与验收

- [x] 扩展设备配置契约、行模型、relay factory 和适配器。
- [x] 完成 UI 全宽输入、中英翻译与 QRC 资源生成。
- [x] 验证两个方向实际命令执行、非零失败、持久化和工厂流，检查既有设备行为。
- [x] 加载真实 QML 验证类型入口、编辑与恢复，说明源码运行范围。
- [x] 清理临时产物，运行范围测试及 `git diff --check`，交 Atlas 验收。

## 验证记录

- 自定义命令/配置与真实 QRC QML 页面回归，加既有 Test 页和产品边界单测：44 passed。UI 检查包括第三类型选择、中文翻译、全宽输入编辑、保存及页面重建恢复。
- 使用源码与本地重建的 QM / resource_rc.py，未构建安装包。客户仍需验证自己的脚本与实物通断。
- 完整源码入口启动探测受临时环境缺少 `aiohttp` 影响；独立真实 Test 页已加载并交互通过。
- `check_product_boundaries.py` 报告既有根目录 AI/debug/tools/testing/android_client/ui；本次未改动这些目录。范围单测、compileall 与 `git diff --check` 通过。
