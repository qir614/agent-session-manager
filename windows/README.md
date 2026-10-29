# Windows

本目录只包含 Windows 启动、EXE 构建与版本资源。Windows 系统适配代码在 `src/agent_session_manager/platforms/windows.py`，专属测试在 `tests/windows/`。

## 启动

普通用户可以从仓库 Releases 下载 `AgentSessionManager.exe` 和 `.sha256` 校验文件后直接运行。源码用户双击 `windows/start.bat`；它从项目根目录查找 EXE、`dist` 中的 EXE，找不到时依次探测可用的 Python：`ASM_PYTHON` 环境变量、`py -3`、PATH 上的 `python.exe`、各盘符与 `%LOCALAPPDATA%\Programs\Python` 下的 conda/官方安装，用 `import tkinter` 实际验证后以 `pythonw.exe` 启动共享入口 `run_gui.py`。

Microsoft Store 的 `WindowsApps\python.exe` 是占位存根，执行会静默失败且退出码为 9009，脚本会跳过它。若自动探测失败，把可用解释器的完整路径写入 `ASM_PYTHON` 再运行即可。

源码运行需要带 Tcl/Tk 的 Python 3.11+。如果出现 `Can't find a usable init.tcl`，使用带完整 Tcl/Tk 的 Python 运行库或已打包的 EXE。

配置文件位于 `%LOCALAPPDATA%\AgentSessionManager\settings.json`，数据默认位于 `%USERPROFILE%\.codex`，也可以通过 `CODEX_HOME` 指定。目录定位使用 Explorer，进程枚举优先使用 Toolhelp32，失败时回退到 `tasklist`。

## 构建 EXE

从项目根目录执行：

```powershell
.\windows\build.ps1 -Clean
```

构建脚本的工作目录仍是项目根目录，输出为 `dist\AgentSessionManager.exe` 和 `.sha256`。版本资源位于 `windows/packaging/version_info.txt`。构建脚本只应在 Windows 上运行；Linux 不执行此构建流程。

## 测试

从项目根目录执行：

```powershell
$env:PYTHONPATH = "src"
New-Item -ItemType Directory -Force .review-tmp | Out-Null
$env:TMP = (Resolve-Path .review-tmp).Path
$env:TEMP = $env:TMP
python -B -m unittest discover -s tests -t . -v
```

Linux 专属测试在 Windows 跳过。共享的界面、扫描、清理和备份逻辑见[项目说明](../README.md)。本次重构在 Linux 验证，Windows 原生启动与 EXE 构建仍需在 Windows 验证。
