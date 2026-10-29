from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


INTERNAL_PREFIXES = (
    "<environment_context>",
    "<external_tool_codex_apps_open_page|>",
    "<recommended_plugins>",
    "# AGENTS.md instructions",
    "<permissions instructions>",
    "<app-context>",
    "<collaboration_mode>",
    "<skills_instructions>",
    "<plugins_instructions>",
    "<apps_instructions>",
)


@dataclass(slots=True)
class ConversationMessage:
    role: str
    text: str
    timestamp: str
    phase: str
    internal: bool = False


@dataclass(slots=True)
class ConversationDetails:
    messages: list[ConversationMessage]
    metadata: dict[str, Any]
    event_count: int
    invalid_line_count: int
    event_type_counts: dict[str, int]


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") in {"input_text", "output_text", "text"}:
            value = item.get("text")
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
    return "\n\n".join(parts)


def read_conversation(
    path: Path | Iterable[Path], include_internal: bool = False
) -> ConversationDetails:
    messages: list[ConversationMessage] = []
    metadata: dict[str, Any] = {}
    counts: dict[str, int] = {}
    invalid = 0
    events = 0
    paths = [path] if isinstance(path, Path) else list(path)
    for segment_path in paths:
        with segment_path.open("r", encoding="utf-8-sig", errors="replace") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except (json.JSONDecodeError, UnicodeError):
                    invalid += 1
                    continue
                if not isinstance(event, dict):
                    continue
                events += 1
                event_type = str(event.get("type") or "unknown")
                counts[event_type] = counts.get(event_type, 0) + 1
                payload = event.get("payload")
                if not isinstance(payload, dict):
                    continue
                if event_type == "session_meta":
                    if not metadata:
                        metadata = dict(payload)
                    continue
                if event_type != "response_item" or payload.get("type") != "message":
                    continue
                role = str(payload.get("role") or "")
                if role not in {"user", "assistant", "system", "developer"}:
                    continue
                text = _text(payload.get("content"))
                if not text:
                    continue
                internal = role in {"system", "developer"} or text.lstrip().startswith(INTERNAL_PREFIXES)
                if internal and not include_internal:
                    continue
                message = ConversationMessage(
                    role=role,
                    text=text,
                    timestamp=str(event.get("timestamp") or ""),
                    phase=str(payload.get("phase") or ""),
                    internal=internal,
                )
                if not messages or (
                    messages[-1].role, messages[-1].text, messages[-1].timestamp
                ) != (message.role, message.text, message.timestamp):
                    messages.append(message)
    return ConversationDetails(messages, metadata, events, invalid, counts)
