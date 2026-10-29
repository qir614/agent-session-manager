"""Configuration is split by scope: general, Codex, and DSH.

Each section lives in its own module so an adapter only imports what it owns,
while the store module owns the on-disk format and legacy migration.
"""
from __future__ import annotations

from .codex import CodexSettings
from .dsh import DshSettings
from .general import GeneralSettings, normalize_general
from .store import (
    SCHEMA_VERSION,
    AppSettings,
    load_settings,
    save_settings,
    settings_path,
)

__all__ = [
    "AppSettings",
    "CodexSettings",
    "DshSettings",
    "GeneralSettings",
    "SCHEMA_VERSION",
    "load_settings",
    "normalize_general",
    "save_settings",
    "settings_path",
]
