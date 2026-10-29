# 智能体会话管理器

Agent Session Manager（智能体会话管理器）是支持 Codex 与 DSH 的本地会话查看、导出、备份与清理工具。Codex 支持 Windows/Linux；DSH 当前支持 v4 的读取、导出和 Linux 清理。项目采用滚动发布，不再使用 `v0.x.y` 展示版本号；程序会清除能够可靠归属于目标对话的本地私有产物，同时始终保留真实项目目录。

[English](README.en.md)

> 本项目不是 OpenAI 官方产品。Codex 本地结构可能随版本变化；数据库损坏、未知引用或删除后仍有核心引用时，工具会停止并报告具体存储，不会把部分成功显示成“删除完成”。

## 按系统启动

| 系统 | 专属目录 | 启动入口 | 使用说明 |
| --- | --- | --- | --- |
| Linux | `linux/` | 激活现有 `codex-cleaner` 环境后运行 `bash linux/start.sh` | [Linux 说明](linux/README.md) |
| Windows | `windows/` | 双击 `windows/start.bat` | [Windows 说明](windows/README.md) |

根目录的 `run_gui.py`、`run_cli.py` 是共享 Python 入口；平台脚本只负责启动，扫描、清理、备份逻辑只有一套。

Windows 上的 `windows/start.bat` 会自动探测带 Tcl/Tk 的 Python（跳过 Microsoft Store 的 `WindowsApps` 存根），失败时可用 `set ASM_PYTHON=<python.exe 完整路径>` 指定解释器。

## 项目结构

```text
linux/                            Linux 启动、CLI、测试脚本与说明
windows/                          Windows 启动、EXE 构建与版本资源
src/agent_session_manager/
  applications/                   Codex 与 DSH 的格式适配和事务
  configuration/                  分区设置、旧版迁移（字段以该模块为准）
  platforms/
    linux.py                      Linux 配置路径、ps、xdg-open、字体与主题
    windows.py                    Windows 配置路径、进程 API、Explorer、字体与主题
    __init__.py                   唯一的平台选择入口
  gui.py                          共享界面和自动字体缩放
  scanner.py / planner.py          共享扫描与删除计划
  executor.py / orphan_cleaner.py  共享清理与回滚
  backup.py / state_store.py       共享备份和数据存储
  audit.py                         无正文操作日志（兼容旧文件名）
tests/linux/                     Linux 专属测试
tests/windows/                   Windows 专属测试
tests/test_*.py                   共享逻辑测试```

共享业务代码通过 `platforms.get_platform()` 调用系统接口，不直接执行系统专属命令。备份清单中的路径校验属于共享文件格式规则。

## 界面如何使用

程序启动后只读扫描，不会自动删除内容。顶部可以切换 `.codex` 目录、重新扫描、打开设置，以及查看 Codex 是否仍在运行。

左侧树按 Codex 自己保存的项目组织对话。项目名和顺序优先读取 `.codex-global-state.json` 中的 `local-projects`、`thread-project-assignments` 和 `sidebar-project-thread-orders`；旧版本数据没有这些字段时才回退到 rollout 的 `cwd`。

标题优先使用显式改名，顺序为：

```text
state.name
→ session_index.jsonl 中该 ID 最后一条 thread_name
→ local_thread_catalog.display_title
→ state.title
→ rollout 第一条用户消息
```

因此在 Codex 中改过的名称不会再被旧的首条消息覆盖。

### 选择多条对话

- **选择全部结果**：选择当前搜索、类型和项目筛选后可删除的全部本地记录，不包含云端只读目录。
- **反选**：反转当前可见本地记录的选择。
- **清除选择**：取消选择。
- 直接选择项目文件夹行时，会自动转换为该项目下的所有主对话。
- 选择父对话后，删除计划自动包含所有普通子智能体和 guardian；附属子智能体不能脱离父对话单独删除。由智能体创建、但没有父线程的独立任务（`agent_created_thread`）按顶层对话处理，使用相同的项目归属记录。

