from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .platforms import get_platform
from .models import CleanupPlan, ScanResult


UUID_RE = re.compile(
    r"(?i)(?<![0-9a-f])([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?![0-9a-f])"
)
ATTACHMENT_RE = re.compile(
    r"(?i)(?:^|[\\/])attachments[\\/]"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
)
THREAD_PRIVATE_ROOTS = (
    "memories", ".tmp", "tmp", "cache", "browser", "computer-use", "node_repl"
)
ARTIFACT_ROOTS = ("attachments", "visualizations", "generated_images") + THREAD_PRIVATE_ROOTS
TEXT_SUFFIXES = {".json", ".jsonl", ".md", ".txt", ".log", ".toml", ".yaml", ".yml"}


@dataclass(slots=True)
class ArtifactAnalysis:
    deletable_paths: list[str] = field(default_factory=list)
    shared_paths: list[str] = field(default_factory=list)
    project_paths: list[str] = field(default_factory=list)
    backup_paths: list[str] = field(default_factory=list)
    unknown_reference_files: list[str] = field(default_factory=list)
    deletable_files: int = 0
    deletable_bytes: int = 0
    attachment_directories: int = 0
    visualization_directories: int = 0
    generated_image_directories: int = 0
    fingerprint: str = ""


def _is_reparse(path: Path) -> bool:
    return get_platform().is_reparse_point(path)

def _safe_managed_path(codex_home: Path, relative: str) -> Path:
    root = codex_home.expanduser().resolve()
    raw = Path(relative)
    if raw.is_absolute() or ".." in raw.parts or not raw.parts:
        raise ValueError(f"Artifact path escapes Codex home or is not a safe relative path: {relative}")
    current = root
    for part in raw.parts:
        current = current / part
        if current.is_symlink() or (current.exists() and _is_reparse(current)):
            raise ValueError(f"Artifact path contains a reparse point: {relative}")
    candidate = (root / raw).resolve(strict=False)
    try:
        common = Path(os.path.commonpath((root, candidate)))
    except ValueError as exc:
        raise ValueError(f"Artifact path escapes Codex home: {relative}") from exc
    if common != root or candidate == root:
        raise ValueError(f"Artifact path escapes Codex home: {relative}")
    rel = candidate.relative_to(root)
    if not rel.parts or rel.parts[0] not in ARTIFACT_ROOTS:
        raise ValueError(f"Artifact path is outside managed roots: {relative}")
    current = root / rel.parts[0]
    for part in rel.parts[1:]:
        if current.exists() and _is_reparse(current):
            raise ValueError(f"Artifact path contains a reparse point: {relative}")
        current = current / part
    if candidate.exists() and _is_reparse(candidate):
        raise ValueError(f"Artifact path is a reparse point: {relative}")
    return candidate


def _strings(value: object):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)


def _attachment_ids(path: Path) -> set[str]:
    result: set[str] = set()
    if not path.is_file():
        return result
    with path.open("r", encoding="utf-8-sig") as stream:
        for line in stream:
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                if not line.strip():
                    continue
                raise ValueError(f"Cannot establish attachment ownership: invalid rollout {path}") from exc
            for text in _strings(value):
                result.update(match.group(1).lower() for match in ATTACHMENT_RE.finditer(text))
    return result


def attachment_references(scan: ScanResult) -> dict[str, set[str]]:
    references: dict[str, set[str]] = {}
    for sid, record in scan.sessions.items():
        references[sid] = set().union(*(_attachment_ids(path) for path in record.paths))
    from .storage import locations
    storage = locations(scan.codex_home)
    sources = (
        (storage.sqlite / "thread_history_1.sqlite", {"thread_items", "thread_realtime_items", "thread_turns", "thread_history_projection_state"}, False),
        (storage.sqlite / "state_5.sqlite", {"thread_attachments"}, True),
        (scan.codex_home / "sqlite/state_5.sqlite", {"thread_attachments"}, True),
    )
    visited: set[Path] = set()
    for path, wanted, attachment_metadata in sources:
        if not path.is_file() or path.resolve() in visited:
            continue
        visited.add(path.resolve())
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
        try:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not attachment_metadata:
                unknown = tables - {"thread_items", "thread_realtime_items", "thread_turns", "thread_history_projection_state", "_sqlx_migrations", "sqlite_sequence"}
                if unknown:
                    raise ValueError("Cannot establish attachment ownership: unsupported history tables: " + ", ".join(sorted(unknown)))
            for table in sorted(tables & wanted):
                columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
                if "thread_id" not in columns:
                    raise ValueError(f"Cannot establish attachment ownership: {table}.thread_id is missing")
                payload = [name for name in columns if name != "thread_id"]
                quoted = ",".join('"' + name.replace('"', '""') + '"' for name in ["thread_id", *payload])
                for row in connection.execute(f'SELECT {quoted} FROM "{table}"'):
                    sid = str(row[0] or "").lower()
                    refs = references.setdefault(sid, set())
                    for column, value in zip(payload, row[1:]):
                        if not isinstance(value, str):
                            if isinstance(value, bytes):
                                raise ValueError(f"Cannot establish attachment ownership: binary {table}.{column}")
                            continue
                        refs.update(match.group(1).lower() for match in ATTACHMENT_RE.finditer(value))
                        # 附件元数据的标识和正文可能使用不同字段，均需保护。
                        if attachment_metadata and UUID_RE.fullmatch(value):
                            refs.add(value.lower())
                        if column.endswith("json") or value.lstrip().startswith(("{", "[")):
                            try:
                                decoded = json.loads(value)
                            except json.JSONDecodeError as exc:
                                raise ValueError(f"Cannot establish attachment ownership: invalid {table}.{column}") from exc
                            for text in _strings(decoded):
                                refs.update(match.group(1).lower() for match in ATTACHMENT_RE.finditer(text))
                                if attachment_metadata and UUID_RE.fullmatch(text):
                                    refs.add(text.lower())
        finally:
            connection.close()
    return references


