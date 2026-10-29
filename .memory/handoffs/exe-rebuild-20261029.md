---
task_id: exe-rebuild-20261029
owner: dsh:lead
recorded_at: '2026-10-29T20:55:00+08:00'
workspace: E:\projects\agent_session_manager
baseline_sha: f119e3a
state_at_checkpoint: pushed
---

Snapshot at the recorded time; project_status is authoritative for current status.

# 用项目 conda 环境重新生成 EXE，并修掉重定向下会中止构建的裸原生调用

用户要求（m03758 逐字）："接下来生成exe啊"
背景：上一轮把项目运行/测试/打包统一到项目专属 conda 环境 `D:\miniconda3\envs\agent-session-manager`（提交 4deddaa / f119e3a），本轮据此重新构建 Windows 单文件 EXE 并实测。

## 本轮修掉的缺陷

`windows/build.ps1:372` 是脚本里唯一没走 `Invoke-Native` 的原生调用：

```powershell
& $buildPython @pyInstallerArgs
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed to build the Windows application." }
```

PyInstaller 把进度写到 stderr；在 `$ErrorActionPreference = "Stop"` 且脚本输出被重定向时（CI 日志、`2>&1 | Tee-Object`），Windows PowerShell 5.1 会把那条 stderr 升级成终止错误记录（`NativeCommandError`，报错位置指向 `build.ps1:372`），健康的构建在分析阶段就 exit 1。改为：

```powershell
$buildResult = Invoke-Native -Exe $buildPython -Arguments $pyInstallerArgs
foreach ($line in $buildResult.Output) { Write-Host "$line" }
if ($buildResult.ExitCode -ne 0) { throw "PyInstaller failed to build the Windows application." }
```

教训：脚本内**所有**原生调用（含最终构建命令本身）都要走 `Invoke-Native`，因为它内部会临时把 `ErrorActionPreference` 降到 `Continue` 并自己收 `$LASTEXITCODE`。

## 构建结果

- 命令：`\windows\build.ps1 -Clean -Python D:\miniconda3\envs\agent-session-manager\python.exe`，外层 TMP/TEMP=`.review-tmp`，**并特意用 `2>&1 | Tee-Object` 重定向**（即之前失败的那种形态）→ exit 0。
- 日志顺序：`Interpreter: ... (Python 3.11, via -Python)` → `Build environment: selected interpreter (PyInstaller already installed)` → `Tcl/Tk: D:\miniconda3\envs\agent-session-manager\Library\lib\tcl8.6` → `hook-tkinter.py`/`TclTkInfo`/`hook-_tkinter.py`(×2)/`hook-sqlite3.py`/`hook-_ctypes.py` → `Analyzing hidden import 'agent_session_manager.platforms.windows'` → `pyi_rth__tkinter.py` → `Build complete!` → `GUI smoke test: main window opened` → `Built:` / `SHA256:` / `Checksum:`。stderr 为空。
- 产物：`dist\AgentSessionManager.exe` **13,415,433 字节**，SHA256 `dc88f056dc62e8e0fbad923795b9a116d1715b80c046eb099114cf180fa68903`；`dist\AgentSessionManager.exe.sha256` 91 字节（小写哈希 + 两空格 + 文件名，与 `Get-FileHash` 一致）。
- 版本资源：`FileVersion=2026.10.29`、`ProductName=Agent Session Manager`。
- `build\AgentSessionManager\warn-AgentSessionManager.txt` 以 `Library not found|libzstd|ffi.dll|liblzma|LIBBZ2|libcrypto` 检索无命中。

## 环境限制（重要，手工验证 EXE 必读）

PyInstaller onefile 启动时先把归档解到 `%TEMP%\_MEI*`。本沙箱下系统临时目录不可写，直接双击/启动 exe 会弹出错误框而不是崩溃栈：

```
cls=#32770 text=[Error]
  CHILD cls=Static text=[Failed to extract LIBBZ2.dll: failed to open target file!
fopen: Permission denied
]
  CHILD cls=Button text=[确定]
```

