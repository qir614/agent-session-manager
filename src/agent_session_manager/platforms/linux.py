"""Linux desktop integration; no Windows imports or commands."""
from __future__ import annotations

import os
import stat
import sys
import subprocess
from pathlib import Path

NAME = "linux"
PREFERRED_FONTS = ("Noto Sans CJK SC", "WenQuanYi Micro Hei", "Droid Sans Fallback", "Noto Sans", "DejaVu Sans")
PREFERRED_THEME = "clam"
# Window-based scaling is applied by the UI; do not also multiply by Xft DPI.
TK_SCALING = 96 / 72
CODEX_COMMAND_NAMES = ("codex",)
CONFIG_DIR_NAME = "AgentSessionManager"
LEGACY_CONFIG_DIR_NAMES = ("CodexLocalSessionCleaner",)


def settings_path() -> Path:
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / CONFIG_DIR_NAME / "settings.json"


def legacy_settings_paths() -> tuple[Path, ...]:
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return tuple(root / name / "settings.json" for name in LEGACY_CONFIG_DIR_NAMES)


def directory_command(path: Path, *, reveal: bool = False) -> list[str]:
    target = path.parent if reveal and not path.is_dir() else path
    return ["xdg-open", str(target)]


def process_names() -> list[str]:
    result = subprocess.run(
        ["ps", "-A", "-o", "comm="], capture_output=True,
        text=True, timeout=5, check=False,
    )
    if result.returncode:
        raise OSError("Linux process enumeration failed")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def is_reparse_point(path: Path) -> bool:
    try:
        return stat.S_ISLNK(path.lstat().st_mode)
    except OSError:
        return True


def prepare_gui_runtime() -> None:
    """Use an optional project-local Xft library without changing conda."""
    runtime = Path(__file__).resolve().parents[3] / "linux" / ".runtime" / "tk-xft"
    library = runtime / "lib" / "libtk8.6.so"
    if not library.is_file() or os.environ.get("ASM_XFT_ACTIVE") == "1":
        return
    import tkinter
    if tkinter.TkVersion != 8.6:
        raise RuntimeError("Project-local Xft runtime requires Tcl/Tk 8.6.")
    arguments = sys.orig_argv[1:]
    if "-" in arguments:
        raise RuntimeError("Start the Xft GUI with linux/start.sh or run_gui.py, not Python stdin.")
    env = os.environ.copy()
    env["ASM_XFT_ACTIVE"] = "1"
    env["TK_LIBRARY"] = str(runtime / "lib" / "tk8.6")
    env["FONTCONFIG_FILE"] = str(runtime.parents[1] / "fonts.conf")
    env["LD_PRELOAD"] = str(library) + (":" + env["LD_PRELOAD"] if env.get("LD_PRELOAD") else "")
    os.execve(sys.executable, [sys.executable, *arguments], env)
