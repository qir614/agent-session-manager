from __future__ import annotations

import json
import os
import hashlib
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


AUDIT_FILE_NAME = ".agent-session-manager-operations.jsonl"
LEGACY_AUDIT_FILE_NAMES = (".codex-local-session-cleaner-operations.jsonl",)
AUDIT_FILE_NAMES = (AUDIT_FILE_NAME, *LEGACY_AUDIT_FILE_NAMES)
UUID_RE = re.compile(
    r"(?i)(?<![0-9a-f])([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?![0-9a-f])"
)


def _anonymize(value: Any) -> Any:
    if isinstance(value, str):
        return UUID_RE.sub(
            lambda match: "thread#" + hashlib.sha256(match.group(1).lower().encode()).hexdigest()[:16],
            value,
        )
    if isinstance(value, list):
        return [_anonymize(item) for item in value]
    if isinstance(value, dict):
        return {str(_anonymize(key)): _anonymize(item) for key, item in value.items()}
    return value


def audit_paths(codex_home: Path) -> tuple[Path, ...]:
    """Return the current journal path followed by legacy journal paths.

    Legacy journals stay readable so upgrades keep their deletion attestations;
    new records are written only to AUDIT_FILE_NAME.
    """
    return tuple(codex_home / name for name in AUDIT_FILE_NAMES)


def _remove_from_file(path: Path, deletion_ids: set[str]) -> int:
    if not path.is_file():
        return 0
    kept: list[str] = []
    removed = 0
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        lowered = line.lower()
        if any(session_id in lowered for session_id in deletion_ids):
            removed += 1
        elif line.strip():
            kept.append(line)
    descriptor, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write("\n".join(kept) + ("\n" if kept else ""))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    except Exception:
        temp.unlink(missing_ok=True)
        raise
    return removed


def remove_operation_records(codex_home: Path, deletion_ids: set[str]) -> int:
    if not deletion_ids:
        return 0
    return sum(_remove_from_file(path, deletion_ids) for path in audit_paths(codex_home))


def write_operation(codex_home: Path, event: str, **values: Any) -> None:
    record = _anonymize({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **values,
    })
    path = codex_home / AUDIT_FILE_NAME
    try:
        with path.open("a", encoding="utf-8", newline="") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        # The cleanup result remains authoritative even if optional diagnostics
        # cannot be persisted (for example because the home became read-only).
        return
