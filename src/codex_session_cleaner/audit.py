from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


AUDIT_FILE_NAME = ".codex-local-session-cleaner-operations.jsonl"


def write_operation(codex_home: Path, event: str, **values: Any) -> None:
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **values,
    }
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
