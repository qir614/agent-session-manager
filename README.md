# Codex 本地会话管理器

面向 Windows 的 Codex 本地会话查看、归档、备份与清理工具。v0.6.0 在多存储扫描基础上新增完整日志回滚、备份路径边界保护、备份管理中心和逐存储删除影响预览。

[English](README.en.md)

> 本项目不是 OpenAI 官方产品。Codex 本地结构可能随版本变化；数据库损坏、未知引用或删除后仍有核心引用时，工具会停止并报告具体存储，不会把部分成功显示成“删除完成”。

## 直接使用 EXE

普通用户不需要安装 Python：

1. 在 GitHub 仓库右侧打开 **Releases**。
2. 下载 `CodexLocalSessionManager.exe`，可同时下载 `.sha256` 校验文件。
3. 双击 EXE。默认扫描 `%USERPROFILE%\.codex`。
4. Windows SmartScreen 若提示“未知发布者”，请确认文件来自本仓库并核对 SHA-256。

仓库源码中也可以双击 `start_windows.bat`。它依次尝试根目录 EXE、`dist` 中的 EXE、`py -3` 和 `python`。

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
- 选择父对话后，删除计划自动包含所有普通子智能体和 guardian；附属子智能体不能脱离父对话单独删除。

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

## 删除对话

查看、搜索和创建会话归档可以在 Codex 运行时使用。删除、恢复完整状态快照、删除备份和数据库维护只能在 Codex/ChatGPT 及后台 app-server 完全关闭后执行。

1. 选择一个或多个本地根记录，或直接选择项目行。
2. 点击 **删除所选**。
3. 查看主对话、子智能体、幽灵记录、rollout、逐库引用、关联日志和事务快照空间。
4. 输入确认文字。默认是 `DELETE`；设置中可改成任意文字，也可使用 `DELETE-{count}` 让 `{count}` 自动替换为删除数量。

删除默认不保留持久备份。设置中开启“删除前保留持久备份”后才会留下完整状态快照。无论是否开启，执行期间都会创建包含本次全部可变存储、包括日志库在内的临时事务快照；成功后销毁，任一阶段失败时恢复 rollout、状态、索引和日志。空间不足时会在第一次写入前停止。

一次完整删除会处理：

- `sessions` / `archived_sessions` rollout；
- 根 `state_5.sqlite` 和旧版 `sqlite/state_5.sqlite`；
- `session_index.jsonl` 的该 ID 全部名称历史；
- `.codex-global-state.json` 及 `.bak` 中的项目、侧边栏、输出目录、权限映射；
- `sqlite/codex-dev.db` 中 `host_id='local'` 的 catalog、timeline、inbox 和 automation run；
- `thread_history_1.sqlite` 的消息、轮次和投影；
- goals、queue、memories、history snapshot 和 summary；
- 两个 `logs_2.sqlite` 中父对话及全部子智能体的关联日志行。

工具随后重新查询所有核心表。只要任何目标 ID 仍然存在，就会判定失败并回滚，而不是显示“删除完成”。操作结果会写入 `.codex-local-session-cleaner-operations.jsonl`，只记录 ID、各库删除行数和错误类型，不记录消息正文。

项目源码、真实工作目录、普通输出目录和无法可靠归属线程的粘贴附件不会自动删除。`visualizations/<线程ID>` 与 `generated_images/<线程ID>` 可能包含有价值成果，v0.6.0 也不把它们偷偷并入默认删除。

## 释放日志数据库空闲空间

删除日志行不会立即缩小 SQLite 文件。主界面提供独立按钮 **释放数据库空闲空间**：

1. 完全关闭 Codex。
2. 点击按钮，查看当前大小和预计可释放空间。
3. 确认后，工具使用 `VACUUM INTO` 生成紧凑副本。
4. 对比压缩前后日志行数并执行 `quick_check`。
5. 原子替换成功后删除临时回滚文件。

该功能不删除有效日志，只回收已经空闲的页面。例如一个约 1 GB、其中约 91% 页面已空闲的数据库，通常可压缩到约 90 MB。它不会在每次删除后自动运行。

## 会话归档、完整快照与备份管理

**归档所选**保存所选父节点及全部后代的 rollout 和清单，不复制整套状态数据库，不会回退其他会话。归档包用于长期保存和导出，不能作为完整状态快照覆盖 `.codex`。

设置中开启“删除前保留持久备份”后，删除前会生成可整体恢复的完整状态快照。恢复会覆盖快照包含的状态数据库，因此界面要求输入 `RESTORE`，并在恢复前创建临时安全快照；恢复失败会回到恢复前状态。

顶部 **备份管理** 可以列出、校验、恢复或删除受管理备份，显示类型、状态、会话数、文件数和空间。支持多选删除、不完整备份清理以及数量、天数、最大空间保留规则。删除严格限制在配置备份根目录的直属受管理目录内，不跟随符号链接或 junction，也不会处理项目源码的 `backup_before_*` 目录。

所有清单路径拒绝绝对路径、盘符、UNC 和 `..`，哈希或 SQLite 完整性失败时禁止恢复。恢复目标必须由用户明确指定或使用当前 `%USERPROFILE%\.codex`，不信任清单中记录的旧路径。

命令行恢复前关闭 Codex 并显式传入 `--force`：

```powershell
python run_cli.py restore D:\codex-backups\backup_before_session_cleanup_20260820_120000 --force
```

## 从源码运行

需要带 Tcl/Tk 的 Python 3.10 或更高版本：

```powershell
cd E:\projects\codex-local-session-cleaner
python run_gui.py
```

若出现 `Can't find a usable init.tcl`，请使用 python.org 的完整 Windows Python，或直接使用 Release EXE。

## CLI

```powershell
python run_cli.py scan
python run_cli.py scan --json
python run_cli.py show <SESSION_ID> --messages
python run_cli.py plan <SESSION_ID>
python run_cli.py backup <SESSION_ID> --destination D:\codex-backups
python run_cli.py backups list --root D:\codex-backups
python run_cli.py backups verify D:\codex-backups\backup_before_session_cleanup_...
python run_cli.py backups delete D:\codex-backups\backup_codex_sessions_... --root D:\codex-backups
python run_cli.py delete <SESSION_ID>
python run_cli.py delete <SESSION_ID> --apply --confirm DELETE-3
python run_cli.py compact-logs
python run_cli.py compact-logs --apply
```

CLI 的 `backup` 创建只含选中父子树的会话归档包。`delete` 默认只预览，并输出数据库引用、日志行数和事务快照空间；真正删除仍使用依赖闭包数量形式 `DELETE-N`。图形界面的确认文字由设置控制。

## 构建 EXE

```powershell
.\build_windows.ps1 -Clean
```

输出为 `dist\CodexLocalSessionManager.exe` 和对应 SHA-256。构建目录、缓存和生成产物不属于源码备份范围。

## 测试与安全边界

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
python run_cli.py --help
```

测试只使用临时构造的数据或真实数据库的临时一致性副本，不删除真实会话。工具不联网、不上传会话，也不会读取或输出 `auth.json` 中的凭据内容。

## 许可证

[MIT](LICENSE)
