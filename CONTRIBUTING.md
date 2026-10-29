# Contributing

Contributions are welcome, especially synthetic compatibility fixtures for newer Codex storage formats.

## Development

Linux: activate the existing `codex-cleaner` conda environment and run `bash linux/test.sh -v`. Temporary fixtures stay under the project's `.review-tmp/`. Launch with `bash linux/start.sh`.

Windows instructions, launcher and build script live in [windows/README.md](windows/README.md).

线程字段语义、官方协议与内部实现证据见 [THREAD_FORMAT.md](THREAD_FORMAT.md)。修改分类或级联删除前先核对该文档，并补充独立的父子关系测试。

## Code boundaries

- `linux/` and `windows/` contain platform-specific startup, build assets and documentation.
- `src/agent_session_manager/platforms/` owns configuration paths, native process enumeration, desktop commands, font preferences, themes and symlink/reparse detection. Only its selector chooses the runtime platform; do not add `os.name` branches to shared UI or business code.
- Scanning, planning, cleanup, backup, storage formats and responsive UI behavior remain shared. Portable backup manifest validation belongs to the shared core.
- Add platform-only tests to `tests/linux/` or `tests/windows/`. Use synthetic fixtures for shared behavior; do not execute the other OS's native commands.


Keep runtime dependencies in the Python standard library unless a dependency provides a clear safety benefit. New destructive paths must include a dry-run plan, exact target validation, explicit backup behavior, failure handling, process guards, and isolated tests. UI-only changes should remain usable with keyboard navigation and must not read every complete rollout during the initial scan.

Never commit real `.codex` rollouts, SQLite databases, global state, session IDs, project paths, or conversation excerpts. Use UUID-shaped synthetic IDs and temporary directories.
