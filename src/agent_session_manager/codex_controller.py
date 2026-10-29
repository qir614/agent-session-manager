from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from . import __version__
from .platforms import get_platform


SESSION_ID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I
)


class CodexOperationError(RuntimeError):
    pass


def resolve_codex_command(explicit: str = "") -> str | None:
    requested = explicit or os.environ.get("CODEX_BINARY", "")
    if requested:
        path = Path(requested).expanduser()
        return str(path.resolve()) if path.is_file() else shutil.which(requested)
    names = get_platform().CODEX_COMMAND_NAMES
    return next((value for name in names if (value := shutil.which(name))), None)


class CodexController:
    def __init__(
        self,
        codex_home: Path,
        command: str = "",
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ):
        self.codex_home = codex_home.expanduser().resolve()
        self.command = resolve_codex_command(command)
        self.runner = runner or subprocess.run

    @property
    def available(self) -> bool:
        return bool(self.command)

    def _validate(self, session_id: str) -> None:
        if not SESSION_ID_RE.fullmatch(session_id):
            raise CodexOperationError("Invalid session ID.")

    def _environment(self) -> dict[str, str]:
        env = os.environ.copy()
        env["CODEX_HOME"] = str(self.codex_home)
        return env

    def _run(self, *args: str) -> None:
        if not self.command:
            raise CodexOperationError(
                "Codex command was not found. Configure its path in Settings."
            )
        try:
            result = self.runner(
                [self.command, *args], env=self._environment(), capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=30, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CodexOperationError(f"Cannot run Codex: {exc}") from exc
        if result.returncode:
            lines = (result.stderr or result.stdout or "").strip().splitlines()
            raise CodexOperationError(lines[-1][:500] if lines else f"Exit code {result.returncode}")

    def rename(self, session_id: str, title: str) -> None:
        self._validate(session_id)
        title = title.strip()
        if not title or len(title) > 200:
            raise CodexOperationError("Title must contain 1-200 characters.")
        if not self.command:
            raise CodexOperationError("Codex command was not found. Configure it in Settings.")
        messages = [
            {"method": "initialize", "id": 1, "params": {"clientInfo": {
                "name": "codex_local_session_cleaner", "title": "Agent Session Manager",
                "version": __version__,
            }}},
            {"method": "initialized", "params": {}},
            {"method": "thread/name/set", "id": 2, "params": {
                "threadId": session_id, "name": title,
            }},
        ]
        payload = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in messages)
        try:
            result = self.runner(
                [self.command, "app-server", "--listen", "stdio://"], input=payload,
                env=self._environment(), capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=30, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CodexOperationError(f"Cannot run Codex app-server: {exc}") from exc
        response = None
        for line in (result.stdout or "").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict) and item.get("id") == 2:
                response = item
                break
        if result.returncode or response is None or "error" in response:
            raise CodexOperationError("Codex did not accept the rename operation.")

    def set_archived(self, session_id: str, archived: bool) -> None:
        self._validate(session_id)
        self._run("archive" if archived else "unarchive", session_id)
