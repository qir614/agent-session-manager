from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(slots=True)
class AppSettings:
    backup_before_delete: bool = False
    backup_directory: str = ""
    include_state_in_backups: bool = False
    show_internal_messages: bool = False
    language: str = "zh-CN"
    theme: str = "system"
    codex_binary: str = ""
    confirmation_text: str = "DELETE"
    backup_retention_count: int = 0
    backup_retention_days: int = 0
    backup_retention_mib: int = 0


def settings_path() -> Path:
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "CodexLocalSessionCleaner" / "settings.json"


def load_settings(path: Path | None = None) -> AppSettings:
    target = path or settings_path()
    if not target.is_file():
        return AppSettings()
    try:
        value = json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return AppSettings()
    if not isinstance(value, dict):
        return AppSettings()
    defaults = AppSettings()
    allowed = asdict(defaults)
    for key, default in allowed.items():
        candidate = value.get(key, default)
        if isinstance(candidate, type(default)):
            setattr(defaults, key, candidate)
    if defaults.language not in {"zh-CN", "en"}:
        defaults.language = "zh-CN"
    if defaults.theme not in {"system", "light", "dark"}:
        defaults.theme = "system"
    defaults.backup_retention_count = max(0, defaults.backup_retention_count)
    defaults.backup_retention_days = max(0, defaults.backup_retention_days)
    defaults.backup_retention_mib = max(0, defaults.backup_retention_mib)
    return defaults


def save_settings(settings: AppSettings, path: Path | None = None) -> Path:
    target = path or settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix="settings.", suffix=".tmp", dir=target.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            json.dump(asdict(settings), stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, target)
    except Exception:
        temp.unlink(missing_ok=True)
        raise
    return target