def _tree_stats(path: Path) -> tuple[int, int]:
    if path.is_file():
        return 1, path.stat().st_size
    files = 0
    size = 0
    for current, directories, names in os.walk(path, followlinks=False):
        current_path = Path(current)
        safe_directories: list[str] = []
        for name in directories:
            child = current_path / name
            if _is_reparse(child):
                raise ValueError(f"Artifact tree contains a reparse point: {child}")
            safe_directories.append(name)
        directories[:] = safe_directories
        for name in names:
            item = current_path / name
            if _is_reparse(item):
                raise ValueError(f"Artifact tree contains a reparse point: {item}")
            files += 1
            size += item.stat().st_size
    return files, size


def _matching_id_directories(root: Path, deletion_ids: set[str]) -> list[Path]:
    if not root.is_dir():
        return []
    result: list[Path] = []
    for current, directories, _files in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in list(directories):
            candidate = current_path / name
            if _is_reparse(candidate):
                directories.remove(name)
                continue
            if name.lower() in deletion_ids:
                result.append(candidate)
                directories.remove(name)
    return result


def _thread_private_candidates(
    codex_home: Path, deletion_ids: set[str]
) -> tuple[list[Path], list[str]]:
    deletable: list[Path] = []
    unresolved: list[str] = []
    needles = tuple(value.encode("ascii") for value in sorted(deletion_ids))
    for root_name in THREAD_PRIVATE_ROOTS:
        root = codex_home / root_name
        if not root.is_dir():
            continue
        matched_directories = _matching_id_directories(root, deletion_ids)
        deletable.extend(matched_directories)
        matched_roots = [item.resolve() for item in matched_directories]
        for path in root.rglob("*"):
            if not path.is_file() or _is_reparse(path):
                continue
            resolved = path.resolve()
            if any(parent == resolved or parent in resolved.parents for parent in matched_roots):
                continue
            lowered_name = path.name.lower()
            if any(session_id in lowered_name for session_id in deletion_ids):
                deletable.append(path)
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if path.suffix.lower() not in TEXT_SUFFIXES or size > 16 * 1024 * 1024:
                continue
            try:
                content = path.read_bytes()
            except OSError:
                continue
            if any(needle in content.lower() for needle in needles):
                unresolved.append(str(path.relative_to(codex_home)).replace("\\", "/"))
    return deletable, sorted(set(unresolved))


def _managed_backups_with_ids(codex_home: Path, deletion_ids: set[str]) -> list[str]:
    found: list[str] = []
    root = codex_home / "session_manager_backups"
    if root.is_dir():
        for manifest_path in root.glob("*/manifest.json"):
            try:
                value = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            ids = {
                str(item).lower()
                for key in ("session_ids", "deletion_ids", "requested_ids")
                for item in (value.get(key) or [])
            }
            if ids & deletion_ids:
                found.append(str(manifest_path.parent.relative_to(codex_home)))
    db_root = codex_home / "db-backups"
    if db_root.is_dir():
        placeholders = ",".join("?" for _ in deletion_ids)
        values = tuple(sorted(deletion_ids))
        for path in db_root.rglob("*.sqlite"):
            try:
                connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
                try:
                    tables = {row[0] for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )}
                    if "threads" in tables:
                        count = int(connection.execute(
                            f"SELECT COUNT(*) FROM threads WHERE id IN ({placeholders})", values
                        ).fetchone()[0])
                        if count:
                            found.append(str(path.relative_to(codex_home)))
                finally:
                    connection.close()
            except (OSError, sqlite3.Error):
                continue
    return sorted(set(found))


