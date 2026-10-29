# Project Version

- 2026-08-20 · v0.1.0：创建 Codex 本地会话清理工具，支持父子级联、孤儿检测、校验备份、索引清理和恢复；首次创建，无修改前备份。
- 2026-08-20 · v0.2.0：扩展为完整智能体会话管理器，新增全量详情、搜索筛选、重命名/归档、手动备份、可配置删除备份（默认关闭）及运行中删除提示；备份：backup_before_expand_session_manager_20260820_192418/；回退：恢复备份文件。
- 2026-08-20 · v0.3.0：彻底重写中英文使用文档，新增免安装 Python 的 Windows 单文件 EXE、本地构建脚本和 GitHub Release 自动构建流程，启动脚本优先运行 EXE；备份：backup_before_windows_exe_and_docs_20260820_200104/；回退：恢复备份文件。
- 2026-08-20 · v0.4.0：新增按工作目录识别的项目分组、项目筛选与项目详情，区分临时工作区，并统一 GUI、Codex 客户端和 EXE 版本；备份：backup_before_project_grouping_20260820_201913/；回退：恢复备份文件。
- 2026-08-20 · v0.5.0：基于 Codex 多存储审计重构扫描与删除，新增真实项目映射、最新重命名标题、本地幽灵会话修复、跨数据库一致性删除、全选/项目选择、可配置 DELETE 确认和独立日志库空间释放，并强化备份与删除后验证；备份：backup_before_multistore_cleanup_v050_20260820_210251/；回退：恢复备份文件。
- 2026-08-20 · v0.6.0：重构备份与恢复语义，修复包含日志库的完整失败回滚和备份路径边界校验，新增备份管理与安全删除、完整快照恢复预览、选中会话归档包及逐存储删除影响报告；备份：backup_before_backup_safety_manager_v060_20260820_214618/；回退：恢复备份文件。
- 2026-08-23：改为滚动日期构建；新增对话独占附件、可视化和生成图片的事务化清除、共享与备份残留提示、SQLite 安全压缩及项目目录强制保留；备份：backup_before_private_data_purge_20260823_000000/；回退：恢复备份文件。
- 2026-08-23：支持同一对话的多 rollout 连续分段，新增历史孤立附件与线程产物的只读扫描、事务化清理和精确残留报告；备份：backup_before_rollout_segments_and_orphan_cleanup_20260823_202530/；回退：恢复备份文件。

- 2026-10-12：修复全局会话引用漏清及复核、已确认侧边栏残留清理、历史残留引用补清、确认词错误提示和删除结果展示。尚未提交；修改前源码备份：backups/backup_before_cleanup_feedback_20261012_155413/。回退：仅恢复该目录覆盖的文件并删除新增 cleanup_dialogs.py、tests/test_cleanup_feedback.py；保留其他既有修改。

- 2026-10-23：修复空标题分页扫描、附件跨存储共享保护、新版数据库及界面状态引用、预览内容变化检测、DSH 可选字段验证；新增六项缺陷及归档相关回归。尚未提交；修改前文件：backups/backup_before_six_fixes_20261023/。回退：仅恢复本任务实际修改的对应备份文件，删除新增 tests/test_review_regressions.py；项目规则文档独立保留，其他既有修改不在回退范围内。验证与具体文件见 reports/review-20261023/fixes.md。

- 2026-10-29：产品更名为 Agent Session Manager／智能体会话管理器，仓库名 agent-session-manager：Python 包改为 agent_session_manager，Windows 单文件程序改为 AgentSessionManager.exe，CLI 为 agent-session-manager，配置目录改为 AgentSessionManager；配置拆分为常规／Codex／DSH 三段并升级到 schema_version 2，旧版扁平 settings.json 自动迁移；操作日志、备份工具标记和旧配置目录保留双读兼容。测试侧修复 Windows 下 SQLite 句柄泄漏与默认编码假设，并修正清理事务回滚的索引错误。本条目随更名提交一并入库（更名前基线 HEAD 72ff756），验证为全量 149 项通过（跳过 19 项）。回退：按本条目涉及的包名、文件名和配置键恢复旧形态即可回到 72ff756 的行为，数据兼容标识不随回退改变。

