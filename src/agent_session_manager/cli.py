from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from . import __version__
from .applications import get_application
from .configuration import SCHEMA_VERSION, load_settings, settings_path
from .backup import create_manual_backup
from .backup_manager import delete_backup, list_backups
from .codex_controller import CodexController
from .conversation_reader import read_conversation
from .executor import CleanupExecutor, recover_transaction, restore_backup
from .models import SessionStatus
from .platforms import get_platform
from .maintenance import compact_log_databases, log_database_stats
from .impact_analyzer import analyze_deletion
from .orphan_cleaner import execute_orphan_cleanup, scan_orphans
from .planner import build_plan
from .recovery import pending_transactions
from .scanner import SessionScanner, default_codex_home


def format_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{value} B"


def _scan_payload(scan) -> dict:
    counts = {status.value: len(scan.ids_with_status(status)) for status in SessionStatus}
    return {
        "application": scan.application,
        "home": str(scan.codex_home),
        "total": len(scan.sessions),
        "counts": counts,
        "sqlite_integrity": scan.sqlite_integrity,
        "issues": [
            {"path": item.path, "code": item.code, "message": item.message, "blocking": item.blocking}
            for item in scan.issues
        ],
        "sessions": [
            {
                "id": record.id,
                "title": record.title,
                "status": scan.status_by_id[record.id].value,
                "parent_id": scan.parent_by_child.get(record.id),
                "size": record.size,
                "path": str(record.path) if record.path else None,
                "segment_count": record.segment_count,
                "paths": [str(path) for path in record.paths],
            }
            for record in scan.sessions.values()
        ],
    }


def _print_scan(scan) -> None:
    print(f"{scan.application.upper()} home: {scan.codex_home}")
    print(f"Sessions:   {len(scan.sessions)}")
    for status in SessionStatus:
        print(f"  {status.value:18} {len(scan.ids_with_status(status))}")
    print(f"SQLite:     {scan.sqlite_integrity}")
    if scan.issues:
        print(f"Issues:     {len(scan.issues)}")
        for issue in scan.issues[:20]:
            marker = "BLOCK" if issue.blocking else "WARN"
            print(f"  [{marker}] {issue.code}: {issue.message} ({issue.path})")


def _print_plan(plan) -> None:
    print(f"Requested roots: {len(plan.requested_ids)}")
    print(f"Cascade total:   {len(plan.deletion_ids)}")
    print(f"Files:           {plan.file_count}")
    print(f"Bytes:           {format_bytes(plan.total_bytes)}")
    print(f"Fingerprint:     {plan.fingerprint}")
    for item in plan.items:
        print(f"  {item.id}  {item.reason}  {item.relative_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-session-manager",
        description="Inspect and clean local Codex or DSH sessions through independent application adapters.",
    )
    parser.add_argument("--version", action="version", version=f"agent-session-manager {__version__}")
    parser.add_argument("--app", choices=("codex", "dsh"), default="codex")
    parser.add_argument("--home", "--codex-home", dest="codex_home", type=Path, metavar="HOME")
    parser.add_argument("--sessions-root", type=Path, help="DSH persistence root; defaults to HOME/sessions in the shipped composition.")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="Scan without changing files.")
    scan.add_argument("--json", action="store_true", dest="as_json")

    plan = sub.add_parser("plan", help="Preview a dependency-aware deletion.")
    plan.add_argument("ids", nargs="+")
    plan.add_argument("--json", action="store_true", dest="as_json")

    show = sub.add_parser("show", help="Show one session and its dependencies.")
    show.add_argument("id")
    show.add_argument("--messages", action="store_true")
    show.add_argument("--internal", action="store_true")

    backup = sub.add_parser("backup", help="Create an export-only archive of selected sessions.")
    backup.add_argument("ids", nargs="*")
    backup.add_argument("--destination", type=Path, required=True)
    backup.add_argument("--no-state", action="store_true", help=argparse.SUPPRESS)

    backups = sub.add_parser("backups", help="List, verify, or safely delete managed backups.")
    backups.add_argument("action", choices=("list", "verify", "delete", "clean-incomplete"))
    backups.add_argument("path", type=Path, nargs="?")
    backups.add_argument("--root", type=Path)

    rename = sub.add_parser("rename", help="Rename a user conversation through Codex.")
    rename.add_argument("id")
    rename.add_argument("title")
    rename.add_argument("--codex-bin", default="")

    archive = sub.add_parser("archive", help="Archive a user conversation through Codex.")
    archive.add_argument("id")
    archive.add_argument("--codex-bin", default="")

    unarchive = sub.add_parser("unarchive", help="Unarchive a user conversation through Codex.")
    unarchive.add_argument("id")
    unarchive.add_argument("--codex-bin", default="")

    delete = sub.add_parser("delete", help="Clean local sessions and their owned data.")
    delete.add_argument("ids", nargs="+")
    delete.add_argument("--apply", action="store_true")
    delete.add_argument("--confirm", default="")
    delete.add_argument("--backup", action="store_true", help="Keep a persistent backup before deletion.")
    delete.add_argument("--backup-dir", type=Path)

    restore = sub.add_parser("restore", help="Restore a cleanup backup.")
    restore.add_argument("backup_dir", type=Path)
    restore.add_argument("--force", action="store_true")
    restore.add_argument(
        "--overwrite-conflicts", action="store_true",
        help="Restore even though current files differ from the saved baseline.",
    )

    recover = sub.add_parser(
        "recover", help="List or explicitly recover interrupted transactions."
    )
    recover.add_argument("transaction_dir", type=Path, nargs="?")
    recover.add_argument("--force", action="store_true")
    recover.add_argument(
        "--overwrite-conflicts", action="store_true",
        help="Recover even though current files differ from the recorded checkpoint.",
    )

    sub.add_parser("gui", help="Open the graphical interface.")
    config = sub.add_parser("config", help="Show configuration paths and effective values.")
    config.add_argument("--json", action="store_true", dest="as_json")
    compact = sub.add_parser("compact-logs", help="Release free pages in log databases.")
    compact.add_argument("--apply", action="store_true")
    orphans = sub.add_parser("orphans", help="Scan or clean artifacts left by deleted sessions.")
    orphans.add_argument("action", choices=("scan", "clean"))
    orphans.add_argument("--grace-days", type=int, default=7)
    orphans.add_argument("--apply", action="store_true")
    orphans.add_argument("--confirm", default="")
    return parser


