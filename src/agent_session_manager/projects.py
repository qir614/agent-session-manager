from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


TEMPORARY_PROJECT_NAME = "未选择项目 / 临时对话"
TEMPORARY_PROJECT_KEY = "__temporary__"


@dataclass(frozen=True, slots=True)
class ProjectInfo:
    key: str
    name: str
    path: str
    temporary: bool


def normalize_workspace(value: str | Path) -> str:
    return str(value).strip().replace("\\", "/").rstrip("/").casefold()


def is_temporary_workspace(cwd: str, user_home: Path | None = None) -> bool:
    if not cwd.strip():
        return True
    home = normalize_workspace(user_home or Path.home())
    workspace = normalize_workspace(cwd)
    managed_root = f"{home}/documents/codex"
    return (
        workspace == home
        or workspace == managed_root
        or workspace.startswith(managed_root + "/")
    )


def project_info(cwd: str, user_home: Path | None = None) -> ProjectInfo:
    raw = cwd.strip().rstrip("\\/")
    if is_temporary_workspace(raw, user_home):
        return ProjectInfo(
            key=TEMPORARY_PROJECT_KEY,
            name=TEMPORARY_PROJECT_NAME,
            path=raw,
            temporary=True,
        )
    normalized = normalize_workspace(raw)
    name = raw.replace("\\", "/").rsplit("/", 1)[-1] or raw
    return ProjectInfo(key=normalized, name=name, path=raw, temporary=False)