- 2026-10-29：DSH 压缩会话读取不再依赖系统库搜索是否走运。libzstd 定位改为有序候选：显式环境变量 `AGENT_SESSION_MANAGER_ZSTD`／`DSH_ZSTD_LIBRARY`／`ZSTD_LIBRARY` → 系统库搜索 → PATH 项及其父目录下的 `mingw64/bin`、`usr/bin` → 常见 Git／conda 安装位置；逐个尝试加载，全失败才报一条安装提示并列出前三条失败原因。本机命中 `D:\Git\mingw64\bin\libzstd.dll`（1.5.7），DSH 扫描从 12 条阻断恢复为 12 个会话、0 问题。同时按官方 master 复核 DSH_FORMAT.md：补压缩开关与「官方不提供删除 API、日志只增不减」的定位依据、`agent/inbox/spliced` 不计入消息面、投影缓存无淘汰策略、Windows 命名内核信号量语义，并把 zstd 查找顺序写进文档。验证：全量 151 项通过（跳过 17 项，均为仅 Linux 的删除／回滚契约与本机不支持符号链接）。回退：恢复 DSH_FORMAT.md、README.md、README.en.md、applications/dsh_format.py、tests/test_dsh_application.py 到 92e94ad 即可回到旧行为。

- 2026-10-29：命令行读取与清理计划失败不再抛裸 traceback。`main()` 改为 `_dispatch()` 加一层 `ValueError` 边界，统一打印单行原因并返回 2；`show --messages` 与 `plan/delete` 另有就地提示「Cannot read session …」「Cannot plan cleanup: …」，不再让截断或损坏的 Zstd 会话把栈冒到用户面前。界面原本就在异步线程里转错误对话框，行为不变。同时修正 DSH_FORMAT.md 里「旧 Python 包名与 CLI 入口保留兼容」的过期说法：更名提交后旧包与旧入口已移除，保留的只有数据层兼容。验证：新增 `test_cli_reports_unreadable_session_instead_of_traceback`，全量 152 项通过（跳过 17 项）；真机用截断副本复核 show 与 plan 均返回 2 并只输出一行原因。回退：恢复 cli.py、tests/test_dsh_application.py、DSH_FORMAT.md、PROJECT_VERSION.md 到 0a6cdf2。

- 2026-10-29：移植并加固清理事务的崩溃恢复。删除与恢复都会在所选 Codex 目录内写只增不改的事务快照，并在每个阶段原子更新 `operation.json` 检查点（prepared／deleting／core_deleted／deleting_logs／verified／complete，以及回滚后的 rolled_back）；进程被强制结束后，残留的 `.cleaner-transaction_`／`.cleaner-restore_` 会让扫描报 `interrupted_operation` 阻止性问题，规划与删除在显式恢复前全部拒绝执行。新增 `recover` 子命令与界面「事务恢复」按钮：不带目录时只列出待恢复事务，带目录时按检查点基线恢复，当前文件与基线不一致会拒绝，需 `--overwrite-conflicts`；缺 `operation.json` 的事务不自动处理。删除后的最终校验除原有引用查询外，还会重扫 `sessions`／`archived_sessions` 下的 rollout 身份、会话索引与本地输入历史、两份全局状态，以及未声明列中的引用；恢复前会先比对当前文件与快照记录的清理后基线，缺少基线记录的旧快照只在目标不存在或哈希一致时写入。配套加固：备份清单写入改为原子替换，备份与恢复后逐文件 fsync（Windows 上 fsync 为尽力而为，只读副本无法以写模式重开），恢复时复验哈希，`verify_backup` 校验清单路径落在受支持范围内并拒绝重复条目。验证：全量 170 项通过（跳过 17 项），含新增 `tests/test_recovery.py`（12 项，含子进程 `os._exit` 强制退出后恢复、恢复失败回滚、基线冲突拒绝）与 `tests/test_cleanup_safety.py`；界面按钮状态与三种交互路径经烟测复核。回退：删除 recovery.py、cleanup_safety.py、tests/test_recovery.py、tests/test_cleanup_safety.py，并把 backup.py、executor.py、scanner.py、cli.py、gui.py、README.md、README.en.md 恢复到 10027d6。
