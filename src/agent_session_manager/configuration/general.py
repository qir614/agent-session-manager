"""Settings shared by every application adapter and operating system."""
from __future__ import annotations

from dataclasses import dataclass

LANGUAGES = ("zh-CN", "en")
THEMES = ("system", "light", "dark")


@dataclass(slots=True)
class GeneralSettings:
    """Cross-application preferences that describe how the manager behaves."""

    backup_before_delete: bool = False
    backup_directory: str = ""
    include_state_in_backups: bool = False
    show_internal_messages: bool = False
    language: str = "zh-CN"
    theme: str = "system"
    confirmation_text: str = "DELETE"
    backup_retention_count: int = 0
    backup_retention_days: int = 0
    backup_retention_mib: int = 0


def normalize_general(general: GeneralSettings) -> GeneralSettings:
    """Clamp values that are unsafe or meaningless when loaded from disk."""
    if general.language not in LANGUAGES:
        general.language = "zh-CN"
    if general.theme not in THEMES:
        general.theme = "system"
    if not general.confirmation_text.strip():
        general.confirmation_text = "DELETE"
    general.backup_retention_count = max(0, general.backup_retention_count)
    general.backup_retention_days = max(0, general.backup_retention_days)
    general.backup_retention_mib = max(0, general.backup_retention_mib)
    return general
