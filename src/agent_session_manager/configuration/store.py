"""Read and write the sectioned configuration document.

The file format separates settings into three scopes:

* ``general`` - behaviour shared by every adapter and operating system;
* ``applications.codex`` - Codex-only preferences;
* ``applications.dsh`` - DSH-only preferences.

Version 1 of the file was a flat mapping of every key at the top level. It is
still read for compatibility and is upgraded to the new layout on the next save.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from ..platforms import get_platform
from .codex import CodexSettings
from .dsh import DshSettings
from .general import GeneralSettings, normalize_general

SCHEMA_VERSION = 2


@dataclass(slots=True)
class AppSettings:
    """Flat, backward-compatible view over the sectioned configuration.

    Callers keep using attribute access (``settings.codex_binary``) while the
    document on disk stores general, Codex, and DSH values in separate scopes.
    """

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
    codex_binary: str = ""
    dsh_home: str = ""
    dsh_session_root: str = ""

    def sections(self) -> tuple[GeneralSettings, CodexSettings, DshSettings]:
        return (
            GeneralSettings(
                backup_before_delete=self.backup_before_delete,
                backup_directory=self.backup_directory,
                include_state_in_backups=self.include_state_in_backups,
                show_internal_messages=self.show_internal_messages,
                language=self.language,
                theme=self.theme,
                confirmation_text=self.confirmation_text,
                backup_retention_count=self.backup_retention_count,
                backup_retention_days=self.backup_retention_days,
                backup_retention_mib=self.backup_retention_mib,
            ),
            CodexSettings(codex_binary=self.codex_binary),
            DshSettings(dsh_home=self.dsh_home, session_root=self.dsh_session_root),
        )

    @classmethod
    def from_sections(
        cls,
        general: GeneralSettings,
        codex: CodexSettings,
        dsh: DshSettings,
    ) -> "AppSettings":
        return cls(
            backup_before_delete=general.backup_before_delete,
            backup_directory=general.backup_directory,
            include_state_in_backups=general.include_state_in_backups,
            show_internal_messages=general.show_internal_messages,
            language=general.language,
            theme=general.theme,
            confirmation_text=general.confirmation_text,
            backup_retention_count=general.backup_retention_count,
            backup_retention_days=general.backup_retention_days,
            backup_retention_mib=general.backup_retention_mib,
            codex_binary=codex.codex_binary,
            dsh_home=dsh.dsh_home,
            dsh_session_root=dsh.session_root,
        )


def settings_path() -> Path:
    return get_platform().settings_path()


def _coerce(default: Any, candidate: Any) -> Any:
    """Only accept values of the declared default type, like schema v1 did."""
    if isinstance(default, bool):
        return candidate if isinstance(candidate, bool) else default
    if isinstance(default, int):
        return candidate if isinstance(candidate, int) and not isinstance(candidate, bool) else default
    if isinstance(default, str):
        return candidate if isinstance(candidate, str) else default
    return default


def _build_section(kind: type, values: dict[str, Any]) -> Any:
    instance = kind()
    for field in fields(kind):
        if field.name in values:
            setattr(instance, field.name, _coerce(getattr(instance, field.name), values[field.name]))
    return instance


def _settings_from_document(value: dict[str, Any]) -> AppSettings:
    if any(key in value for key in ("schema_version", "general", "applications")):
        general_value = value.get("general")
        applications = value.get("applications")
        general_doc = general_value if isinstance(general_value, dict) else {}
        app_doc = applications if isinstance(applications, dict) else {}
        codex_value = app_doc.get("codex")
        dsh_value = app_doc.get("dsh")
        return AppSettings.from_sections(
            _build_section(GeneralSettings, general_doc),
            _build_section(CodexSettings, codex_value if isinstance(codex_value, dict) else {}),
            _build_section(DshSettings, dsh_value if isinstance(dsh_value, dict) else {}),
        )
    # Schema v1: a flat mapping with every preference at the top level.
    return AppSettings.from_sections(
        _build_section(GeneralSettings, value),
        _build_section(CodexSettings, value),
        _build_section(DshSettings, value),
    )


def _read_document(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _normalized(settings: AppSettings) -> AppSettings:
    general, codex, dsh = settings.sections()
    return AppSettings.from_sections(normalize_general(general), codex, dsh)


def load_settings(path: Path | None = None) -> AppSettings:
    """Load settings, migrating a schema v1 file when one is all that exists."""
    target = path or settings_path()
    document = _read_document(target)
    if document is not None:
        return _normalized(_settings_from_document(document))
    for legacy in get_platform().legacy_settings_paths():
        if legacy == target:
            continue
        legacy_document = _read_document(legacy)
        if legacy_document is None:
            continue
        settings = _normalized(_settings_from_document(legacy_document))
        # Migrate in place on the next write; never fail a read because of it.
        try:
            save_settings(settings, target)
        except OSError:
            pass
        return settings
    return AppSettings()


def _document_from_settings(settings: AppSettings) -> dict[str, Any]:
    general, codex, dsh = settings.sections()
    return {
        "schema_version": SCHEMA_VERSION,
        "general": asdict(general),
        "applications": {"codex": asdict(codex), "dsh": asdict(dsh)},
    }


def save_settings(settings: AppSettings, path: Path | None = None) -> Path:
    target = path or settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix="settings.", suffix=".tmp", dir=target.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            json.dump(_document_from_settings(_normalized(settings)), stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, target)
    except Exception:
        temp.unlink(missing_ok=True)
        raise
    return target
