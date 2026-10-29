# DSH 接入依据与边界

核对日期：2026-10-23，2026-10-29 按官方 master 复核。本机发布包 `@deepseek-ai/dsh 0.2.0-rc.2`，逻辑格式仍为 v4。官方网页 master 可能领先或滞后于本机；本机 v4 以发布包的 README、类型定义和编码器交叉核实。工具不执行、不导入 DSH 的业务代码，不启动 DSH/Codex 服务。

## 官方依据

- [持久化包](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/session/session-persistence-jsonl/README.md)：每个会话拥有独立目录，历史格式保留，选择编号最高的规范文件；文件名版本和头部版本必须一致。一套根目录只有一种压缩编码。压缩内容为拼接的标准 Zstandard 帧。独立读取器按块解压，限制单行 16 MiB；损坏、混合编码或截断拒绝写入，不自动修复原文件。`compression` 默认 `zstd`，设 `none` 时是裸 NDJSON；一个根目录只能一种编码，改编码必须换根目录。官方说明明确日志只增不减、该层不提供删除 API，累积的会话只能由外部清理，本工具就是这个外部清理器。
- [会话语义](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/session/README.md)：分叉继承历史与附属子智能体不同。仅 `origin=subagent` 加 `parentSession` 建立清理依赖；`isSeeded` 自身不构成附属关系。当前阅读器处理内置消息的替换区间，默认仅显示正文；reasoning 和注入上下文由显示内部消息选项控制。`agent/inbox/spliced` 是拼进下一次模型可见内容的中间态，不计入消息面，避免把它当成独立消息源。不执行第三方插件的消息投影。旧格式不自动迁移。
- [投影缓存](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/session/session-projection-cache/README.md)：标题等是派生快照。标题仅在缓存生命周期与头部一致时使用；缓存不替代原日志。官方承认该层没有淘汰或保留策略、缓存文件按会话持续累积，裁剪属于带外维护；因此删除所选会话时同步移除其独立缓存文件，否则缓存会成为删除后的残留。
- [Home 路径](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/util/home-paths/README.md)：显式路径优先于 `DSH_HOME`，空白环境变量视为未设置。`~/.dsh` 是默认 home；持久化插件的 `root` 必须由部署指定。界面可填独立会话根目录，CLI 使用 `--sessions-root`。默认 `HOME/sessions` 对应本机标准组合，不能代表所有自定义部署。外置根目录目前仅扫描和阅读，导出的跨根路径映射和清理事务暂未实现。
- [附件存储](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/attachment/attachment-local/README.md)：附件按内容去重，会话和分叉可能共享。此次清理保留全局附件与 request-image 缓存，不根据 UUID 或目录年龄猜测垃圾，不宣称完整隐私擦除。

## 本机桌面数据

`storages/workspace.json` 是本机已核实的 JSON 工作区索引，包含工作区的 `sessionIds` 和全局的 `archivedSessionIds`、`pinnedSessionIds`。这不是所有 DSH 部署都必须采用的公开稳定布局。仅移除精确匹配的目标 ID；未知位置仍有目标引用时停止。工作区及项目目录保留，不删除配置、凭据、SSH、插件或模型缓存。

## 功能和事务

共享应用接口位于 `applications/`；OS 接口仍位于 `platforms/`。Codex 使用原实现，DSH 使用独立适配器，模型携带 application 标识，拒绝混用执行器。更名后旧 Python 包名与旧 CLI 入口已移除，保留的只有数据层兼容（备份工具标记、操作日志文件名、旧配置目录名）。读取或清理计划失败时 CLI 打印单行原因并退出 2，不再抛裸 traceback；界面在异步线程内转为错误对话框。

- 当前支持 v4 的扫描、项目分组、标题、消息阅读、导出与 Linux 本地清理；旧版本或新版本显示为未知，只读导出，不降级读取较旧文件来获得删除权限。
- v4 头部的 delegationDepth 可省略；提供时必须是非负整数，布尔值、null、字符串及负数拒绝。依据本机 dsh-session 的 SessionHeader 类型和 canonicalHeader 验证；不自动补写或升级原文件。
- 清理父会话时包括真实附属后代，禁止单删附属子会话。删除其会话自有目录及全部历史格式副本、对应投影缓存，并更新工作区引用。
- DSH 必须关闭；检查进程和 home 的 PID 文件，并获取已有 `session.lock` 的非阻塞内核锁。官方 POSIX 后端就是 `session.lock` 上的非阻塞 `flock(2)`，Windows 后端改用按路径派生的命名内核信号量、不占用文件系统对象；本工具尚未实现 Windows 那套语义，因此 Windows 的 DSH 清理明确拒绝执行，查看及导出可用。锁文件存在不表示进程存活，空闲锁不构成垃圾依据。
- 压缩读取需要 libzstd：先查 `AGENT_SESSION_MANAGER_ZSTD`（也认 `DSH_ZSTD_LIBRARY`、`ZSTD_LIBRARY`），再查系统库搜索，最后按 PATH 项及其父目录下的 `mingw64/bin`、`usr/bin` 和常见 Git/conda 安装位置探测；任一可加载即用，全不成立时只报一条安装提示，裸 NDJSON 会话不受影响。
- 预览记录内容哈希，执行前重新扫描及核对。数据移动到同一 home 下的 `.cleaner-dsh-transaction-*`，不复制到系统 tmp。普通异常或 Ctrl+C 将原件移回并恢复工作区。恢复失败保留恢复清单并阻止下一次清理。
- 验证成功后标记 committed，再清除暂存。若清除暂存失败，报告残留位置，不把仍有暂存副本说成彻底擦除。强制终止或断电不保证自动回滚：检查 recovery.json 和 data/，非 committed 状态需核对恢复，committed 表示逻辑删除已完成，确认后清除残留。
- 可选持久备份是会话导出包，不能整体覆盖恢复应用状态。DSH 重命名、归档修改、全局孤立附件清理及备份自动删除暂未启用；界面明确禁用相应 Codex 专用功能。

测试使用真实标准 Zstd 拼接帧和项目内合成数据；本机 `.dsh` 仅做只读扫描与界面验证。
