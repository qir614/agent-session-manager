---
task_id: windows-exe-build-and-docs-20261029
owner: dsh:lead
recorded_at: '2026-10-29T20:10:00+08:00'
workspace: E:\projects\agent_session_manager
baseline_sha: 092a17f
state_at_checkpoint: committed
---

Snapshot at the recorded time; project_status is authoritative for current status.

# Windows EXE 构建链路重写与文档同步

用户授权：旧会话 seq 1792「该提交git就要提交git，不用问我」、m01139「都由你来决定吧」，常规提交与推送无需再问；强制推送与改写历史仍需单独确认。

## 背景：旧脚本在本机整体不可用（四处硬伤）

1. `$ErrorActionPreference = "Stop"` 会把原生命令的 stderr 升级为终止错误。探测「解释器是否已装 PyInstaller」时 Python 把 ImportError 打到 stderr，构建在探测阶段就失败。
2. Tcl/Tk 路径只按官方 CPython 布局探测（`<base>\tcl\cl8.6`、`DLLs\cl86t.dll`）；本机 conda 环境把它们放在 `Library\lib\tcl8.6` 与 `Library\bin\tcl86t.dll`，六项探测全部 False，直接 throw。
3. PyInstaller 用相对路径调用，配合 `--specpath build` 后 `--version-file` 被解析成 `build\windows/packaging/version_info.txt`，报 FileNotFoundError；同时缺 `--hidden-import sqlite3`，且没有 `--add-binary`/`--add-data`。
4. GUI 烟测用 `Start-Process -WindowStyle Hidden`（后来也试过 Minimized）：本机这两种方式都拿不到 `MainWindowTitle`，健康的构建必然超时，报 "GUI smoke test did not find the expected main window"。

另有一个环境限制：本机无外网到 PyPI（`Invoke-WebRequest https://pypi.org/simple/pyinstaller/` 失败），venv + `pip install ".[build]"` 会挂死。因此构建必须复用已装 PyInstaller 的解释器。

## 新版 `windows/build.ps1`（471 行）

- `Invoke-Native` 包装所有原生命令：临时把 `$ErrorActionPreference` 降到 `Continue`，`& $Exe @Arguments 2>&1`，返回 `@{ Output; ExitCode }`。解释器探针、`import PyInstaller` 探测、venv 创建、pip 安装、`sys.base_prefix`、`pyi-archive_viewer` 全部走它。
- 解释器探测用 `import sys, tkinter; print(sys.executable); print('%d.%d' % sys.version_info[:2])` 真实验证，跳过 Microsoft Store 的 `WindowsApps` 占位存根（退出码 9009）。候选顺序：`-Python` → `ASM_PYTHON` → `py -3` → PATH `python` → `.build-venv` → 各盘符与 `%LOCALAPPDATA%\Programs\Python` 下的 conda/官方安装。**两轮遍历：先取第一个有效的保底，再优先取第一个已装 PyInstaller 的**，以支持离线构建。
- `Resolve-TclTkLayout` 同时支持两种布局：官方 CPython（`tcl\cl8.6`、`tcl\k8.6`、`DLLs\cl86t.dll`、`DLLs\k86t.dll`、`DLLs\sqlite3.dll`）与 conda（`Library\lib\tcl8.6`、`Library\lib\tk8.6`、`Library\bin\tcl86t.dll`、`Library\bin\tk86t.dll`、`Library\bin\sqlite3.dll`）。这样 CI 的 `actions/setup-python` 与本机 conda 都能用同一份脚本。
- 把 `TMP`/`TEMP` 指到项目内 `.review-tmp`（venv/ensurepip 在系统临时目录不可写时会失败）；设置 `TCL_LIBRARY`/`TK_LIBRARY`，把 `tcl86t.dll` 所在目录、`Library\bin`、`DLLs` 前置进 `PATH`，让 PyInstaller 能解析 `ffi.dll`、`liblzma.dll`、`LIBBZ2.dll` 等间接依赖。
- PyInstaller 全部使用绝对路径（`--specpath`/`--workpath`/`--distpath`/`--paths`/`--version-file`/入口脚本），hidden-import 补 `sqlite3 lzma bz2 tkinter _tkinter agent_session_manager.platforms.windows`，`--add-binary` 带 `_tkinter.pyd`、`tcl86t.dll`、`tk86t.dll`（有则加 `sqlite3.dll`），`--add-data` 带 tkinter 包、`tcl8.6`→`_tcl_data`、`tk8.6`→`_tk_data`。
- 构建后用 `python -m PyInstaller.utils.cliutils.archive_viewer -r -b <exe>` 反查归档，必需项：`tkinter\__init__.py`、`_tkinter.pyd`、`_tcl_data\init.tcl`、`_tk_data\tk.tcl`、`sqlite3.dll`、`_sqlite3.pyd`、`ffi.dll`、`_ctypes.pyd`、`agent_session_manager.orphan_cleaner`、`agent_session_manager.platforms.windows`，另有 tcl/tk DLL 正则。
- 烟测改为**可见窗口**启动（不传 `-WindowStyle`），超时 120 秒（首次启动会被杀软扫描拖慢数十秒），用临时 `CODEX_HOME`，结束后强制结束进程并清理；新增 `-SkipSmokeTest`，并在无交互桌面会话时自动跳过。
- 参数：`-Clean`、`-Python <path>`、`-SkipSmokeTest`。