def _dispatch(argv: list[str] | None) -> int:
    args = build_parser().parse_args(argv)
    application = get_application(args.app)
    args.codex_home = args.codex_home or application.default_home()
    if args.app == "dsh" and args.command not in {"scan", "show", "plan", "delete", "backup", "gui"}:
        print(f"DSH does not support {args.command}; select --app codex for Codex-specific operations.", file=sys.stderr)
        return 2
    if args.command == "config":
        settings = load_settings()
        payload = {
            "application": args.app,
            "schema_version": SCHEMA_VERSION,
            "settings_path": str(settings_path()),
            "legacy_settings_paths": [str(path) for path in get_platform().legacy_settings_paths()],
            "settings": asdict(settings),
        }
        if args.as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"Application:   {payload['application']}")
            print(f"Schema:        {payload['schema_version']}")
            print(f"Settings file: {payload['settings_path']}")
            for legacy in payload["legacy_settings_paths"]:
                print(f"Legacy file:   {legacy}")
            for key, value in payload["settings"].items():
                print(f"  {key} = {value!r}")
        return 0
    if args.command == "gui":
        from .gui import launch

        launch(args.codex_home, application=args.app, session_root=args.sessions_root)
        return 0
    if args.command == "recover":
        if args.transaction_dir is None:
            print(json.dumps(
                [str(path) for path in pending_transactions(args.codex_home)], indent=2
            ))
            return 0
        count = recover_transaction(
            args.transaction_dir, args.codex_home,
            force=args.force, overwrite_conflicts=args.overwrite_conflicts,
        )
        print(f"Recovered {count} files.")
        return 0
    if args.command == "restore":
        count = restore_backup(
            args.backup_dir, args.codex_home, args.force,
            overwrite_conflicts=args.overwrite_conflicts,
        )
        print(f"Restored {count} files.")
        return 0
    if args.command == "compact-logs":
        stats = log_database_stats(args.codex_home)
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        if not args.apply:
            print("Dry run only. Re-run with --apply while Codex is closed.")
            return 0
        result = compact_log_databases(args.codex_home)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "orphans":
        result = scan_orphans(args.codex_home, max(0, args.grace_days))
        payload = asdict(result)
        payload["home"] = str(result.codex_home)
        payload.pop("codex_home", None)
        payload["file_count"] = result.file_count
        payload["total_bytes"] = result.total_bytes
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        if args.action == "scan":
            return 0
        phrase = f"CLEAN-{len(result.candidates) + len(result.state_reference_ids)}"
        if not args.apply:
            print(f"Dry run only. Re-run with --apply --confirm {phrase}")
            return 0
        if args.confirm != phrase:
            print(f"Refusing cleanup: --confirm must equal {phrase}", file=sys.stderr)
            return 2
        report = execute_orphan_cleanup(result)
        print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
        return 0
    if args.command == "backups":
        root = (args.root or (args.codex_home / "session_manager_backups")).expanduser().resolve()
        records = list_backups(root)
        if args.action == "list":
            print(json.dumps([asdict(item) | {"path": str(item.path)} for item in records],
                             ensure_ascii=False, indent=2))
            return 0
        if args.action == "verify":
            if args.path is None:
                raise SystemExit("backups verify requires a backup path")
            from .backup import verify_backup
            print(json.dumps(verify_backup(args.path), ensure_ascii=False, indent=2))
            return 0
        if args.action == "delete":
            if args.path is None:
                raise SystemExit("backups delete requires a backup path")
            deleted = delete_backup(root, args.path, args.codex_home)
            print(f"Deleted backup and released {format_bytes(deleted)}.")
            return 0
        deleted_count = 0
        released = 0
        for record in records:
            if record.status == "incomplete":
                released += delete_backup(root, record.path, args.codex_home)
                deleted_count += 1
        print(f"Deleted {deleted_count} incomplete backups; released {format_bytes(released)}.")
        return 0

    scan = application.scan(args.codex_home, args.sessions_root)
    if args.command == "scan":
        if args.as_json:
            print(json.dumps(_scan_payload(scan), ensure_ascii=False, indent=2))
        else:
            _print_scan(scan)
        return 2 if any(item.blocking for item in scan.issues) else 0

    if args.command == "show":
        record = scan.sessions.get((args.id if args.app == "dsh" else args.id.lower()))
        if not record:
            print(f"Session not found: {args.id}", file=sys.stderr)
            return 2
        payload = {
            "id": record.id,
            "title": record.title,
            "status": scan.status_by_id[record.id].value,
            "kind": record.kind.value,
            "parent_id": scan.parent_by_child.get(record.id),
            "child_ids": sorted(scan.children.get(record.id, set())),
            "archived": record.archived,
            "cwd": record.cwd,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "path": str(record.path) if record.path else None,
            "paths": [str(path) for path in record.paths],
            "segment_count": record.segment_count,
            "size": record.size,
            "warnings": record.warnings,
        }
        if args.messages and record.path is not None:
            try:
                details = application.read(record, include_internal=args.internal)
            except ValueError as exc:
                print(f"Cannot read session {record.id}: {exc}", file=sys.stderr)
                return 2
            payload["messages"] = [
                {"role": item.role, "timestamp": item.timestamp, "text": item.text}
                for item in details.messages
            ]
            payload["event_type_counts"] = details.event_type_counts
            payload["invalid_json_lines"] = details.invalid_line_count
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.command == "backup":
        backup_dir, _manifest = application.export(
            scan, selected_ids=args.ids if args.app == "dsh" else [value.lower() for value in args.ids],
            destination=args.destination,
        )
        print(f"Backup: {backup_dir}")
        return 0

    if args.command in {"rename", "archive", "unarchive"}:
        controller = CodexController(args.codex_home, args.codex_bin)
        if args.command == "rename":
            controller.rename((args.id if args.app == "dsh" else args.id.lower()), args.title)
        else:
            controller.set_archived((args.id if args.app == "dsh" else args.id.lower()), args.command == "archive")
        print(f"Completed: {args.command} {args.id}")
        return 0

    try:
        plan = application.plan(scan, args.ids)
        impact = application.impact(scan, plan)
    except ValueError as exc:
        print(f"Cannot plan cleanup: {exc}", file=sys.stderr)
        return 2
    if args.command == "plan":
        if args.as_json:
            print(json.dumps({
                "requested_ids": plan.requested_ids,
                "deletion_ids": plan.deletion_ids,
                "file_count": plan.file_count,
                "total_bytes": plan.total_bytes,
                "fingerprint": plan.fingerprint,
                "impact": asdict(impact),
            }, ensure_ascii=False, indent=2))
        else:
            _print_plan(plan)
            print(f"SQLite rows:     {impact.sqlite_reference_total}")
            print(f"Log rows:        {impact.log_reference_total}")
            print(f"Input history:   {impact.history_references}")
            print(f"Private files:   {impact.artifact_files}")
            print(f"Private bytes:   {format_bytes(impact.artifact_bytes)}")
            print(f"Shared retained: {len(impact.shared_artifacts)}")
            print(f"Unresolved refs: {len(impact.unresolved_artifacts)}")
            print(f"Backups retained:{len(impact.retained_backups):>3}")
            for project in impact.preserved_projects:
                print(f"Project kept:    {project}")
            print(f"Rollback space:  {format_bytes(impact.transaction_snapshot_bytes)}")
        return 0

    _print_plan(plan)
    phrase = f"DELETE-{len(plan.deletion_ids)}"
    if not args.apply:
        print(f"Dry run only. Re-run with --apply --confirm {phrase}")
        return 0
    if args.confirm != phrase:
        print(f"Refusing deletion: --confirm must equal {phrase}", file=sys.stderr)
        return 2
    report = application.execute(
        plan,
        persistent_backup=args.backup,
        backup_directory=args.backup_dir,
    )
    print(f"Deleted {len(report.deleted_ids)} sessions.")
    print(f"Deleted owned artifact files: {report.artifact_files_deleted}")
    print(f"Removed local history entries: {report.history_entries_removed}")
    for warning in report.warnings:
        print(warning)
    print(f"Retained shared artifacts: {len(report.retained_shared_artifacts)}")
    print(f"Unresolved artifact references: {len(report.unresolved_artifacts)}")
    print(f"Retained backups containing sessions: {len(report.retained_backups)}")
    for project in report.preserved_projects:
        print(f"Preserved project: {project}")
    print(f"Backup: {report.backup_dir or 'not created'}")
    if report.manifest_path:
        print(f"Manifest: {report.manifest_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return _dispatch(argv)
    except (OSError, RuntimeError, ValueError) as exc:
        # Expected failures (missing backup, pending transaction, refused
        # confirmation, unreadable session) must not reach users as tracebacks.
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
