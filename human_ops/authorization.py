from __future__ import annotations

from typing import Any


AUTHORIZATION_MODE_REVIEW = "review"
AUTHORIZATION_MODE_FULL = "full"
AUTHORIZATION_MODES = frozenset({AUTHORIZATION_MODE_REVIEW, AUTHORIZATION_MODE_FULL})


def normalize_authorization_mode(
    value: Any,
    *,
    require_act_review: Any = True,
) -> str:
    """Return a safe action authorization mode, including legacy config support."""

    mode = str(value or "").strip().lower()
    if mode in AUTHORIZATION_MODES:
        return mode
    return AUTHORIZATION_MODE_FULL if require_act_review is False else AUTHORIZATION_MODE_REVIEW


def full_authorization_enabled(config: dict[str, Any] | None) -> bool:
    data = config if isinstance(config, dict) else {}
    return (
        normalize_authorization_mode(
            data.get("authorization_mode"),
            require_act_review=data.get("require_act_review", True),
        )
        == AUTHORIZATION_MODE_FULL
    )


__all__ = [
    "AUTHORIZATION_MODE_FULL",
    "AUTHORIZATION_MODE_REVIEW",
    "AUTHORIZATION_MODES",
    "full_authorization_enabled",
    "normalize_authorization_mode",
]
