"""Documented Codex locations. Never infer an external directory from filenames."""
from __future__ import annotations
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StorageLocations:
    home: Path
    sqlite: Path
    logs: Path


def locations(home: Path) -> StorageLocations:
    home = home.expanduser().resolve()
    config = home / "config.toml"
    values = tomllib.loads(config.read_text(encoding="utf-8")) if config.is_file() else {}
    def configured(name, fallback):
        value = values.get(name)
        if value is None:
            return fallback
        if not isinstance(value, str) or not Path(value).expanduser().is_absolute():
            raise ValueError(f"Unsupported {name}: use an absolute path; no writes are allowed.")
        return Path(value).expanduser().resolve()
    sqlite = Path(os.environ.get("CODEX_SQLITE_HOME") or home).expanduser().resolve()
    return StorageLocations(home, configured("sqlite_home", sqlite), configured("log_dir", home / "log"))
