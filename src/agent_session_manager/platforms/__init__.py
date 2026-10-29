"""One runtime selector; platform implementations stay in separate modules."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Protocol, cast


class Platform(Protocol):
    NAME: str
    PREFERRED_FONTS: tuple[str, ...]
    PREFERRED_THEME: str
    TK_SCALING: float | None
    CODEX_COMMAND_NAMES: tuple[str, ...]
    CONFIG_DIR_NAME: str
    LEGACY_CONFIG_DIR_NAMES: tuple[str, ...]

    def settings_path(self) -> Path: ...
    def legacy_settings_paths(self) -> tuple[Path, ...]: ...
    def directory_command(self, path: Path, *, reveal: bool = False) -> list[str]: ...
    def process_names(self) -> list[str]: ...
    def prepare_gui_runtime(self) -> None: ...
    def is_reparse_point(self, path: Path) -> bool: ...


def get_platform() -> Platform:
    if sys.platform == "win32":
        name = "windows"
    elif sys.platform.startswith("linux"):
        name = "linux"
    else:
        raise RuntimeError(f"Unsupported operating system: {sys.platform}; supported: Linux, Windows")
    return cast(Platform, importlib.import_module(f"{__name__}.{name}"))
