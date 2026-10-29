# Windows

本目录只包含 Windows 启动、EXE 构建与版本资源。Windows 系统适配代码在 `src/agent_session_manager/platforms/windows.py`，专属测试在 `tests/windows/`。

## 启动

普通用户可以从仓库 Releases 下载 `AgentSessionManager.exe` 和 `.sha256` 校验文件后直接运行。源码用户双击 `windows/start.bat`；它从项目根目录查找 EXE、`dist` 中的 EXE，找不到时依次探测可用的 Python：`ASM_PYTHON` 环境变量、**各 conda 安装根下的 `envs\agent-session-manager`（本项目专属环境）**、`py -3`、PATH 上的 `python.exe`、各盘符与 `%LOCALAPPDATA%\Programs\Python` 下的 conda/官方安装，用 `import tkinter` 实际验证后以 `pythonw.exe` 启动共享入口 `run_gui.py`。

Microsoft Store 的 `WindowsApps\python.exe` 是占位存根，执行会静默失败且退出码为 9009，脚本会跳过它。若自动探测失败，把可用解释器的完整路径写入 `ASM_PYTHON` 再运行即可。

源码运行需要带 Tcl/Tk 的 Python 3.11+。如果出现 `Can't find a usable init.tcl`，使用带完整 Tcl/Tk 的 Python 运行库或已打包的 EXE。

配置文件位于 `%LOCALAPPDATA%\AgentSessionManager\settings.json`，数据默认位于 `%USERPROFILE%\.codex`，也可以通过 `CODEX_HOME` 指定。目录定位使用 Explorer，进程枚举优先使用 Toolhelp32，失败时回退到 `tasklist`。

## 环境

本项目使用专属 conda 环境 `agent-session-manager`，定义在根目录 `environment.yml`（python 3.11 + tk + PyInstaller）：

```powershell
conda env create -f environment.yml
conda activate agent-session-manager
python -m pip install --no-build-isolation -e .
```

安装后该环境同时提供 `agent-session-manager`（CLI）与 `agent-session-manager-gui`（GUI）两个命令，并把源码目录以可编辑模式注册进去，所以运行、测试和 EXE 构建都不必再设 `PYTHONPATH`。本机现有环境位于 `D:\miniconda3\envs\agent-session-manager`（Python 3.11.18、Tk 8.6、PyInstaller 6.22.3），即下面命令中的路径。

```powershell
D:\miniconda3\envs\agent-session-manager\Scripts\agent-session-manager.exe scan
D:\miniconda3\envs\agent-session-manager\python.exe run_gui.py    # 或走 windows\start.bat
```

## 构建 EXE

从项目根目录执行：

```powershell
.\windows\build.ps1 -Clean
```

构建脚本的工作目录仍是项目根目录，输出为 `dist\AgentSessionManager.exe` 和 `.sha256`。版本资源位于 `windows/packaging/version_info.txt`。构建脚本只应在 Windows 上运行；Linux 不执行此构建流程。

### 解释器与依赖

脚本按 `-Python`、`ASM_PYTHON`、`py -3`、PATH 上的 `python`、`.build-venv`、各盘符及 `%LOCALAPPDATA%\Programs\Python` 下的 conda/官方安装依次探测，用 `import sys, tkinter` 实际验证 Python 3.11+，所以 Microsoft Store 的 `WindowsApps` 存根会被跳过。**优先选用已经装好 PyInstaller 的解释器**，因为仅在没有现成环境时才需要联网安装依赖。

本机推荐使用项目专属 conda 环境（已装 PyInstaller，可直接离线构建）：

```powershell
.\windows\build.ps1 -Clean -Python D:\miniconda3\envs\agent-session-manager\python.exe
```

脚本同时支持两种 Tcl/Tk 布局并自动探测：

