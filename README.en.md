# Codex Local Session Manager

A Windows-first viewer, archive, backup, and cleaner for Codex local conversations. Version 0.6.0 adds complete log rollback, strict backup-path containment, a backup manager, and per-store deletion impact previews.

[中文](README.md)

> This is not an official OpenAI product. Unknown references, damaged databases, or post-delete remnants fail closed and are reported by store.

## Run the EXE

Download `CodexLocalSessionManager.exe` and its optional `.sha256` from GitHub Releases, then double-click it. The default home is `%USERPROFILE%\.codex`. The unsigned build may trigger SmartScreen; use only files from this repository.

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

Use **Select all results**, **Invert**, or **Clear selection**. Selecting a project row selects its user-root conversations. Deleting a parent always includes every ordinary subagent and guardian identified by explicit parent metadata.

## Delete conversations

Viewing, searching, rename, archive, and session export work while Codex is running. Multi-store deletion, state restore, backup deletion, and database maintenance require Codex/ChatGPT and background app-server processes to be closed.

The default GUI confirmation is `DELETE`. Settings can replace it with custom text or a template such as `DELETE-{count}`. Persistent pre-delete backup remains disabled by default.

A complete deletion removes the selected dependency closure from rollout files, primary and legacy state databases, name history, known global-state mappings, local-host catalog rows, projected history, goals, queue, memory, snapshots, summaries, and thread-associated rows in both log databases.

Before confirmation, the GUI reports user roots, subagents, ghosts, rollout bytes, per-database references, associated log rows, and estimated transaction-snapshot space. The cleaner re-queries every core store after deletion. Any failure restores rollout, state, index, and log databases from a temporary transaction snapshot. That snapshot is destroyed after success and is not a persistent backup. A content-free operation journal records IDs, row counts, and error types in `.codex-local-session-cleaner-operations.jsonl`.

Project source directories and pasted attachments are never deleted automatically. Session visualization and generated-image outputs are also preserved by default because they may be valuable artifacts.

## Release database free space

Deleting SQLite rows does not shrink the file. The GUI's **Release database free space** action is separate from conversation deletion. While Codex is closed, it previews reclaimable pages, builds a compact copy with `VACUUM INTO`, verifies integrity and unchanged log row counts, then atomically replaces the original. This removes no valid logs. A roughly 1 GB database with about 91% free pages can typically shrink to about 90 MB.

## Session archives, state snapshots, and backup management

**Archive selected** stores the selected parent/descendant rollouts and a verified manifest. It does not copy complete state databases and cannot overwrite the current Codex state. This prevents one old conversation from silently rolling back unrelated newer conversations.

When persistent pre-delete backup is enabled, cleanup creates a restorable state snapshot. Restoring one requires explicit `RESTORE` or `--force` confirmation and creates a temporary guard snapshot first. A failed restore returns to the pre-restore state.

The GUI's **Backup manager** lists, verifies, restores, and safely deletes managed backups. It supports multi-delete, incomplete-backup cleanup, and count, age, and space retention rules. Deletion is limited to direct managed children of the configured backup root and refuses symlinks, junctions, the root itself, nested paths, and unrelated directories.

Manifest entries reject absolute, drive, UNC, and `..` paths. Sources must remain inside the backup root and restore targets inside the selected Codex home. Hash or SQLite integrity failures block restore.

## Source and CLI

Run with Python 3.10+ including Tcl/Tk:

```powershell
python run_gui.py
python run_cli.py scan
python run_cli.py show <SESSION_ID> --messages
python run_cli.py plan <SESSION_ID>
python run_cli.py backup <SESSION_ID> --destination D:\codex-backups
python run_cli.py backups list --root D:\codex-backups
python run_cli.py backups verify D:\codex-backups\backup_before_session_cleanup_...
python run_cli.py backups delete D:\codex-backups\backup_codex_sessions_... --root D:\codex-backups
python run_cli.py delete <SESSION_ID> --apply --confirm DELETE-3
python run_cli.py compact-logs
python run_cli.py compact-logs --apply
```

Build the EXE with `.\build_windows.ps1 -Clean`. Run tests with `python -m unittest discover -s tests -v`. Tests use isolated fixtures or temporary consistent copies and never delete real conversations. The tool does not upload sessions or expose credentials from `auth.json`.

## License

[MIT](LICENSE)
