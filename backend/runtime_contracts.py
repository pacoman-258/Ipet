from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol, runtime_checkable


@dataclass(slots=True)
class AgentInputEvent:
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    session_id: str = ""


@dataclass(slots=True)
class AgentRuntimeEvent:
    event: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentRuntimeError(Exception):
    message: str
    runtime: str = ""
    detail: str = ""

    def __str__(self) -> str:
        return self.message


@runtime_checkable
class RuntimeAdapter(Protocol):
    runtime_id: str

    async def status(self) -> dict[str, Any]:
        ...

    async def request_json(self, method: str, path: str, *, json_payload: Any | None = None) -> dict[str, Any]:
        ...

    def stream_sse(self, path: str, payload: dict[str, Any]) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        ...