- 官方 CPython（GitHub Actions 的 `actions/setup-python`）：`tcl\cl8.6`、`tcl\k8.6`、`DLLs\cl86t.dll`、`DLLs\k86t.dll`、`DLLs\sqlite3.dll`。
- conda 环境：`Library\lib\tcl8.6`、`Library\lib\tk8.6`、`Library\bin\tcl86t.dll`、`Library\bin\tk86t.dll`、`Library\bin\sqlite3.dll`。

### 脚本实际做了什么

1. 把 `TMP`/`TEMP` 指到项目内的 `.review-tmp`，避免受限配置文件下 venv/ensurepip 因临时目录不可写而失败。
2. 探测解释器与 Tcl/Tk 路径，把 `tcl86t.dll` 所在目录和 `DLLs` 前置进 `PATH`，并设置 `TCL_LIBRARY`/`TK_LIBRARY`，让 PyInstaller 能解析 `ffi.dll`、`liblzma.dll`、`LIBBZ2.dll` 等间接依赖。
3. 以绝对路径调用 PyInstaller，显式补 `--hidden-import sqlite3 lzma bz2 tkinter _tkinter`。缺 `sqlite3` 时打包出的 EXE 里根本没有该模块，缺 `--add-binary`/`--add-data` 时 `_ctypes`、`_lzma`、`_bz2`、`_tkinter` 会因找不到 DLL 而导入失败——这些都不会表现为构建错误，只会让 EXE 启动后崩溃。
4. 用 `pyi-archive_viewer` 反查归档，确认 `tkinter`、`_tkinter.pyd`、`tcl`/`tk` 数据、`sqlite3.dll`、`ffi.dll`、`_ctypes.pyd` 与包内平台模块都在，然后才继续。
5. 以**可见窗口**启动一次 EXE 做烟测：隐藏或最小化启动拿不到 `MainWindowTitle`，会让健康的构建一直超时，所以这里不传 `-WindowStyle`。烟测使用临时 `CODEX_HOME`，结束后强制结束进程并清理。无交互桌面会话或加 `-SkipSmokeTest` 时跳过。

### 构建参数

| 参数 | 说明 |
| --- | --- |
| `-Clean` | 删除既有 `.build-venv`、`dist` 输出后重建 |
| `-Python <path>` | 指定解释器（`python.exe` 完整路径） |
| `-SkipSmokeTest` | 跳过 GUI 烟测（仅构建） |

无网络的机器上，`pip install ".[build]"` 会失败；此时用 `-Python` 指向已经装好 PyInstaller 的解释器（本机即 `D:\miniconda3\envs\agent-session-manager`）即可离线构建。

## 测试

从项目根目录执行：

```powershell
New-Item -ItemType Directory -Force .review-tmp | Out-Null
$env:TMP = (Resolve-Path .review-tmp).Path
$env:TEMP = $env:TMP
D:\miniconda3\envs\agent-session-manager\python.exe -B -m unittest discover -s tests -t . -v
```

用项目环境跑全量回归实测为 `Ran 170 tests` / `OK (skipped=17)`，约 3 分钟。若改用未安装本项目的方式运行，再补 `$env:PYTHONPATH = "src"`。

Linux 专属测试在 Windows 跳过。共享的界面、扫描、清理和备份逻辑见[项目说明](../README.md)。

## 已验证状态

2026-10-29 在 Windows 10 (26100) 上实测：

- `windows\build.ps1 -Clean` 用 conda 环境 `agent-session-manager` 完成构建，产出 `dist\AgentSessionManager.exe`（约 13 MB）与 `.sha256`，脚本内置的归档校验和 GUI 烟测均通过。
- EXE 启动后主窗口标题为 `智能体会话管理器 · Agent Session Manager`；打包环境中 `ctypes.WinDLL("kernel32")`、`sqlite3`、`tkinter`、`lzma`、`bz2` 均可导入，DSH 的 zstd 解压可用（运行时从 PATH 或 Git/conda 安装位置加载 `libzstd.dll`）。
- 尚未在官方 `actions/setup-python` 的标准 CPython 布局下本地复跑；CI 的 `windows-release.yml` 会覆盖该路径。
