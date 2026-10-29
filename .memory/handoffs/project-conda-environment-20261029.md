---
task_id: project-conda-environment-20261029
owner: dsh:lead
recorded_at: '2026-10-29T20:35:00+08:00'
workspace: E:\projects\agent_session_manager
baseline_sha: 1fa4fe6
state_at_checkpoint: implemented-uncommitted
---

Snapshot at the recorded time; project_status is authoritative for current status.

# 项目专属 conda 环境成为默认运行/测试/构建环境

用户要求（m03532 逐字）："你自己的conda环境呢？我不是说了创建自己的conda环境？"
背景：上一轮交付把 Windows 验证环境写成 DSH 自带 Python，用户不接受；项目必须有自己可复现的 conda 环境，并作为正式运行、测试与打包环境落进文档与启动脚本。

## 环境事实

- 环境 `D:\miniconda3\envs\agent-session-manager`（`D:\miniconda3\Scripts\conda-script.py create -n agent-session-manager python=3.11 tk -y`）：Python 3.11.18、Tk 8.6、PyInstaller 6.22.3、pip 26.2.2、setuptools 84.0.0。
- 已可编辑安装本项目：`python.exe -m pip install --disable-pip-version-check --no-build-isolation --no-index -e .`（工作目录项目根，TMP/TEMP=.review-tmp，需要完整权限否则写工作区外失败）→ `Successfully installed agent-session-manager-2026.10.29`。
- 安装后可编辑入口生效：`Scripts\agent-session-manager.exe --version` → `agent-session-manager 2026.10.29`；`agent-session-manager.exe config --json` 输出 schema_version=2 与 settings_path=`C:\Users\qir\AppData\Local\AgentSessionManager\settings.json`；任意 cwd 下 `import agent_session_manager` 指向 `E:\projects\agent_session_manager\src\agent_session_manager\__init__.py`。因此不再需要 PYTHONPATH。
- 不激活也能用：把 PATH 缩成 `C:\Windows\system32;C:\Windows` 后用绝对路径 python.exe 仍然 `import tkinter` 成功（`_tkinter.pyd` 在 `D:\miniconda3\envs\agent-session-manager\DLLs\_tkinter.pyd`），所以启动脚本/测试/BUILD 都直接用绝对路径即可。
- `D:\miniconda3\python.exe`（base，3.13.12）没有 PyInstaller，不能当构建环境。

## 新增文件

- `E:\projects\agent_session_manager\environment.yml`：环境唯一权威定义（name: agent-session-manager；python=3.11、tk、pip、pip: pyinstaller>=6.14,<7；不写死 channels，用用户已配置的源）。`conda env create -f environment.yml` 后 `python -m pip install --no-build-isolation -e .`。

## 改动清单

- `windows/start.bat`（90 行）：新增 `:probe_env` 子例程，在 `ASM_PYTHON` 之后、`py -3` 之前扫描 C:–G: 盘符与 `%USERPROFILE%`／`%LOCALAPPDATA%`／`%ProgramData%` 下 miniconda3／anaconda3／Miniconda3／Anaconda3 的 `envs\agent-session-manager\python.exe`；失败提示补 `conda env create -f environment.yml`。
- `PROJECT.md`：环境段整段重写（原「Windows 验证使用 DSH 自带 Python」删除，改为项目环境；DSH Python 降级为经用户同意的临时兜底）；回归入口改为项目环境解释器。
- `windows/README.md`：启动探测顺序补「各 conda 安装根下的 envs\agent-session-manager」；新增整节「## 环境」；「## 测试」命令改用项目环境解释器并注明实测结果。
- `README.md`：启动表 Linux 行、Windows 探测段、「从源码运行」新增 conda 三行代码块、「构建与测试」末段改为「回归测试统一在项目 conda 环境 agent-session-manager 中运行」。
- `README.en.md`：同步四处（Linux 行、Windows 探测段、Source and CLI、测试句）。
- `CONTRIBUTING.md`：Development 段改为 Both platforms use the project conda environment + 代码块 + Linux 旧名说明。
- `linux/README.md`：「## 启动」加 environment.yml 代码块，`conda activate codex-cleaner` 行注释「# 或新名 agent-session-manager」。决定不改 Linux 主机上已有环境名（本机无 Linux 无法验证，用户未要求），仍写旧名 codex-cleaner 的位置都是"Linux 主机现状说明"。
- `PROJECT_VERSION.md`：追加第 25 条（环境统一 + environment.yml + start.bat 探测 + 文档同步 + 验证 + 回退到 1fa4fe6）。

## 验证

- `windows/start.bat` 在隔离目录实测（`.review-tmp\startbat-test\windows\` + 桩 run_gui.py 写 sys.executable）：`cmd /c start.bat` → BAT_EXIT=0，选中 `D:\miniconda3\envs\agent-session-manager\pythonw.exe`，证明新探测分支生效。
- 全量回归用项目环境解释器（`D:\miniconda3\envs\agent-session-manager\python.exe -B -m unittest discover -s tests -t . -v`，TMP/TEMP=.review-tmp，不设 PYTHONPATH）：`Ran 170 tests in 196.778s` / `OK (skipped=17)`，日志 `.review-tmp\conda-regression.log`。
- CLI 与 GUI 入口经可编辑安装后的 console script 实测（--version／config --json）。

## 未做/限制

- 未在 Linux 主机验证 `environment.yml` 创建的等价环境；Linux 侧文档保留旧环境名现状说明。
- 未重新跑 `windows/build.ps1`（本轮未改构建脚本，上一轮的 exe 与 SHA256 仍为本机构建结果）。
- conda 环境名在 Linux 主机上仍为 codex-cleaner，未强行改名。
