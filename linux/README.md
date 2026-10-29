# Linux

本目录只包含 Linux 启动和测试入口。Linux 系统适配代码在 `src/agent_session_manager/platforms/linux.py`，专属测试在 `tests/linux/`。

## 启动

需要一个带 Tcl/Tk 的 Python 3.11+ 环境；脚本不创建环境、不安装依赖。当前 Linux 主机上现成的 conda 环境名为 `codex-cleaner`（旧项目名遗留），继续使用它即可。先确认环境可用：

```bash
python -c "import tkinter; print(tkinter.TkVersion)"
```

```bash
cd /path/to/agent-session-manager
conda activate codex-cleaner
bash linux/start.sh
```

命令行入口：

```bash
bash linux/cli.sh --help
bash linux/cli.sh scan
```

脚本按自身位置找到项目，可以从其他工作目录启动。`python` 使用当前已激活环境中的解释器。图形界面需要可用的 X11/Wayland 桌面与 Tcl/Tk；打开目录使用 `xdg-open`，进程枚举使用 `ps`。如果进程枚举失败，清理操作会被阻止。

配置文件位于 `$XDG_CONFIG_HOME/AgentSessionManager/settings.json`，未设置时使用 `~/.config/AgentSessionManager/settings.json`。Codex 数据目录使用 `$CODEX_HOME`，未设置时使用 `~/.codex`。

## 字体与缩放

优先选择本机可用的 Noto 中文、文泉驿等字体，使用 Linux 的 Clam 控件主题，不再使用容易缺字的文件夹表情符号。窗口以 1420×900 为基准，控件 12 磅、正文 13 磅；放大时字号自动增大，控件最大 28 磅，标题、正文、行高和列宽同步变化，还原窗口时恢复。Linux 通过程序专用 `fonts.conf` 和基础 Tk 缩放统一为 96 DPI，避免桌面 Xft DPI 与窗口字号缩放重复叠加；不修改系统字体设置。

若当前 conda Tk 是 noxft 构建，无法使用系统 TrueType 字体，可在这个已激活的环境中准备项目内的 Xft 渲染库：

```bash
python -B linux/prepare_fonts.py
bash linux/start.sh
```

准备脚本仅支持 Linux x86_64/Tk 8.6，下载约 3.3 MB 的固定版本 conda-forge 包并验证 SHA-256；只提取 Tk 渲染库、脚本和许可证到 `linux/.runtime/tk-xft/`，为项目内的库补齐 SONAME，避免同时加载环境中的另一份 Tk；不创建或修改 conda 环境，也不使用系统 `/tmp`。需要本机已有 `zstd` 和 Xft 系统库；当前 conda 安装自带的 `zstd` 也可以使用。依赖不足时明确报错，不自动安装。

存在该目录时，GUI 启动会用同一个 Python 解释器重新启动，并仅为该进程加载项目内渲染库；CLI 扫描和测试不加载它。该运行库目录已被 Git 忽略，换电脑后可重新执行准备命令。Tk 版本不兼容时会停止并提示。

## 测试

```bash
bash linux/test.sh -q
```

测试使用项目内 `.review-tmp/`，不向内存 `/tmp` 复制会话或环境。Windows 专属测试在 Linux 跳过。扫描、清理、备份与恢复的共同规则见[项目说明](../README.md)。
