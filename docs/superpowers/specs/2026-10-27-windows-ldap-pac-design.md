# Windows LDAP 网络失败后使用系统 PAC

状态：Coco 已确认实施，直连重试间隔 2 秒、PAC 为 SOCKS5。实现分支：`codex/windows-ldap-pac`；交付远端仅 `myfork`（`abbaabccba/SmartTest`）。

## 范围与行为

仅改变 Windows 桌面端需要执行 LDAP 验证的登录路径。复用 `core/authentication/ldap.py:LdapAuthenticator`，不改变已有安全凭据恢复、账号生命周期、非 Windows、Web 和其他网络访问。

1. 直连实际配置的 LDAP 服务器，累计最多三次，每次建立独立连接，成功即停止；需要下一次直连时等待 2 秒，第三次失败后直接进入 PAC。
2. 仅网络连接/读写失败继续下一次。认证拒绝、TLS/证书、协议、配置和程序错误立即返回，不进入 PAC；bind 成功后身份信息读取失败不重新认证。
3. 三次均为网络失败后，读取 Windows 当前用户已配置的系统 PAC，使用实际 LDAP URL 和原始 hostname 求值，不增加 WPAD/DNS 自动发现。
4. 仅使用 PAC 返回的第一个路由条目，且该条目必须为 SOCKS5；通过它再认证一次。缺少 PAC、脚本失败、首项不是 SOCKS5、代理失败均明确报错，不自行跳过 DIRECT/HTTP 或改试其他路线。
5. 不先在本机解析 LDAP hostname，由 SOCKS5 代理解析。不增加 SOCKS4/4a 支持。

## 所有权与依赖复用

`LdapAuthenticator` 继续负责 LDAP/NTLM、错误分类和连接生命周期。`client/app/ui/example/bridge/AuthBridge.py` 现有异步认证入口显式启用 Windows 桌面策略，无需修改 QML 或增加第二套认证流程。

拟新增 `core/authentication/windows_pac.py`，仅封装当前用户 PAC 读取/求值及本次 LDAP 连接的 SOCKS 适配。复用 PyPAC 与 python-socks，不自写 PAC 执行器或 SOCKS 协议。ldap3 2.9.1 没有公开 socket_factory，且默认候选地址流程先本地 DNS，因此需针对本次实例适配 strategy/server 的建连入口；禁止全局替换 socket 或影响其他连接。实现时锁定兼容依赖版本，并补齐 Windows 开发环境和打包依赖链，不新增通用代理框架。

PAC token 必须明确为 SOCKS5，不将无版本 SOCKS 当作 v5。参考：[PyPAC API](https://pypac.readthedocs.io/en/latest/api.html)、[PAC parser](https://github.com/carsonyl/pypac/blob/master/pypac/parser.py)、[Chromium 协议语义](https://chromium.googlesource.com/chromium/src/+/HEAD/net/docs/proxy.md)、[python-socks](https://github.com/romis2012/python-socks)、[ldap3 建连入口](https://github.com/cannatag/ldap3/blob/v2.9.1/ldap3/strategy/base.py)。

## 控制点与错误边界

以下常量已集中定义在 `core/authentication/ldap.py`，不新增设置页面或环境变量：

| 控制点 | 值 | 作用 |
| --- | --- | --- |
| `DIRECT_ATTEMPTS` | 3 | Windows 桌面直连总次数 |
| `DIRECT_RETRY_INTERVAL_SECONDS` | 2 | 直连重试间隔 |
| `CONNECT_TIMEOUT_SECONDS` | 5 | 每次 socket 建连超时 |
| `RECEIVE_TIMEOUT_SECONDS` | 5 | LDAP socket 读取超时 |
| `PAC_TIMEOUT_SECONDS` | 2 | PAC 下载超时 |
| `PROXY_ATTEMPTS` | 1 | SOCKS 认证尝试次数 |

5 秒是本方案的交互体验建议，非外部标准。DNS、多候选地址与协议多次读写使总耗时不能按上述数值简单相加承诺上限。既有 `AMLOGIC_LDAP_HOST`（默认 `ldap.amlogic.com`）与 `AMLOGIC_LDAP_DOMAIN`（默认 `AMLOGIC`）含义不变。

网络分类检查具体 socket/DNS/超时底层原因，不将全部 LDAPException 或 LDAPSocketOpenError 一律视为网络失败；尤其避免将 TLS 错误重试。所有连接在成功、失败及异常路径关闭。不增加持久状态，不打印密码、凭据、PAC 脚本或完整 PAC URL。失败沿现有认证结果入口返回，界面不新增文案。

## 执行清单

- Mason 扩展唯一 LDAP owner，增加局部 Windows PAC/SOCKS 适配及桌面接入。
- 更新 `core/devtools/scripts/init_venv.py` 和 `client/packaging/tool_runtime_dependencies.py`，按实际打包需要同步 spec，不构建安装包。
- 补充重要行为和真实 socket 适配测试，执行现有桌面认证与 Web session 回归；具体测试文件按仓库已有 owner 复用。
- Atlas 从完整 git status、diff、测试证据及 git diff --check 验收，检查净生产代码增长、全局 socket 隔离及依赖完整性。
- 验收通过后提交至当前分支并只推送 myfork；本次不合并 main，不向 origin 推送。
- Coco 在 Windows 更新依赖并从源码运行，验收真实系统 PAC 与公司 LDAP；源码测试不等同于已发布 SmartTest.exe。

## 验收标准

- 首次成功停止；两次网络失败后第三次直连成功不读取 PAC；三次失败后 SOCKS 成功。
- 错误密码仅认证一次；非网络错误不重试；bind 后身份读取错误不重新认证。
- 非 Windows 与 Web 保持原行为，绝不读取 PAC。
- PAC 缺失/脚本失败/首项非 SOCKS5、SOCKS 失败如实返回；版本映射和远端 DNS 正确。
- 局部连接关闭、并发连接隔离，不污染全局 socket；成熟库负责协议。
- 最小必要自动化与 socket 级验证通过；Windows 实机结果单独记录，未完成前不宣称实机通过。

## 确认记录

Coco 已指示实施，并补充重试间隔 2 秒、PAC 为 SOCKS5。单次连接/读取超时仍为 5 秒；本次仅交付功能分支。

## 实现验证记录

- 复用 PyPAC 0.19.0、python-socks 2.8.2、dukpy 0.6.0；业务代码净增约 137 行。唯一 worker：`/root/mason`，Atlas 完成差异验收。
- PAC、既有桌面认证和边界检查器测试共 61 项通过（exit 0）；覆盖真实 SOCKS5 协商与 LDAP bind/unbind、远端 DNS、代理拒绝、连接关闭和并发隔离。compileall 与 git diff --check 通过。
- Web session 数据库回归未完成：当前环境未配置 `SMARTTEST_TEST_DB_*` 专用测试数据库，28 项 fixture 错误，未访问生产数据库。
- 产品边界扫描发现现存 `AI/debug/tools/testing/android_client/ui` 退役目录，未在本次范围内修改；检查器自身测试通过。
- Atlas 源码启动检查受当前 Qt 环境阻塞：进程 exit -6，提示 `Incompatible processor ... neon`；不据此宣称 GUI 启动成功。
- Windows 系统 PAC、公司 LDAP 和 Windows 源码启动待 Coco 实机验证；未构建安装包。
