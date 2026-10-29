# 线程格式与分类依据

核查日期：2026-10-23。本机 Codex CLI 0.163.0-alpha.10.2。以下区分公开协议、本机存储和历史桌面实现证据，不承诺内部存储格式长期稳定。

## 官方协议

[Codex App Server 官方文档](https://learn.chatgpt.com/docs/app-server)提供 `thread/read`、`thread/list`、父子线程过滤和协议导出方法：

```bash
codex app-server generate-json-schema --experimental --out ./schemas
```

以本机生成的 Schema 为准：

| 字段 | 含义 | 本工具用途 |
| --- | --- | --- |
| `source` / `SessionSource` | CLI、IDE、App Server、自定义来源或结构化子智能体来源 | 识别结构化 `subagent.thread_spawn`；不能与统计标签混用 |
| `threadSource` / `ThreadSource` | 可选统计来源分类，类型是开放字符串 | 保留原始值，不能覆盖明确父子关系 |
| `parentThreadId` | 只有子智能体才有的父线程 ID | 与 rollout 父字段、SQLite spawn 边共同构建依赖图 |
| `forkedFromId` | 分叉时复制历史的来源线程 | 不当作 spawn 父关系，不随原线程级联删除 |

公开 API 使用 camelCase；JSONL 和 SQLite 使用的 snake_case 字段通过各自存储适配读取，不能直接把 API 枚举当作数据库字段枚举。

## 2026-10-11 桌面实现的历史证据

本机应用 `.vite/build/src-C1dW0Du8.js` 在任务启动处理使用的同一来源集合中包含 `user`、`agent_created_thread`、`agent_forked_thread`。中文界面资源 `webview/assets/zh-CN-3ed9eb1db28a.js` 将后两者标为“智能体创建的任务”“智能体派生的任务”。该集合用于 `thread/start` 和任务环境处理，不能据此断言它是完整的侧边栏可见性规则。这是当前已安装应用的实现证据，不是公开存储协议承诺。

本次查阅的公开文档没有直接解释 `agent_created_thread`；本工具仅在没有结构化子智能体来源、没有明确父线程时，将上述三个已核对标签识别为主任务。其他未支持标签保留未知，不能仅凭名称猜测后启用删除。

## 判定与删除规则

1. 显式 spawn 父关系优先于统计标签；SQLite 和 rollout 父信息冲突时阻止清理。
2. 结构化内部来源不能被数据库的 `user` 标签升级为主任务。
3. 项目归属独立读取项目分配、目录 catalog 和 SQLite 项目 ID；项目名称或工作目录不构成父子关系。
4. 删除规划独立检查父关系：父线程存在时禁止单独删除子线程，选择父线程才纳入实际后代；分叉来源不构成后代。
5. 云端目录只读；未知类型仍禁止直接删除；损坏的结构化来源阻止规划。

回归测试在 `tests/test_thread_semantics.py` 和 `tests/test_agent_created_projects.py`。真实会话核查只读，不以真实用户数据执行删除测试。

## 其他已核对的官方约束

- [配置环境变量](https://learn.chatgpt.com/docs/config-file/environment-variables)：`CODEX_HOME` 与 `CODEX_SQLITE_HOME` 是不同位置；`sqlite_home` 配置优先。
- [配置示例](https://learn.chatgpt.com/docs/config-file/config-sample)：`log_dir` 可单独配置。当前本地事务仅支持默认存储位置，外置位置禁止写入。
- [高级配置](https://learn.chatgpt.com/docs/config-file/config-advanced)：`history.jsonl` 是本地持久输入历史；本项目按精确会话 ID 清理并加入事务快照。文件行结构仍按本机已验证格式实现，不宣称官方稳定契约。
- [App-server](https://learn.chatgpt.com/docs/app-server)：分页历史与 legacy 不同，缺少 rollout 不能单独证明记录失效。缺少 rollout 的分页记录只读；有 rollout 的分页记录在本地分页库结构验证通过后由本项目清理，同样验证所有后代。fork 来源与 spawn 后代必须分开；这不代表所有历史模式下分叉都没有共享存储依赖。
- [项目](https://learn.chatgpt.com/docs/projects?surface=app) 与 [工作树](https://learn.chatgpt.com/docs/environments/git-worktrees)：项目文件、云端数据和 Git 工作树各有生命周期，不能当会话缓存清除。

本机分页库已核对的表为 `thread_turns`、`thread_items`、`thread_history_projection_state`、`thread_realtime_items`，按精确 `thread_id` 删除、快照及复核。表结构来自只读本机核查，不是公开格式承诺；新表或缺少必要字段时停止。保留分叉指向待删分页历史时停止，不能假定分叉历史一定独立。

## 2026-10-23 本机存储与删除验证

- 无 rollout 且标题为空的分页记录仍保留在扫描清单中，显示未命名标题；缺少文件不代表可以清理。
- 附件归属联合核查所有存活 JSONL、分页消息表和 thread_attachments；数据库中尚未出现在目录里的对话引用也受保护。损坏或未知的消息存储会阻止自动判断附件归属。
- 已核对 guardian_review_feedback、thread_read_receipts、queued_thread_revisions，以及本地 catalog 的 local_thread_catalog_scan_entries、live_visualization_suggestions。带 host_id 的 catalog 表仅处理 local；远端行保留。
- 所有受检查数据库均核对已知必需列及未知 thread_id 引用。未知表或新数据库仍引用目标时，预览直接停止；删除验证也不会跳过这些引用。自动化配置等不属于已验证可删记录的引用继续阻止删除。
- 两份全局状态支持本地置顶迁移列表和目标对话自己的 thread-tab-routes-v1 状态；不删除项目定义或远端迁移列表。其他未知引用在预览时阻止。
- 预览对待删 JSONL、相关状态文件和数据库及其 WAL 计算内容指纹，附件预览包含内容哈希。事务快照准备后再次核查数据和附件归属；首次修改前发现变化时保留新内容并要求重新预览。

当前删除仍由本项目独立实现；后续优先评估官方 thread/delete，并验证其余本地残留的处理边界。导出包只是副本，不能冒充 Codex 归档或完整状态恢复。
