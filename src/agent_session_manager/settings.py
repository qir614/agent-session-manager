"""Backward-compatible facade; the implementation lives in configuration/."""
from __future__ import annotations

from .configuration import (
    SCHEMA_VERSION,
    AppSettings,
    load_settings,
    save_settings,
    settings_path,
)

__all__ = [
    "AppSettings",
    "SCHEMA_VERSION",
    "load_settings",
    "save_settings",
    "settings_path",
]
