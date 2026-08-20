from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from . import __version__
from .backup import create_manual_backup
from .backup_manager import delete_backup, list_backups
from .codex_controller import CodexController
from .conversation_reader import read_conversation
from .executor import CleanupExecutor, restore_backup
from .models import SessionStatus
from .maintenance import compact_log_databases, log_database_stats
from .impact_analyzer import analyze_deletion
from .planner import build_plan
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
        "codex_home": str(scan.codex_home),
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
            }
            for record in scan.sessions.values()
        ],
    }


def _print_scan(scan) -> None:
    print(f"Codex home: {scan.codex_home}")
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
        prog="codex-session-cleaner",
        description="Inspect and safely clean local Codex sessions with dependency cascading.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--codex-home", type=Path, default=default_codex_home())
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

    delete = sub.add_parser("delete", help="Apply a deletion plan; persistent backup is opt-in.")
    delete.add_argument("ids", nargs="+")
    delete.add_argument("--apply", action="store_true")
    delete.add_argument("--confirm", default="")
    delete.add_argument("--backup", action="store_true", help="Keep a persistent backup before deletion.")
    delete.add_argument("--backup-dir", type=Path)

    restore = sub.add_parser("restore", help="Restore a cleanup backup.")
    restore.add_argument("backup_dir", type=Path)
    restore.add_argument("--force", action="store_true")

    sub.add_parser("gui", help="Open the graphical interface.")
    compact = sub.add_parser("compact-logs", help="Release free pages in log databases.")
    compact.add_argument("--apply", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "gui":
        from .gui import launch

        launch(args.codex_home)
        return 0
    if args.command == "restore":
        count = restore_backup(args.backup_dir, args.codex_home, args.force)
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

    scan = SessionScanner(args.codex_home).scan()
    if args.command == "scan":
        if args.as_json:
            print(json.dumps(_scan_payload(scan), ensure_ascii=False, indent=2))
        else:
            _print_scan(scan)
        return 2 if any(item.blocking for item in scan.issues) else 0

    if args.command == "show":
        record = scan.sessions.get(args.id.lower())
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
            "size": record.size,
            "warnings": record.warnings,
        }
        if args.messages and record.path is not None:
            details = read_conversation(record.path, include_internal=args.internal)
            payload["messages"] = [
                {"role": item.role, "timestamp": item.timestamp, "text": item.text}
                for item in details.messages
            ]
            payload["event_type_counts"] = details.event_type_counts
            payload["invalid_json_lines"] = details.invalid_line_count
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.command == "backup":
        backup_dir, _manifest = create_manual_backup(
            scan,
            selected_ids=[value.lower() for value in args.ids],
            destination=args.destination,
            include_state=False,
        )
        print(f"Backup: {backup_dir}")
        return 0

    if args.command in {"rename", "archive", "unarchive"}:
        controller = CodexController(args.codex_home, args.codex_bin)
        if args.command == "rename":
            controller.rename(args.id.lower(), args.title)
        else:
            controller.set_archived(args.id.lower(), args.command == "archive")
        print(f"Completed: {args.command} {args.id}")
        return 0

    plan = build_plan(scan, args.ids)
    impact = analyze_deletion(scan, plan)
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
    report = CleanupExecutor().execute(
        plan,
        persistent_backup=args.backup,
        backup_directory=args.backup_dir,
    )
    print(f"Deleted {len(report.deleted_ids)} sessions.")
    print(f"Backup: {report.backup_dir or 'not created'}")
    if report.manifest_path:
        print(f"Manifest: {report.manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
