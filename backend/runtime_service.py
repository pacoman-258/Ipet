from __future__ import annotations

from typing import Any

from .runtime_adapters import create_runtime_adapter
from .runtime_contracts import RuntimeAdapter


def resolve_runtime_adapter(runtime_config: dict[str, Any], runtime_id: str | None = None) -> RuntimeAdapter:
    return create_runtime_adapter(runtime_config, runtime_id=runtime_id)