把 `TMP`/`TEMP` 指到项目内 `.review-tmp` 后（`MEI_DIRS=1`，37.1 秒后窗口出现）：

```
WIN cls=TkTopLevel vis=True text=[智能体会话管理器 · Agent Session Manager]   (pid 1964)
WIN cls=PyInstallerOnefileHiddenWindow vis=False text=[PyInstaller Onefile Hidden Window]  (pid 18108，引导进程)
```

`build.ps1` 自身已把 `TMP`/`TEMP` 前置到 `.review-tmp`，所以脚本内烟测不受影响。另外隐藏/最小化启动拿不到 `MainWindowTitle`，必须可见启动。

## 验证

- 构建：exit 0，`Build complete!` + `GUI smoke test: main window opened`（内置归档校验通过后才进烟测）。
- 归档必查项（脚本内置 `pyi-archive_viewer -r -b`）：`tkinter\__init__.py`、`_tkinter.pyd`、`_tcl_data\init.tcl`、`_tk_data\tk.tcl`、`sqlite3.dll`、`_sqlite3.pyd`、`ffi.dll`、`_ctypes.pyd`、`agent_session_manager.orphan_cleaner`、`agent_session_manager.platforms.windows` 与 tcl/tk DLL 正则。
- 手工可见启动（TMP/TEMP 指向 `.review-tmp`）：主窗口标题 `智能体会话管理器 · Agent Session Manager`，`responding=True`。
- 全量回归（项目环境解释器 `D:\miniconda3\envs\agent-session-manager\python.exe -B -m unittest discover -s tests -t . -v`，TMP/TEMP=`.review-tmp`，不设 PYTHONPATH）：`Ran 170 tests in 197.198s` / `OK (skipped=17)`，日志 `.review-tmp\regression-exe-round.log`。

## 改动文件

- `windows/build.ps1`：`build.ps1:372-382` 改为 `Invoke-Native` + 回放输出 + 按 ExitCode 判定。
- `windows/README.md`：新增「脚本实际做了什么」第 6 条（所有原生命令走 `Invoke-Native`，重定向下 stderr 会中止构建）；「## 已验证状态」改写为本轮实测（含字节数、SHA256、版本资源）并补 TMP/`_MEI*` 限制说明。
- `PROJECT_VERSION.md`：追加第 26 条（重定向中止构建的根因、修复、实测数据、回退到 f119e3a）。

## 提交与推送

- 提交 `cbf7a24` "Fix the Windows build aborting when its output is redirected"：4 files changed, 95 insertions(+), 3 deletions(-)（`windows/build.ps1`、`windows/README.md`、`PROJECT_VERSION.md`、本交接记录）；基线 f119e3a。
- 推送：`f119e3a..cbf7a24  main -> main`，`git ls-remote origin refs/heads/main` = `cbf7a2450d5e65de1444be876501d55316c31845`；本地 `refs/remotes/origin/main` 已用 `git update-ref` 同步，`git status -sb` 显示 `## main...origin/main` 无 ahead。推送通道同上一轮：PowerShell `Add-Type` 定义 `CredReadW`/`CredFree` 读 `git:https://github.com` 的 `gho_` token，拼一次性 remote URL，token 不落盘；脚本 `.agent-tmp\push3.ps1` 用后已删。**注意：`git` 调用必须带 `-c safe.directory=E:/projects/agent_session_manager`，否则报 `fatal: detected dubious ownership`。**

## 未做/限制

- 未在官方 `actions/setup-python` 标准 CPython 布局下本地复跑，CI 的 `windows-release.yml` 覆盖该路径。
- 未打 tag、未发 GitHub Release。
- EXE 的 DSH zstd 解压依赖运行时能定位 `libzstd.dll`（本机命中 `D:\Git\mingw64\bin\libzstd.dll`），归档内不含该库。
