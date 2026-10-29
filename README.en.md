# Agent Session Manager

A Linux and Windows viewer, archive, backup, and privacy cleaner for Codex local conversations. The project now uses rolling date builds instead of `v0.x.y` display versions. It removes locally managed private artifacts that can be attributed safely while always preserving real project directories.

[中文](README.md)

> This is not an official OpenAI product. Unknown references, damaged databases, or post-delete remnants fail closed and are reported by store.

## Run on your operating system

- Linux: activate the existing `codex-cleaner` conda environment, then run `bash linux/start.sh`. See [linux/README.md](linux/README.md).
- Windows: run `windows/start.bat`, or download the EXE from Releases. See [windows/README.md](windows/README.md).

Platform launchers and documentation are separate. Shared Python entry points remain `run_gui.py` and `run_cli.py`.

## What the interface shows

The tree uses Codex's own `local-projects`, `thread-project-assignments`, and sidebar order before falling back to rollout `cwd`. Explicit renamed titles take precedence in this order:

```text
state.name
→ latest session_index thread_name
→ local catalog display_title
→ state.title
→ first user message
```

Records are classified as user roots, attached subagents, orphan trees, local ghosts, read-only cloud catalog entries, or unknown records. A local ghost is a local catalog/sidebar reference without a corresponding rollout or primary state row. A `chatgpt` host row is displayed for inventory but cannot be deleted locally as if that deleted the server conversation.

One conversation may span multiple sequential rollout files. Compatible shards are grouped by conversation ID and validated against project path, source, and parent metadata. Reading, export, attachment ownership, backup, and deletion cover every shard; an actual metadata conflict blocks mutation and reports the conflicting fields and file paths.

Use **Select all results**, **Invert**, or **Clear selection**. Selecting a project row selects its user-root conversations. Deleting a parent always includes every ordinary subagent and guardian identified by explicit parent metadata.

## Delete conversations

Viewing, searching, rename, archive, and session export work while Codex is running. Multi-store deletion, state restore, backup deletion, and database maintenance require Codex/ChatGPT and background app-server processes to be closed.

The default GUI confirmation is `DELETE`. Settings can replace it with custom text or a template such as `DELETE-{count}`. Persistent pre-delete backup remains disabled by default.

A complete deletion removes the selected dependency closure from rollout files, primary and legacy state databases, name history, known global-state mappings, local-host catalog rows, projected history, goals, queue, memory, snapshots, summaries, and thread-associated rows in both log databases.

Before confirmation, the GUI reports user roots, subagents, rollout bytes, per-database references, associated log rows, exclusive and shared attachments, visualizations, generated images, recoverable backups, preserved project paths, and estimated transaction-snapshot space. Any failure restores rollout, state, indexes, logs, and private artifacts from a temporary transaction snapshot. A content-free operation journal records short thread hashes rather than raw IDs. New records go to `.agent-session-manager-operations.jsonl`; the legacy `.codex-local-session-cleaner-operations.jsonl` remains readable, is kept in state backups, and is handled by legacy-record cleanup, so an upgrade never loses existing deletion attestations.

The cleaner deletes attachment directories referenced only by the selected dependency closure and UUID-keyed visualization or generated-image directories. Shared attachments remain and are reported. Existing backups that can restore the conversation also remain and prevent a misleading "fully purged" result. Real project directories, source files, Git repositories, ordinary output directories, and uploaded originals are never deleted automatically, even when the conversation created the project.

## Release database free space

Conversation deletion now enables SQLite `secure_delete`, truncates WAL files, vacuums modified databases, and runs `quick_check` inside the rollback boundary. The GUI's separate **Release database free space** action remains available for unrelated historical free pages. These measures remove logical-file remnants but cannot promise physical erasure from SSD firmware, operating-system snapshots, or external backups.

The GUI's **Historical residue cleanup** action scans for orphan attachment directories and UUID-keyed visualization, generated-image, browser, cache, and temporary artifacts left by older deletions. Only unreferenced items older than the seven-day grace period and wholly inside managed `.codex` roots are eligible. Recent, unreadable, symlink/junction-containing, ambiguous, and backup paths are report-only. Applying the preview requires `CLEAN-N`, stages artifacts by same-filesystem rename, and redacts obsolete raw thread IDs from the operation journal. Success deletes staging; failed recovery preserves the staging directory and recovery manifest.

## Session archives, state snapshots, and backup management

**Archive selected** stores the selected parent/descendant rollouts and a verified manifest. It does not copy complete state databases and cannot overwrite the current Codex state. This prevents one old conversation from silently rolling back unrelated newer conversations.

When persistent pre-delete backup is enabled, cleanup creates a restorable state snapshot. Restoring one requires explicit `RESTORE` or `--force` confirmation and creates a temporary guard snapshot first. A failed restore returns to the pre-restore state.

The GUI's **Backup manager** lists, verifies, restores, and safely deletes managed backups. It supports multi-delete, incomplete-backup cleanup, and count, age, and space retention rules. Deletion is limited to direct managed children of the configured backup root and refuses symlinks, junctions, the root itself, nested paths, and unrelated directories.

