# SmartTest Web Backend

Web 应用持久数据统一存入 MySQL；Redis 只保存可以从当前有效版本重新计算的统计正文。前端接口及认证、刷新权限、调度规则不因存储切换改变。外部 Wi-Fi Database 仍由独立的 `ReadonlyDatabase` 读取，不能作为应用写库。

## 安装与启动

在仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r web/backend/requirements.txt
.\.venv\Scripts\python.exe web/scripts/dev.py
```

后端也可沿用 `python -m uvicorn smarttest_web.app:app --app-dir web/backend --host 127.0.0.1 --port 8000 --no-access-log`。既有懒 ASGI 入口调用 `create_configured_app()`；它读取统一配置、验证 MySQL Alembic 版本，并装配共享数据库和 Redis。启动不会自动建表或降级到其他存储。`GET /health` 的处理函数不查询业务数据，但应用初始化必须能连接版本正确的 MySQL。

当前 Windows 本机部署由当前用户的计划任务管理：`SmartTest.MySQL` 与 `SmartTest.RedisRuntime` 在用户登录时启动独立 MySQL 和现有 WSL Redis；按需任务 `SmartTest.WebRuntime` 隐藏运行上述同一 `web/scripts/dev.py` 入口。可通过 `Start-ScheduledTask -TaskName SmartTest.WebRuntime` 启动、任务计划程序停止；手动启动开发入口前先停止已运行的 Web 实例，保持单 Web 进程。这些用户任务不等同于开机未登录前的系统服务。

## 持久配置与凭据

`smarttest_web/config.py:storage_environment()` 复用 `core.config.jsonTool`，读取应用数据目录下的 `web/storage.json`，显式环境变量覆盖文件配置。Windows 示例只包含非敏感值：

```json
{
  "SMARTTEST_DB_HOST": "127.0.0.1",
  "SMARTTEST_DB_PORT": 13306,
  "SMARTTEST_DB_USER": "smarttest_web",
  "SMARTTEST_DB_NAME": "smarttest_web",
  "SMARTTEST_DB_CREDENTIAL_REF": "mysql",
  "SMARTTEST_REDIS_HOST": "127.0.0.1",
  "SMARTTEST_REDIS_PORT": 16379,
  "SMARTTEST_REDIS_DB": 0,
  "SMARTTEST_REDIS_CREDENTIAL_REF": "redis",
  "SMARTTEST_STATISTICS_CACHE_TTL_SECONDS": 300
}
```

密码通过已有 `core.credentials.windows.WindowsCredentialStore` 读取：prefix 为 `SmartTest/WebStorage/`，上述 refs 对应 `mysql` 和 `redis`。条目的 UserName 不推断数据库或 Redis ACL 用户；连接用户由配置明确指定。禁止把 PASSWORD 写进 JSON。Linux 使用外部 secret 注入 `SMARTTEST_DB_PASSWORD` / `SMARTTEST_REDIS_PASSWORD`，不使用 Windows credential refs。

`ApplicationDatabaseSettings` 默认连接池为 5、额外连接 0，连接/池等待各 5 秒；MySQL 连接显式使用严格 SQL mode、UTC 和 REPEATABLE READ。`RedisCacheSettings` 默认最大连接 10、连接/读写超时各 1 秒、无隐式重试。统计 TTL 由 `SMARTTEST_STATISTICS_CACHE_TTL_SECONDS` 控制，默认 300 秒；它只回收缓存，不启动远端刷新。身份包含账号范围、主/独立事实版本、规则指纹、查询维度和日期边界。未命中或 Redis 故障读取同一数据库版本，MySQL 失败明确报错。

`SMARTTEST_REPORTS_ROOT` 可指定报告目录；默认仍为应用数据目录的 `web/audit-email-reports`，与数据库连接无关。服务端 LDAP/Jira/Confluence 账户凭据 owner 不变：Windows 使用既有 WebSession Credential Manager 范围，Linux 使用 AES-GCM 写入 MySQL，主密钥由 `SMARTTEST_WEB_CREDENTIAL_KEY` 注入。会话只持有 token 哈希，滑动期限 90 天；登出不会删除账号已保存凭据。

外部只读 Wi-Fi 连接继续使用 `WIFI_DB_HOST/PORT/USER/PASSWORD/NAME/POOL_SIZE`，仅在对应查询首次使用时解析，不与 `SMARTTEST_DB_*` 合并。

## 手动登录与浏览数据（当前 Windows 本机）

以下 PowerShell 命令在仓库根目录执行，复用 Windows Credential Manager 的现有凭据，不显示密码。MySQL 正式库为 `smarttest_web`，端口 `13306`；Redis 使用 WSL Ubuntu 的端口 `16379`、DB `0`。请勿连接系统原有的 `3306` 实例。

进入 MySQL 官方交互客户端：

```powershell
$mysqlLogin = @'
import os, subprocess
from core.credentials.windows import WindowsCredentialStore
user, password = WindowsCredentialStore(target_prefix='SmartTest/WebStorage/').read('mysql')
env = dict(os.environ, MYSQL_PWD=password)
raise SystemExit(subprocess.call([
    'C:/Program Files/MySQL/MySQL Server 8.0/bin/mysql.exe', '--no-defaults',
    '--host=127.0.0.1', '--port=13306', '--user=smarttest_web',
    '--default-character-set=utf8mb4', 'smarttest_web'
], env=env))
'@
.\.venv\Scripts\python.exe -c $mysqlLogin
```

进入后可执行只读查询，使用 `exit` 退出：

```sql
SELECT DATABASE(), @@port;
SHOW TABLES;
SELECT COUNT(*) FROM jira_issues;
SELECT COUNT(*) FROM confluence_projects;
SELECT * FROM jira_analytics_queries LIMIT 5;
```

进入 Redis 官方交互客户端（凭据只传入子进程环境）：

```powershell
$redisLogin = @'
import os, subprocess
from core.credentials.windows import WindowsCredentialStore
user, password = WindowsCredentialStore(target_prefix='SmartTest/WebStorage/').read('redis')
env = dict(os.environ, REDISCLI_AUTH=password)
env['WSLENV'] = ':'.join(filter(None, [env.get('WSLENV', ''), 'REDISCLI_AUTH']))
raise SystemExit(subprocess.call([
    'wsl.exe', '-d', 'Ubuntu', '--', 'redis-cli',
    '-h', '127.0.0.1', '-p', '16379', '--user', 'default', '-n', '0'
], env=env))
'@
.\.venv\Scripts\python.exe -c $redisLogin
```

进入后执行 `PING`、`INFO keyspace`、`DBSIZE` 或 `SCAN 0 COUNT 20`；用返回的游标继续 SCAN，直到游标为 `0`。对实际返回的 key 执行 `TTL "<key>"`、`GET "<key>"` 查看剩余秒数和缓存正文，使用 `quit` 退出。Redis 只保存可重建缓存，未命中时由后端读取 MySQL；Redis 为空不代表历史数据丢失。当前缓存 TTL 为 300 秒，关闭 AOF/RDB 持久化。

## Schema、导入与备份

Alembic 是唯一 schema 升级 owner。应用账号仅需 SELECT/INSERT/UPDATE/DELETE，DDL 使用单独部署身份显式执行：

```powershell
.\.venv\Scripts\python.exe -m alembic -c web/backend/alembic.ini upgrade head
.\.venv\Scripts\python.exe -m alembic -c web/backend/alembic.ini check
```

部署身份通过进程内安全配置覆盖数据库凭据，不将管理员密码写入仓库或命令行。一次性 `web/scripts/migrate_storage.py` 仅接受指定、SHA256 匹配的离线备份；显式目标仅允许正式 `smarttest_web` 和隔离准备库，拒绝非空目标。它先检查覆盖、长度/NULL/关联，再按外键顺序分批导入，保留原标识和插入顺序，验证所有表的行数/内容摘要、指针及选定 owner 输出。7 张已停用旧表保留在独立完整备份中并记录摘要，不静默丢弃；数据库运行期不包含备份格式读取器。

MySQL 备份/恢复直接使用官方 `mysqldump` / `mysql`，不要经 PowerShell 文本管道转码。两个命令均明确 `--default-character-set=utf8mb4`，避免 Windows 客户端默认 GBK 破坏 UTF-8 SQL。凭据可使用受限权限、用后清理的 `--defaults-extra-file`（该参数放在首位），不写密码参数：

```text
mysqldump.exe --defaults-extra-file=<受限配置> --single-transaction --hex-blob --default-character-set=utf8mb4 --no-tablespaces smarttest_web --result-file=<仓库外备份.sql>
mysql.exe --defaults-extra-file=<受限配置> --default-character-set=utf8mb4 --database=<独立恢复校验库>
```

在第二个官方客户端内使用 `source C:/.../backup.sql`。恢复校验只操作独立校验库，不覆盖正式库；核对完整摘要、关系与版本后保留备份和 manifest。

## 测试

完整后端业务测试必须使用独立 MySQL `smarttest_preparation_test`，缺配置明确失败，不会大面积跳过或使用正式库。`conftest.py:application_database` 在执行任何清理前验证 schema 名称；测试身份必须只获隔离库权限，不能使用正式 `smarttest_web` 凭据。

由部署环境安全注入 `SMARTTEST_TEST_DB_HOST/PORT/USER/PASSWORD/NAME`，其中 NAME 必须为 `smarttest_preparation_test`；需要真实 Redis 集成时注入 `SMARTTEST_TEST_REDIS_HOST/PORT/DB/USER/PASSWORD`。不要把测试密码写入仓库。先由有 DDL 权限的测试身份准备该库，再从仓库根目录执行：

```powershell
$env:PYTHONPATH = "D:/SmartTest;D:/SmartTest/web/backend"
.\.venv\Scripts\python.exe -m pytest web/backend/tests core/devtools/ci/test_check_product_boundaries.py -q
.\.venv\Scripts\python.exe core/devtools/ci/check_product_boundaries.py
```

`database_factory` 每次返回真实 MySQL Engine，测试结束只清理已知隔离表；导入器的合成源格式独立于应用运行依赖。纯函数单测不要求连接服务；业务套件不允许将缺服务当作通过。

## 既有调度

`daily_refresh.py` 的 `DAILY_REFRESH_HOUR=5`、`REFRESH_TIMEZONE=Asia/Shanghai` 及 `REFRESH_ITEMS` 继续控制现有每日刷新范围和手动权限。存储配置不新增调度开关。周审查任务 `SmartTest.WeeklyAuditEmail` 仍由既有 `weekly_audit.py --install` 安装，代码定义为北京时间每周五 18:00，复用已保存账号凭据和数据库 occurrence 唯一领取，任务进程也读取同一持久存储配置。
