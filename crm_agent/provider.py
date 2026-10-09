from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Terminal:
    disposition: str
    reason: str
    citations: tuple[dict, ...] = ()


class Provider(Protocol):
    """Providers see only conversation observations and registered tool schemas."""
    mode: str
    model_id: str | None

    def next(self, messages: list[dict], tools: list[dict], limits: dict) -> ToolCall | Terminal: ...
