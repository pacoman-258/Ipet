from __future__ import annotations

from typing import Any, Callable


def build_proactive_conversation_history(
    *,
    topic_store: Any,
    session_id: str,
    opportunity: dict[str, Any],
    private_config: dict[str, Any],
    relationship_memory_context: Callable[[str, dict[str, Any]], tuple[str, list[str]]],
) -> list[dict[str, str]]:
    snapshot = topic_store.get_context_snapshot(session_id)
    history = list(snapshot.model_messages[-12:]) if snapshot is not None else []
    memory_config = private_config.get("memory", {}) if isinstance(private_config.get("memory"), dict) else {}
    if memory_config.get("long_term_enabled") is not False:
        facts = opportunity.get("facts") if isinstance(opportunity.get("facts"), dict) else {}
        query = " ".join(
            str(value or "")
            for value in (opportunity.get("kind"), facts.get("foreground_app"))
            if str(value or "").strip()
        )
        try:
            memory_context, _due_ids = relationship_memory_context(query, memory_config)
        except Exception:
            memory_context = ""
        if memory_context:
            history = [{"role": "system", "content": memory_context}, *history]
    return [
        {"role": str(item.get("role") or ""), "content": str(item.get("content") or "")}
        for item in history
        if isinstance(item, dict)
        and str(item.get("role") or "") in {"system", "user", "assistant"}
        and str(item.get("content") or "").strip()
    ]


__all__ = ["build_proactive_conversation_history"]
