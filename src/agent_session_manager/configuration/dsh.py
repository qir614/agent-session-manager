"""DSH-specific configuration; never imported by Codex adapters."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class DshSettings:
    """Preferences that only apply when the DSH adapter is selected."""

    dsh_home: str = ""
    session_root: str = ""
