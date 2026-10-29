"""Codex-specific configuration; never imported by DSH adapters."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class CodexSettings:
    """Preferences that only apply when the Codex adapter is selected."""

    codex_binary: str = ""