def analyze_artifacts(scan: ScanResult, plan: CleanupPlan) -> ArtifactAnalysis:
    deletion = set(plan.deletion_ids)
    deleted_attachment_ids: set[str] = set()
    retained_attachment_ids: set[str] = set()
    for session_id, refs in attachment_references(scan).items():
        if session_id in deletion:
            deleted_attachment_ids.update(refs)
        else:
            retained_attachment_ids.update(refs)

    deletable: list[Path] = []
    shared: list[str] = []
    attachments_root = scan.codex_home / "attachments"
    for attachment_id in sorted(deleted_attachment_ids):
        relative = f"attachments/{attachment_id}"
        candidate = _safe_managed_path(scan.codex_home, relative)
        if attachment_id in retained_attachment_ids:
            shared.append(relative)
        elif candidate.is_dir():
            deletable.append(candidate)

    visualizations = _matching_id_directories(
        scan.codex_home / "visualizations", deletion
    )
    generated = _matching_id_directories(
        scan.codex_home / "generated_images", deletion
    )
    deletable.extend(visualizations)
    deletable.extend(generated)
    private_candidates, unresolved = _thread_private_candidates(scan.codex_home, deletion)
    deletable.extend(private_candidates)

    unique: dict[str, Path] = {}
    for path in deletable:
        relative = path.resolve().relative_to(scan.codex_home.resolve()).as_posix()
        _safe_managed_path(scan.codex_home, relative)
        unique[relative] = path
    file_count = 0
    byte_count = 0
    fingerprint_items: list[str] = []
    for relative, path in sorted(unique.items()):
        files, size = _tree_stats(path)
        file_count += files
        byte_count += size
        fingerprint_items.append(f"{relative}:{files}:{size}:{path.stat().st_mtime_ns}")
        from .backup import sha256_file
        for source in artifact_files(scan.codex_home, [relative]):
            before = source.stat()
            digest = sha256_file(source)
            after = source.stat()
            if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
                raise RuntimeError("Conversation artifacts changed during preview; scan again.")
            fingerprint_items.append(source.relative_to(scan.codex_home).as_posix() + ":" + digest)

    projects = sorted({
        scan.sessions[sid].project_path or scan.sessions[sid].cwd
        for sid in deletion if sid in scan.sessions
        if (scan.sessions[sid].project_path or scan.sessions[sid].cwd)
    })
    backups = _managed_backups_with_ids(scan.codex_home, deletion)
    digest = hashlib.sha256(
        "\n".join(fingerprint_items + shared + unresolved).encode()
    ).hexdigest()
    return ArtifactAnalysis(
        deletable_paths=sorted(unique),
        shared_paths=sorted(shared),
        project_paths=projects,
        backup_paths=backups,
        unknown_reference_files=unresolved,
        deletable_files=file_count,
        deletable_bytes=byte_count,
        attachment_directories=sum(path.startswith("attachments/") for path in unique),
        visualization_directories=len(visualizations),
        generated_image_directories=len(generated),
        fingerprint=digest,
    )


def artifact_files(codex_home: Path, relative_paths: list[str]) -> list[Path]:
    result: list[Path] = []
    for relative in relative_paths:
        candidate = _safe_managed_path(codex_home, relative)
        if candidate.is_file():
            result.append(candidate)
        elif candidate.is_dir():
            for current, directories, files in os.walk(candidate, followlinks=False):
                current_path = Path(current)
                for name in list(directories):
                    if _is_reparse(current_path / name):
                        raise ValueError(f"Artifact tree contains a reparse point: {current_path / name}")
                for name in files:
                    item = current_path / name
                    if _is_reparse(item):
                        raise ValueError(f"Artifact tree contains a reparse point: {item}")
                    result.append(item)
    return sorted(set(result))


def delete_artifacts(codex_home: Path, relative_paths: list[str]) -> list[str]:
    deleted: list[str] = []
    paths = sorted(relative_paths, key=lambda value: len(Path(value).parts), reverse=True)
    for relative in paths:
        target = _safe_managed_path(codex_home, relative)
        if target.is_dir():
            shutil.rmtree(target)
        elif target.is_file():
            target.unlink()
        if target.exists():
            raise OSError(f"Artifact still exists after deletion: {target}")
        deleted.append(relative)
    for root_name in ARTIFACT_ROOTS:
        root = codex_home / root_name
        if not root.is_dir():
            continue
        for current, directories, _files in os.walk(root, topdown=False):
            for name in directories:
                directory = Path(current) / name
                if not _is_reparse(directory):
                    try:
                        directory.rmdir()
                    except OSError:
                        pass
    return deleted
