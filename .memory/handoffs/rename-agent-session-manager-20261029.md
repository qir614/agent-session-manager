---
task_id: rename-agent-session-manager-20261029
owner: dsh:lead
recorded_at: '2026-10-29T10:20:00+08:00'
workspace: E:\\projects\\agent_session_manager
git_sha: 11d13f99edee2dc30deb785bad26ed7d8f3ae2eb
baseline_sha: 72ff75647069878cb293a002fbe41971271bd3a1
state_at_checkpoint: committed
---

Snapshot at the recorded time; project_status is authoritative for current status.

# Rename to Agent Session Manager / agent-session-manager

用户授权（m00074）：产品名 Agent Session Manager（智能体会话管理器），全量技术名 agent-session-manager，彻底重构；Linux/Windows/DSH/Codex 配置需要分离；文件名要改。禁止无授权提交/推送；不得删除真实用户数据。（后续用户已明确授权本地提交，见文末「提交记录」。）

## 已完成（下列为记录当时的工作树状态；这些内容已随本文件末尾的提交记录入库）

- 包目录 src/codex_session_cleaner -> src/agent_session_manager；全仓 47 个文件文本改名（源码、测试、pyproject、run_*.py、windows/、linux/、.github/、.agents/、README/文档）。
- pyproject: name agent-session-manager, version 2026.10.29, scripts agent-session-manager/agent-session-manager-gui；__init__.__version__ 2026.10.29。
- GUI 标题常量 WINDOW_TITLE = "智能体会话管理器 · Agent Session Manager"（gui.py），windows/build.ps1 smoke 标题已匹配（修复旧三处标题不一致的发布隐患）。
- 数据兼容：audit.py 新 journal .agent-session-manager-operations.jsonl + 旧 .codex-local-session-cleaner-operations.jsonl 只读兼容；backup.py BACKUP_TOOL=agent-session-manager 接受旧 marker；platforms 配置目录 AgentSessionManager + 旧 CodexLocalSessionCleaner 迁移路径。
- version_info.txt 名称已改，记录当时版本号仍 2026.8.23（后续已升到 2026.10.29，见 windows/packaging/version_info.txt）。

## 并行任务（任务板，记录当时状态；后均未产出、由 Lead 直接完成）

- task-1 docs-build（进行中）：docs/build/CI/version_info/数据兼容说明。
- task-2 config-separation（进行中）：configuration 包，general/codex/dsh 分离，GUI 设置分区，legacy 设置迁移，测试更新。
- task-3 test-stabilization（进行中）：Windows 30 个 teardown PermissionError [WinError 32] + 1 个编码失败（tests/test_review_regressions.py:168）。
- Lead 保留：最终全量测试、GUI/CLI smoke、构建静态校验、最终汇报。

## 验证现状

- 基线（Windows，bundled Python 3.12）：Ran 139, failures=1, errors=30, skipped=19（错误均为 tmp 清理 file in use；1 个真实失败为编码/乱码断言）。
- 改名后 compileall 通过；run_cli.py --version 输出 rolling build 2026.10.29；--help prog=agent-session-manager。
- 无 WSL，Linux 运行时无法本机验证；PyInstaller 未安装，EXE 构建只能静态检查。
- 工具限制：DSH write/edit 当前 EISDIR 失败，用 run_code Node fs 或 pwsh Set-Content 写文件。

## 下一步

- 等待三个队友完成并核对结果；复审 src 中遗留旧名/兼容分支；version_info、README 链接、SECURITY.md 残留由 docs-build 处理。
- 全量 unittest（同一命令）应无失败无错误后，运行 GUI smoke（标题匹配）与 CLI scan 只读烟测；检查 git status 只含本任务变更，不提交。


## 收尾更新（2026-10-29 晚）

- 三个队友未能产出可交付结果（后台子代理反复无收尾失败），Lead 直接完成全部剩余工作；共享任务 task-1/2/3 已标记 completed。
- 测试稳定化根因：sqlite3 "with connection" 只提交不关闭 → Windows 句柄泄漏；tests/helpers.py 新增 open_sqlite() 上下文管理器，29 处替换；生产代码 history_store.py:18 改用 contextlib.closing。测试默认编码 cp936 → 22 处 read_text、34 处 write_text 显式 encoding="utf-8"；exFAT 卷拒绝 1980 前 mtime → os.utime 改 1_700_000_000。另修复 orphan_cleaner.py 回滚循环 IndexError（改用 zip(operation_log_backups, operation_paths)）。
- 配置分离：新增 src/agent_session_manager/configuration/{general,codex,dsh,store}.py，settings.json schema_version=2（general/applications.codex/applications.dsh），旧扁平文件自动迁移并立即落盘；settings.py 变兼容垫片。CLI 新增 config [--json]；--version 输出 "agent-session-manager 2026.10.29"。GUI 设置对话框分 常规/Codex/DSH 三页。新增 tests/test_configuration.py（10 项）。
- 命名收尾：CLI scan/orphans JSON 顶层键 codex_home -> home（Python 内部字段未改，属后续重构）；build.ps1 冒烟标题改为 ASCII 通配 "*Agent Session Manager*"（原中文字面量在 PowerShell 5.1 无 BOM 时会乱码）；.gitignore 忽略 .agents/state/ 与 .agent-tmp/；.agents/collaboration.yaml root 改为 E:/projects/codex-local-session-cleaner（收尾时已随目录改名更新为 E:/projects/agent_session_manager）；PROJECT_VERSION.md 追加 2026-10-29 条目。
- 验证：compileall 通过；全量 unittest Ran 149 tests, OK (skipped=19)；GUI smoke 通过（标题=常量、设置对话框页签 常规/Codex/DSH、settings 路径 %LOCALAPPDATA%\AgentSessionManager\settings.json）；CLI smoke 通过（--version/--help/config --json/--home scan/--app dsh scan/orphans scan/backups list）；windows/build.ps1 经 PowerShell 5.1 与 pwsh 解析均无语法错误。
- 未做/需授权：push 未执行（本机当前无法连接 github.com，且推送本需另行授权）；GitHub 仓库改名与 origin URL 仍为 qir614/codex-local-session-cleaner（需用户决定后操作）；无 WSL 且未安装 PyInstaller，EXE 构建与 Linux 运行时未实测；reports/ 历史报告保留旧名，且因含真实会话标识已加入 .gitignore 不入库；.agents/state/project-onboarding.json 的 root 为历史 Linux 路径（历史记录，未改写）。

## 提交记录（2026-10-29 11:25–11:26 +08:00）

- 用户已授权本地提交（旧会话 seq 1792："该提交git就要提交git，不用问我"）。
- 提交 11d13f99edee2dc30deb785bad26ed7d8f3ae2eb "Rename project to agent-session-manager"：95 files changed, 6300 insertions(+), 919 deletions(-)，覆盖包改名、配置分离、platforms/applications 拆分、CI 与构建脚本路径迁移、测试稳定化；基线 72ff756。
- 提交前最终验收：全量 unittest Ran 149 tests, OK (skipped=19)，exit 0；提交后又从未提交工作树导出的 HEAD 树（95 文件）重跑同一命令，同样 Ran 149 tests, OK (skipped=19)，确认已入库内容自洽；compileall exit 0；CLI --version/--help/config --json 与 GUI 标题烟测通过。
- reports/ 含真实会话标识，加入 .gitignore 并保留在磁盘作为本地证据，不入库。
- origin 仍为旧仓库 URL，push 未执行（无外网，且推送需另行授权）。