## 验证

- 命令：`.\windows\build.ps1 -Clean`（TMP/TEMP 已指到 `.review-tmp`），exit 0。
- 产出 `dist\AgentSessionManager.exe` 13,417,540 字节 + `AgentSessionManager.exe.sha256`，SHA256 `09CE0BA2D72A36562F093C9C0E8B9054BD2F8D14DD7E7B546F7A3DB38FBA8648`。
- 脚本内置归档校验与 GUI 烟测均通过（`GUI smoke test: main window opened`）；主窗口标题「智能体会话管理器 · Agent Session Manager」。
- 对照实验（关键证据，说明这些参数不是冗余）：用只有 `--onefile --console --paths src` 的最小参数构建另一个 exe，运行后 `ctypes`、`tkinter`、`sqlite3`、`lzma`、`bz2` 全部导入失败——`sqlite3` 直接 `ModuleNotFoundError`，其余是缺 DLL；补齐参数后 `ctypes.WinDLL("kernel32")`、`sqlite3`、`tkinter`、`lzma`、`bz2`、`agent_session_manager.platforms.windows` 全部可导入，DSH zstd 解压可用（运行时从 `D:\Git\mingw64\bin\libzstd.dll` 加载）。
- 全量回归：`Ran 170 tests in 198.171s / OK (skipped=17)`，exit 0。
- 未验证：官方 `actions/setup-python` 标准 CPython 布局未在本机复跑（由 `.github/workflows/windows-release.yml` 覆盖）；Linux 运行时本机无 WSL 无法验证。

## 构建环境

项目专属 conda 环境 `D:\miniconda3\envs\agent-session-manager`：Python 3.11.18、PyInstaller 6.22.3、Tk 8.6、pip 26.2.2。此前用到的六个旧 conda 环境已按用户要求删除。构建命令：

```powershell
.\windows\build.ps1 -Clean -Python D:\miniconda3\envs\agent-session-manager\python.exe
```

## 文档同步

- `windows/README.md`：「构建 EXE」重写（解释器与依赖、两种 Tcl/Tk 布局、脚本实际做了什么、参数表、离线构建说明），末尾新增「已验证状态」。
- `README.md`：启动表的 Linux 环境措辞、构建与测试段补 Windows 构建命令。
- `README.en.md`：Run on your operating system 与 Build 段落同步。
- `CONTRIBUTING.md`、`linux/README.md`、`PROJECT.md`：conda 环境措辞统一为「带 Tcl/Tk 的 conda 环境（本机现名 codex-cleaner，旧项目名遗留）」；`linux/README.md` 去掉硬编码盘符路径；`PROJECT.md` 补 Windows 构建环境与提交/推送授权现状。

决定不改 Linux conda 环境名：本机没有 Linux 无法验证，且用户未要求。

## 临时产物

已删除 `.review-tmp` 下的 `_MEI*` 解包目录、`pip-*` 目录、`exe-smoke*`、`pipprobe`、`wtcheck`、`head-src`、`docs-build-backup-*` 与调试脚本（`exe_check*.py`、`zstdprobe.py`、`dbg*.py` 等）。保留各 `*-tests.log` 与 `final-regression.log` 作为本地测试证据（`.review-tmp` 与 `dist`/`build` 均被 git 忽略）。

## 下一步

- 提交并推送本轮 8 个文件（build.ps1 + 6 份文档 + PROJECT_VERSION.md）。
- 若要发布，用 `windows-release.yml` 打 tag，由 CI 在标准 CPython 布局下重建并上传 EXE；本地产物不入库。