### 记录类型

| 类型 | 含义 | 是否可删除 |
| --- | --- | --- |
| 用户对话 | 有本地 rollout 的顶层对话 | 可以，自动包含全部后代 |
| 附属子智能体 | 带有明确 `parent_thread_id` 的子线程 | 必须随父对话删除 |
| 孤儿子智能体 | 父记录已不存在的子线程树 | 可以，自动包含更深后代 |
| 本地幽灵记录 | 本地 rollout/state 已不存在，但侧边栏 catalog 或项目顺序仍引用该 ID | 可以，执行索引修复式删除 |
| 云端目录（只读） | `host_kind=chatgpt`，本机没有 rollout | 只能查看目录信息；本地删缓存不等于删除服务器对话 |
| 未知 | 元数据不足、关系冲突或结构未知 | 拒绝删除 |

右侧“对话内容”按需读取 rollout；本地幽灵会显示残留说明，云端目录会明确显示为只读。系统、开发者和注入消息默认隐藏，可在设置中开启。

同一对话可能由多个连续 rollout 文件组成。程序按对话 ID、项目路径、来源和父子元数据合并兼容分段，阅读、归档、附件归属和删除均覆盖全部分段；只有关键元数据冲突时才阻止写入，并显示冲突字段及全部文件路径。

## 删除对话

查看、搜索和创建会话归档可以在 Codex 运行时使用。删除、恢复完整状态快照、恢复未完成事务、删除备份和数据库维护只能在 Codex/ChatGPT 及后台 app-server 完全关闭后执行。

1. 选择一个或多个本地根记录，或直接选择项目行。
2. 点击 **删除对话及私有数据**。
3. 查看主对话、子智能体、rollout、逐库引用、独占/共享附件、可视化、生成图片、备份残留、明确保留的项目路径和事务快照空间。
4. 输入确认文字。默认是 `DELETE`；设置中可改成任意文字，也可使用 `DELETE-{count}` 让 `{count}` 自动替换为删除数量。

删除默认不保留持久备份。设置中开启“删除前保留持久备份”后会留下能够恢复会话的完整状态快照，此时结果不会声称已完整清除。无论是否开启，执行期间都会创建包含本次全部可变存储、日志库和待删私有产物的临时事务快照；成功后销毁，任一阶段失败时恢复 rollout、状态、索引、日志、附件和生成产物。空间不足时会在第一次写入前停止。

一次完整删除会处理：

- `sessions` / `archived_sessions` rollout；
- 根 `state_5.sqlite` 和旧版 `sqlite/state_5.sqlite`；
- `session_index.jsonl` 的该 ID 全部名称历史；
- `history.jsonl` 中 `session_id` 精确匹配的本地输入历史（其他会话保留）；
- `.codex-global-state.json` 及 `.bak` 中的项目、侧边栏、输出目录、权限映射；
- `sqlite/codex-dev.db` 中 `host_id='local'` 的 catalog、timeline、inbox 和 automation run；
- `thread_history_1.sqlite` 的消息、轮次和投影；
- goals、queue、memories、history snapshot 和 summary；
- 两个 `logs_2.sqlite` 中父对话及全部子智能体的关联日志行。
- 只被删除闭包引用的 `attachments/<附件ID>`；被保留会话共同引用的附件会保留并报告；
- `visualizations/.../<线程ID>` 和 `generated_images/<线程ID>` 中能够由目录名精确归属的产物；
- 操作日志中直接包含目标线程 ID 的旧记录；新日志只保存不可逆短哈希；
- 修改过的 SQLite 使用 `secure_delete`、WAL checkpoint、`VACUUM` 和 `quick_check` 清除逻辑文件内的空闲页残留。

