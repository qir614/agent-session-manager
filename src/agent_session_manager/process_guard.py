from __future__ import annotations

import subprocess
from .platforms import get_platform


CODEX_PROCESS_NAMES = {
    "chatgpt.exe",
    "codex.exe",
    "codex-code-mode-host.exe",
    "chatgpt",
    "codex",
    "codex-code-mode-host",
}


def running_codex_processes() -> list[str]:
    """Platform adapters enumerate; shared policy blocks unsafe writes."""
    try:
        names = get_platform().process_names()
    except (OSError, subprocess.TimeoutExpired):
        return ["process-check-unavailable"]
    return sorted({name for name in names if name.casefold() in CODEX_PROCESS_NAMES}, key=str.casefold)


def require_codex_stopped() -> None:
    running = running_codex_processes()
    if running:
        names = ", ".join(running)
        raise RuntimeError(
            "Codex must be completely closed before cleanup or restore. "
            f"Detected: {names}"
        )
