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