执行期间在所选 Codex 目录内维护事务快照和 `operation.json` 检查点；进程被强制结束或任一阶段失败时，残留事务会阻止新的规划和删除，直到显式恢复或回滚完成。

工具随后重新查询所有核心表和计划内私有产物，并重扫 `sessions` / `archived_sessions` 下的 rollout 身份、索引与本地输入历史、全局状态和未声明列中的引用。只要任何目标 ID、独占产物或未识别引用仍然存在，就会判定失败并回滚，而不是显示“删除完成”。操作结果会写入 `.agent-session-manager-operations.jsonl`，只记录线程 ID 的短哈希、各库删除行数和错误类型，不记录消息正文。旧版本使用的 `.codex-local-session-cleaner-operations.jsonl` 仍会被读取、随备份保存并参与旧记录清理，升级不会丢失已写入的删除凭证。

顶部 **历史残留清理** 用于处理以前删除对话后留下的孤立附件、可视化、生成图片和线程 UUID 产物。它先做只读扫描，只把超过 7 天、没有任何存活 rollout 或本地目录记录引用、且完全位于受管理 `.codex` 根目录内的项目列为可清理；近期项目、含符号链接或 junction 的目录、无法读取的目录和备份只报告。执行前必须输入 `CLEAN-N`，删除时在所选 Codex 目录的同一文件系统中重命名暂存，不复制附件，也不使用系统 `/tmp`。全部暂存和日志更新成功后直接删除暂存产物，不保留持久备份；跨文件系统的产物会使操作停止并回滚。普通异常或 Ctrl+C 会恢复已移动的原件；恢复失败时保留 `.cleaner-orphan-transaction-*` 目录及 `recovery.json` 清单，并阻止继续清理。强制结束进程或断电不能保证自动恢复，此时应保留该目录，按清单检查和恢复 `data/` 下的原件。操作日志中已失效的原始线程 ID 会脱敏。

项目源码、真实工作目录、Git 仓库、普通输出目录和上传文件原件永远不会随对话自动删除，即使项目完全由该对话生成。程序只删除 `.codex` 受管理根目录内、能够可靠证明只属于删除闭包的副本和产物。共享附件、包含目标会话的已有备份以及无法确认归属的文件会保留并在结果中明确报告；存在这些保留项时不会显示“完整清除”。云端对话和服务端附件也不在本地工具权限范围内。

## 释放日志数据库空闲空间

普通维护仍可使用主界面的 **释放数据库空闲空间**。对话删除已经自动对发生变化的 SQLite 执行安全删除、checkpoint 和压缩；独立维护用于回收历史操作留下的其他空闲页面：

1. 完全关闭 Codex。
2. 点击按钮，查看当前大小和预计可释放空间。
3. 确认后，工具使用 `VACUUM INTO` 生成紧凑副本。
4. 对比压缩前后日志行数并执行 `quick_check`。
5. 原子替换成功后删除临时回滚文件。

该功能不删除有效日志，只回收已经空闲的页面。例如一个约 1 GB、其中约 91% 页面已空闲的数据库，通常可压缩到约 90 MB。

## 会话归档、完整快照与备份管理

**导出所选**保存所选父节点及全部后代的 rollout 和清单，不复制整套状态数据库，不会回退其他会话。归档包用于长期保存和导出，不能作为完整状态快照覆盖 `.codex`。

设置中开启“删除前保留持久备份”后，删除前会生成可整体恢复的完整状态快照。恢复会覆盖快照包含的状态数据库，因此界面要求输入 `RESTORE`，并在恢复前创建临时安全快照；恢复失败会回到恢复前状态。

顶部 **备份管理** 可以列出、校验、恢复或删除受管理备份，显示类型、状态、会话数、文件数和空间。支持多选删除、不完整备份清理以及数量、天数、最大空间保留规则。删除严格限制在配置备份根目录的直属受管理目录内，不跟随符号链接或 junction，也不会处理项目源码的 `backup_before_*` 目录。

