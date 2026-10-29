# PROJECT.md

## 目标与方向

管理 Codex 与 DSH 的本地对话，提供查看、导出、影响预览、删除和残留核查。用户于本次访谈确认：后续优先接入官方删除接口；接口未覆盖的本地残留继续按可验证的归属处理。本次六项修复保持当前独立实现，不在本次任务内改造为官方接口客户端。

## 工作目录与环境

固定项目根：E:\projects\agent_session_manager\（Windows 主机；产品、仓库与本地检出目录均已更名为 Agent Session Manager／agent-session-manager，旧路径 E:\projects\codex-local-session-cleaner 不再存在）。项目内的隔离工作树、报告、恢复材料和临时文件均留在该目录。不得改动其他检出或旧工作树。

项目使用专属 conda 环境 agent-session-manager，定义见根目录 environment.yml（python 3.11 + tk + PyInstaller）。Windows 上为 D:\miniconda3\envs\agent-session-manager，已以可编辑模式安装本项目（pip install -e .），提供 agent-session-manager 与 agent-session-manager-gui 两个命令，并作为回归测试、GUI 启动与 EXE 构建的默认环境（PyInstaller 6.22.3，可离线构建）。已以可编辑模式安装本项目后运行与测试不再需要 PYTHONPATH，但仍需把 TMP/TEMP 指到 <项目根>\.review-tmp 并以 -B 运行（未安装时补 PYTHONPATH=src）。Linux 回归使用同一 environment.yml 创建的项目环境（当前 Linux 主机上的现成环境名仍为 codex-cleaner，属旧项目名遗留，尚未在本机验证改名）；若确需其他环境，只能经授权使用 conda；不安装系统或全局 Python 依赖。DSH 自带 Python 只在项目环境不可用且用户同意时作为临时兜底，不作为项目环境。Windows 的 EXE 构建入口见 windows/README.md。大型产物放磁盘，不将代码、数据库或大体积测试夹具放入系统临时目录。

## 架构

Python 共享逻辑位于 src/agent_session_manager；applications 分离 Codex 与 DSH，configuration 统一分区设置、旧版迁移与 schema_version，platforms 与 linux/windows 入口分离平台行为。GUI 使用 Tk。Codex 当前提供 Linux/Windows 支持，DSH 的本地写入限于已验证的 Linux 格式；平台支持不能代替对应平台实测。

## 数据边界

未知格式、无法确认的引用或预览后数据变化必须阻止删除。归档对话仍是存活数据，必须纳入扫描及附件保护。分页数据库、附件元数据和会话文件共同决定附件归属；共享附件保留。项目目录、凭据、配置、云端内容和已有导出有各自生命周期，不随对话删除扩大清理。用户配置位于系统配置目录的 AgentSessionManager/settings.json（旧 CodexLocalSessionCleaner 目录仅作迁移回退），不属于会话数据清理范围。

真实对话的删除另按用户当次明确范围授权；当前六项修复只允许隔离夹具删除和真实数据只读核对。不得通过停用进程检查或替换真实实现，在用户数据上进行删除测试。

## 验证与记录

Windows 回归入口（项目根目录，使用项目 conda 环境 D:\miniconda3\envs\agent-session-manager\python.exe，或已激活该环境后的 python）：把 TMP/TEMP 设为 <项目根>\.review-tmp 后运行 python -B -m unittest discover -s tests -t . -v（已可编辑安装故无需 PYTHONPATH；未安装时补 PYTHONPATH=src）。Linux 回归入口：激活带 Tcl/Tk 的 conda 环境后执行 bash linux/test.sh -q（当前 Linux 主机上的环境名仍为 codex-cleaner，或 PATH=/home/qir/miniconda3/envs/codex-cleaner/bin:$PATH）。临时目录必须位于项目内且已存在，命令使用 -B 避免生成 Python 缓存。测试应覆盖删除范围、保留对象、未知结构阻止、预览变化及失败恢复。GUI 行为改变时还需实际打开核对。

任务记录留在项目 .memory 和协调记录中；格式依据见 THREAD_FORMAT.md、DSH_FORMAT.md，审查及测试证据留在 reports。PROJECT_VERSION.md 保留简短版本和恢复记录。仅清理已确认归属且不再需要的本任务临时产物。

## Git 与恢复

保留既有未提交内容。修改前保护直接涉及的当前文件；当前 HEAD 不代表尚未提交的版本已获保护。任务提交只包含本任务差异；文件混有此前修改时，不将整文件计入本任务，不自动暂存、重置或强制清理。用户已授权常规提交与推送（旧会话 seq 1792「该提交git就要提交git，不用问我」、m01139「都由你来决定吧」）；强制推送、改写历史与并入他人分支仍需单独确认。

## 官方依据

Codex：https://learn.chatgpt.com/docs/app-server 。DSH：https://github.com/deepseek-ai/deepseek-harness 。同时核对本机已安装版本和实际表结构，不将上游 master 或旧项目文档视为本机兼容性证明。