Manifest entries reject absolute, drive, UNC, and `..` paths. Sources must remain inside the backup root and restore targets inside the selected Codex home. Hash or SQLite integrity failures block restore.

## Configuration and data compatibility

Settings live in `%LOCALAPPDATA%\AgentSessionManager\settings.json` on Windows and in `$XDG_CONFIG_HOME/AgentSessionManager/settings.json` on Linux (default `~/.config/AgentSessionManager/settings.json`). The JSON file has a top-level `schema_version` and is split into `general`, `codex`, and `dsh` sections: `general` holds language, theme, confirmation text, internal message display, and pre-delete backup and retention rules; `codex` holds the Codex binary and an optional Codex home; `dsh` holds the DSH home and an optional sessions root. Exact key names are defined by the configuration module in `src/agent_session_manager/configuration/`, shared by the GUI and CLI.

On upgrade, a legacy flat `settings.json` is read and migrated into the sectioned layout; when no readable new-format file exists at the new path, the legacy `CodexLocalSessionCleaner/settings.json` is used as a fallback. Migration happens in memory, legacy files are never deleted or modified, and the new layout is written only on the next save. Corrupt or unknown-version files fall back to defaults without blocking startup.

Journals and backups stay compatible: new operation records go to `.agent-session-manager-operations.jsonl`, while the legacy `.codex-local-session-cleaner-operations.jsonl` remains readable and maintained. Backups now carry the `agent-session-manager` tool marker; older `codex-local-session-cleaner` backups still verify, restore, and appear in backup management. No manual conversion of journals or backups is needed, and legacy files are outside conversation-data cleanup scope.

## Source and CLI

Run with Python 3.11+ including Tcl/Tk:

```powershell
python run_gui.py
python run_cli.py scan
python run_cli.py show <SESSION_ID> --messages
python run_cli.py plan <SESSION_ID>
python run_cli.py backup <SESSION_ID> --destination D:\codex-backups
python run_cli.py backups list --root D:\codex-backups
python run_cli.py backups verify D:\codex-backups\backup_before_session_cleanup_...
python run_cli.py backups delete D:\codex-backups\backup_codex_sessions_... --root D:\codex-backups
python run_cli.py orphans scan --grace-days 7
python run_cli.py orphans clean --grace-days 7 --apply --confirm CLEAN-N
python run_cli.py delete <SESSION_ID> --apply --confirm DELETE-3
python run_cli.py compact-logs
python run_cli.py compact-logs --apply
```

Build the EXE with `.\windows\build.ps1 -Clean`. Run tests with `python -m unittest discover -s tests -v`. Tests use isolated fixtures or temporary consistent copies and never delete real conversations. The tool does not upload sessions or expose credentials from `auth.json`.

## License

[MIT](LICENSE)

## Platform separation

Use [linux/](linux/README.md) for Linux launchers (`start.sh`, `cli.sh`, `test.sh`) and [windows/](windows/README.md) for the Windows launcher, EXE build and version resources. Native desktop behavior lives in `src/agent_session_manager/platforms/linux.py` and `platforms/windows.py`; `platforms/__init__.py` selects only the current operating system. Shared scanning, deletion, backup and UI behavior remains in the main package. Native tests live in `tests/linux/` and `tests/windows/`.

## Documentation and local implementation

Cleanup is implemented locally: scanning, dependency planning, file/SQLite deletion, verification and rollback do not launch Codex CLI or app-server. Existing optional rename/archive integrations are separate. Official documentation informs semantics and configuration; it does not promise a stable private database schema.

`sqlite_home` overrides `CODEX_SQLITE_HOME`. Scanning reads the configured main state database. External database/log locations remain read-only because cross-directory rollback is not supported. Paginated cleanup requires an existing rollout and a validated local history schema, including realtime items. Missing databases, unknown tables and retained fork dependencies block deletion; missing rollouts alone do not prove stale data. Local `history.jsonl` entries are removed only by exact `session_id`, within the rollback boundary. Exports are separate from Codex archive state. This is not a guarantee of complete privacy erasure.

## DSH support

Choose `codex` or `dsh` in the GUI, or pass `--app dsh` to the CLI. `--home` overrides `DSH_HOME`; `--sessions-root` selects a custom persistence directory. The shipped composition uses HOME/sessions. DSH v4 supports browsing, projects, exports and independent Linux cleanup with kernel locks and local rollback. Shared attachments and projects are preserved. Historical/new formats are inspection/export only. Compressed reads need libzstd but never DSH or Node: the reader checks `AGENT_SESSION_MANAGER_ZSTD` (also `DSH_ZSTD_LIBRARY`, `ZSTD_LIBRARY`), then the system library search, then common Git/conda install locations derived from PATH, and only reports an install hint when nothing loads; raw NDJSON sessions are unaffected. Windows DSH deletion is not enabled. See [DSH_FORMAT.md](DSH_FORMAT.md) for official sources, guarantees and limits.

```bash
python -B run_cli.py --app dsh scan
python -B run_cli.py --app dsh --home /path/to/.dsh plan session-ID
```

Deletion previews by default; apply with `--apply --confirm DELETE-N` after closing DSH. No DSH or Codex process executes the cleanup.