所有清单路径拒绝绝对路径、盘符、UNC 和 `..`，哈希或 SQLite 完整性失败时禁止恢复。恢复目标必须由用户明确指定或使用当前用户的 `.codex` 目录，不信任清单中记录的旧路径。

命令行恢复前关闭 Codex 并显式传入 `--force`：

```text
python run_cli.py restore BACKUP_DIRECTORY --force
```

恢复会先比对当前文件与快照记录的清理后基线：快照记录过基线时，当前文件与基线不一致会直接停止，需要额外确认才覆盖；旧版本生成、没有基线记录的快照只在目标文件不存在或哈希一致时写入。

### 中断恢复与恢复冲突

删除和恢复都在 Codex 目录内写一份只增不改的事务快照，并在每个阶段更新 `operation.json` 检查点。进程被强制结束时，残留的 `.cleaner-transaction_` 或 `.cleaner-restore-` 目录会让扫描报出阻止性问题，规划和删除在恢复前全部拒绝执行；界面顶部 **事务恢复** 与命令行 `recover` 都只恢复清单里记录过的文件：

```text
python run_cli.py recover
python run_cli.py recover PATH/TO/.cleaner-transaction_... --force
```

不带目录时列出发现的未完成事务。恢复前会重新校验快照哈希、清单路径和当前文件状态；当前文件与基线不同、或检查点标记为写入未完成时会停止，确认后再加 `--overwrite-conflicts`。缺少 `operation.json` 或完整清单的目录不会被自动处理，需要人工检查。该能力经过进程强制退出测试，但不承诺硬件故障或所有断电情形。

## 配置与数据兼容

设置文件按系统存放：Windows 为 `%LOCALAPPDATA%\AgentSessionManager\settings.json`，Linux 为 `$XDG_CONFIG_HOME/AgentSessionManager/settings.json`（未设置时使用 `~/.config/AgentSessionManager/settings.json`）。文件顶层包含 `schema_version`，设置分为 `general`、`codex`、`dsh` 三个分区：`general` 保存语言、主题、确认文字、内部消息显示和删除前备份与保留规则；`codex` 保存 Codex 可执行文件与可选的 Codex 数据目录；`dsh` 保存 DSH 数据目录与可选的独立会话根目录。具体字段名以 `src/agent_session_manager/configuration/` 中的配置模块为准，界面与 CLI 共用这份配置。

升级后会读取旧版扁平 `settings.json` 并迁移到新分区格式；新路径没有可读文件时，还会回退读取旧目录 `CodexLocalSessionCleaner/settings.json`。迁移在内存中进行，旧文件保留不删除，只有下次保存时才写成新格式；文件损坏或版本未知时回退默认设置，不阻止启动。

日志与备份保持兼容：新操作日志写入 `.agent-session-manager-operations.jsonl`，旧日志 `.codex-local-session-cleaner-operations.jsonl` 仍会被读取和维护；备份清单的工具标记改为 `agent-session-manager`，旧版本生成的 `codex-local-session-cleaner` 备份仍可校验、恢复和管理。升级不需要手工转换旧日志或旧备份，旧文件也不属于会话数据清理范围。

## 从源码运行

需要带 Tcl/Tk 的 Python 3.11 或更高版本。环境要求和启动方法按系统分别见 [Linux](linux/README.md) 与 [Windows](windows/README.md)。

## CLI

```text
python run_cli.py scan
python run_cli.py scan --json
python run_cli.py show <SESSION_ID> --messages
python run_cli.py plan <SESSION_ID>
python run_cli.py backup <SESSION_ID> --destination BACKUP_ROOT
python run_cli.py backups list --root BACKUP_ROOT
python run_cli.py backups verify BACKUP_DIRECTORY
python run_cli.py backups delete BACKUP_DIRECTORY --root BACKUP_ROOT
python run_cli.py orphans scan --grace-days 7
python run_cli.py orphans clean --grace-days 7 --apply --confirm CLEAN-N
python run_cli.py delete <SESSION_ID>
python run_cli.py delete <SESSION_ID> --apply --confirm DELETE-3
python run_cli.py compact-logs
python run_cli.py compact-logs --apply
python run_cli.py recover
python run_cli.py recover PATH/TO/.cleaner-transaction_... --force
```

CLI 的 `backup` 创建只含选中父子树的会话归档包。`delete` 默认只预览，并输出数据库引用、日志行数和事务快照空间；真正删除仍使用依赖闭包数量形式 `DELETE-N`。图形界面的确认文字由设置控制。

## 构建与测试

Windows EXE 构建只在 [windows/](windows/README.md) 中维护。Linux 当前直接从源码启动，不使用 EXE 构建流程。

Linux 本地回归测试使用现有 `codex-cleaner` 环境，执行 `bash linux/test.sh -q`；临时数据放在项目磁盘目录 `.review-tmp/`。平台专属测试分别位于 `tests/linux/` 和 `tests/windows/`，不适用的平台测试会跳过。

测试只使用临时构造的数据或真实数据库的临时一致性副本，不删除真实会话。工具不联网、不上传会话，也不会读取或输出 `auth.json` 中的凭据内容。

## 许可证

[MIT](LICENSE)

## 官方资料与本地实现边界

扫描、依赖判断、删除、验证和失败回滚由本项目独立实现，清理不启动 Codex CLI 或 app-server。官方文档用于核对语义与配置，并不提供稳定的私有数据库格式承诺。原有重命名、Codex 归档操作仍属于独立的可选命令集成，不参与清理。

`sqlite_home` 配置优先于 `CODEX_SQLITE_HOME`，默认使用 `CODEX_HOME`；`log_dir` 默认位于其 `log` 子目录。扫描按配置读取主状态库。外置数据库或日志目录尚未实现跨目录事务恢复，因此明确拒绝清理，避免改错库或部分删除。缺少可验证 rollout 的目录记录不能据此判为垃圾。分页历史仅在 rollout 存在且本地分页库表结构通过验证时允许本地清理，覆盖 turns、items、projection 和 realtime items；未知表结构、缺失库或保留的分叉依赖会阻止写入。导出会话副本与 Codex 的归档状态是不同操作。

清理已知本地引用不等于完整隐私擦除；共享附件、项目、云端、已有导出和未识别格式仍有各自生命周期。完整格式依据与限制见 [THREAD_FORMAT.md](THREAD_FORMAT.md)。

## DSH 与应用切换

界面左上角选择 `codex` 或 `dsh`。DSH 默认读取 `DSH_HOME` 或 `~/.dsh`，可指定数据目录及独立会话根目录。支持 v4 会话读取、项目分组、导出和 Linux 本地清理；保留共享附件和项目。官方资料、格式限制与回滚机制见 [DSH_FORMAT.md](DSH_FORMAT.md)。

```bash
python -B run_cli.py --app dsh scan
python -B run_cli.py --app dsh --home /path/to/.dsh plan session-ID
python -B run_cli.py --app dsh --home /path/to/.dsh --sessions-root /path/to/.dsh/custom gui
```

`delete` 默认只预览，按实际依赖数量用 `--apply --confirm DELETE-N` 执行。DSH 必须关闭。压缩读取需要 libzstd，无需运行 DSH/Node：先查 `AGENT_SESSION_MANAGER_ZSTD`（也认 `DSH_ZSTD_LIBRARY`、`ZSTD_LIBRARY`），再查系统库搜索，最后按 PATH 与常见 Git/conda 安装位置找 `libzstd.dll`/`libzstd.so`，找到即用；找不到才提示安装，裸 NDJSON 会话不受影响。旧格式只读，Windows 的 DSH 删除尚未启用。应用适配与 Linux/Windows 适配分别管理，原 Codex 命令继续可用。
