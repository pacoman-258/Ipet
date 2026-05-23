from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator
from uuid import uuid4

from .conversation_store import (
    MEMORY_MODE_PERSISTENT,
    MEMORY_MODE_TEMPORARY,
    ConversationStore,
    normalize_conversation_id,
    normalize_memory_mode,
)
from .ipet_memory_store import IpetMemoryStore, build_memory_candidates_from_turn
from .runtime_tasks import cleanup_runtime_session, temporary_runtime_session_id


TURN_NO_SAVE_MARKERS = (
    "这次不要保存",
    "本轮不要保存",
    "本轮不保存",
    "不要保存这次",
    "不要记住",
    "don't save this",
    "do not save this",
    "do not remember this",
)
CONVERSATION_NO_SAVE_MARKERS = (
    "不保存此次对话",
    "对话不保存",
    "这个话题之后都别记录",
    "本对话不要保存",
    "以后都别记录",
    "off the record",
)
TEMPORARY_MODE_MARKERS = (
    "临时聊聊",
    "临时对话",
    "临时聊天",
    "temporary chat",
)
SKIP_RETRIEVAL_MARKERS = (
    "不参考记忆",
    "跳过检索",
    "别用历史",
    "不要使用记忆",
    "不要用记忆",
    "do not use memory",
    "do not use history",
)


@dataclass(frozen=True)
class SavePolicy:
    memory_mode: str
    save_allowed: bool
    scope: str
    reason: str
    use_memory: bool = True


@dataclass(frozen=True)
class PendingApprovalTurn:
    conversation_id: str
    ipet_turn_id: str
    runtime_session_id: str
    approval_turn_id: str
    original_text: str
    assistant_text: str
    policy: SavePolicy
    chat_mode: str


_PENDING_APPROVAL_TURNS: dict[str, PendingApprovalTurn] = {}


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    return any(marker.casefold() in lowered for marker in markers)


def _pending_key(runtime_id: str, approval_turn_id: str) -> str:
    return f"{runtime_id}:{str(approval_turn_id or '').strip()}"


def detect_save_policy(user_text: str, memory_mode: str) -> SavePolicy:
    mode = normalize_memory_mode(memory_mode)
    text = str(user_text or "")
    use_memory = not _contains_any(text, SKIP_RETRIEVAL_MARKERS)
    if mode == MEMORY_MODE_TEMPORARY or _contains_any(text, TEMPORARY_MODE_MARKERS):
        return SavePolicy(
            memory_mode=MEMORY_MODE_TEMPORARY,
            save_allowed=False,
            scope="turn",
            reason="temporary",
            use_memory=use_memory,
        )
    if _contains_any(text, CONVERSATION_NO_SAVE_MARKERS):
        return SavePolicy(
            memory_mode=MEMORY_MODE_PERSISTENT,
            save_allowed=False,
            scope="conversation",
            reason="user_requested_no_save",
            use_memory=use_memory,
        )
    if _contains_any(text, TURN_NO_SAVE_MARKERS):
        return SavePolicy(
            memory_mode=MEMORY_MODE_PERSISTENT,
            save_allowed=False,
            scope="turn",
            reason="user_requested_no_save",
            use_memory=use_memory,
        )
    return SavePolicy(
        memory_mode=MEMORY_MODE_PERSISTENT,
        save_allowed=True,
        scope="none",
        reason="",
        use_memory=use_memory,
    )


class MemoryHarness:
    def __init__(
        self,
        *,
        conversation_store: ConversationStore,
        memory_store: IpetMemoryStore,
        runtime_client: Any,
    ) -> None:
        self.conversation_store = conversation_store
        self.memory_store = memory_store
        self.runtime_client = runtime_client

    async def stream_chat(self, payload: dict[str, Any]) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        original_text = str(payload.get("original_text") or payload.get("text") or "").strip()
        runtime_input_text = str(payload.get("text") or original_text).strip()
        conversation_id = normalize_conversation_id(payload.get("session_id") or payload.get("topic_id") or "default")
        policy = detect_save_policy(original_text, str(payload.get("memory_mode") or MEMORY_MODE_PERSISTENT))
        if policy.scope == "conversation":
            self.conversation_store.disable_saving(conversation_id, reason=policy.reason)

        runtime_turn_id = uuid4().hex
        runtime_session_id = temporary_runtime_session_id(runtime_turn_id)
        runtime_payload = dict(payload)
        for key in ("topic_id", "conversation_id", "runtime_session_id"):
            runtime_payload.pop(key, None)
        runtime_payload["session_id"] = runtime_session_id
        runtime_payload["text"] = self.build_runtime_text(
            conversation_id=conversation_id,
            user_text=runtime_input_text,
            policy=policy,
        )

        full_answer = ""
        pending_approval = False
        try:
            yield "meta", {
                "runtime": self._runtime_id(),
                "topic_id": conversation_id,
                "session_id": conversation_id,
                "runtime_session_id": runtime_session_id,
                "memory_mode": policy.memory_mode,
                "chat_mode": str(payload.get("chat_mode") or "react"),
            }
            async for event, data in self.runtime_client.stream_sse("/api/chat/stream", runtime_payload):
                next_data = dict(data or {})
                if event == "token":
                    full_answer += str(next_data.get("delta") or "")
                    yield event, next_data
                    continue
                if event == "done":
                    full_answer = str(next_data.get("text") or full_answer)
                    self._save_turn_if_allowed(
                        conversation_id=conversation_id,
                        runtime_turn_id=runtime_turn_id,
                        user_text=original_text,
                        assistant_text=full_answer,
                        runtime_session_id=runtime_session_id,
                        policy=policy,
                        chat_mode=str(payload.get("chat_mode") or "react"),
                    )
                    yield "done", self._with_conversation_meta(next_data, conversation_id, policy)
                    continue
                if event == "approval_required":
                    approval_turn_id = str(next_data.get("turn_id") or runtime_session_id).strip() or runtime_session_id
                    next_data["turn_id"] = approval_turn_id
                    pending_approval = True
                    self._store_pending_approval(
                        PendingApprovalTurn(
                            conversation_id=conversation_id,
                            ipet_turn_id=runtime_turn_id,
                            runtime_session_id=runtime_session_id,
                            approval_turn_id=approval_turn_id,
                            original_text=original_text,
                            assistant_text=full_answer,
                            policy=policy,
                            chat_mode=str(payload.get("chat_mode") or "react"),
                        )
                    )
                    yield "approval_required", self._with_conversation_meta(next_data, conversation_id, policy)
                    return
                if event == "meta":
                    continue
                yield event, next_data
        finally:
            if not pending_approval:
                await self._cleanup_runtime_session(runtime_turn_id, runtime_session_id)

    async def stream_approval(self, payload: dict[str, Any]) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        approval_turn_id = str(payload.get("turn_id") or "").strip()
        pending = self._load_pending_approval(approval_turn_id)
        if pending is None:
            async for event, data in self.runtime_client.stream_sse("/api/chat/approval", payload):
                yield event, dict(data or {})
            return

        approval_text = str(payload.get("user_text") or "").strip()
        policy = self._policy_after_approval_text(
            pending.policy,
            conversation_id=pending.conversation_id,
            original_text=pending.original_text,
            approval_text=approval_text,
        )
        user_text = self._approval_history_user_text(pending.original_text, approval_text)
        full_answer = pending.assistant_text
        pending_approval = False
        terminal_done = False
        try:
            async for event, data in self.runtime_client.stream_sse("/api/chat/approval", dict(payload)):
                next_data = dict(data or {})
                if event == "token":
                    full_answer += str(next_data.get("delta") or "")
                    yield event, next_data
                    continue
                if event == "done":
                    full_answer = str(next_data.get("text") or full_answer)
                    self._save_turn_if_allowed(
                        conversation_id=pending.conversation_id,
                        runtime_turn_id=pending.ipet_turn_id,
                        user_text=user_text,
                        assistant_text=full_answer,
                        runtime_session_id=pending.runtime_session_id,
                        policy=policy,
                        chat_mode=pending.chat_mode,
                    )
                    terminal_done = True
                    yield "done", self._with_conversation_meta(next_data, pending.conversation_id, policy)
                    continue
                if event == "approval_required":
                    next_approval_turn_id = str(next_data.get("turn_id") or approval_turn_id).strip() or approval_turn_id
                    next_data["turn_id"] = next_approval_turn_id
                    pending_approval = True
                    self._drop_pending_approval(approval_turn_id)
                    self._store_pending_approval(
                        PendingApprovalTurn(
                            conversation_id=pending.conversation_id,
                            ipet_turn_id=pending.ipet_turn_id,
                            runtime_session_id=pending.runtime_session_id,
                            approval_turn_id=next_approval_turn_id,
                            original_text=user_text,
                            assistant_text=full_answer,
                            policy=policy,
                            chat_mode=pending.chat_mode,
                        )
                    )
                    yield "approval_required", self._with_conversation_meta(next_data, pending.conversation_id, policy)
                    return
                if event == "meta":
                    continue
                yield event, next_data
        finally:
            if pending_approval:
                return
            if not terminal_done:
                return
            self._drop_pending_approval(approval_turn_id)
            await self._cleanup_runtime_session(pending.ipet_turn_id, pending.runtime_session_id)

    def build_runtime_text(self, *, conversation_id: str, user_text: str, policy: SavePolicy) -> str:
        sections: list[str] = []
        if policy.use_memory and policy.memory_mode != MEMORY_MODE_TEMPORARY:
            memories = self.memory_store.search(user_text, limit=5)
            if memories:
                lines = ["[Long-term Memory]"]
                for item in memories:
                    lines.append(f"- {item['title']}: {item['summary']}")
                sections.append("\n".join(lines))

        if policy.memory_mode != MEMORY_MODE_TEMPORARY:
            context = self.conversation_store.load_context(conversation_id)
            if context is not None:
                if context.major_summary:
                    sections.append(f"[Conversation Major Summary]\n{context.major_summary}")
                if context.rolling_summary:
                    sections.append(f"[Conversation Rolling Summary]\n{context.rolling_summary}")
                if context.recent_messages:
                    lines = ["[Recent Conversation]"]
                    for item in context.recent_messages[-12:]:
                        lines.append(f"{item['role']}: {item['content']}")
                    sections.append("\n".join(lines))

        sections.append(f"用户消息：{user_text}")
        return "\n\n".join(section for section in sections if str(section or "").strip())

    def _extract_memory_if_needed(
        self,
        conversation_id: str,
        runtime_turn_id: str,
        user_text: str,
        assistant_text: str,
        save_result: dict[str, Any],
    ) -> None:
        if not save_result.get("saved"):
            return
        candidates = build_memory_candidates_from_turn(
            conversation_id=conversation_id,
            turn_id=runtime_turn_id,
            user_text=user_text,
            assistant_text=assistant_text,
        )
        for candidate in candidates:
            self.memory_store.write_memory(**candidate)
        meta = save_result.get("meta") if isinstance(save_result.get("meta"), dict) else {}
        self.conversation_store.update_memory_extracted_turn(
            conversation_id,
            int(meta.get("assistant_turn_count") or 0),
        )

    def _save_turn_if_allowed(
        self,
        *,
        conversation_id: str,
        runtime_turn_id: str,
        user_text: str,
        assistant_text: str,
        runtime_session_id: str,
        policy: SavePolicy,
        chat_mode: str,
    ) -> None:
        if not policy.save_allowed:
            return
        save_result = self.conversation_store.append_turn(
            conversation_id,
            turn_id=runtime_turn_id,
            user_text=user_text,
            assistant_text=assistant_text,
            runtime=self._runtime_id(),
            runtime_session_id=runtime_session_id,
            memory_mode=policy.memory_mode,
            save_policy="save",
            metadata={"chat_mode": chat_mode},
        )
        self._extract_memory_if_needed(
            conversation_id,
            runtime_turn_id,
            user_text,
            assistant_text,
            save_result,
        )

    async def _cleanup_runtime_session(self, runtime_turn_id: str, runtime_session_id: str) -> None:
        cleanup = await cleanup_runtime_session(self.runtime_client, runtime_session_id)
        if cleanup.get("ok"):
            self.conversation_store.clear_cleanup_record(runtime_turn_id)
        else:
            self.conversation_store.write_cleanup_record(
                runtime_turn_id=runtime_turn_id,
                runtime=self._runtime_id(),
                runtime_session_id=runtime_session_id,
                error=str(cleanup.get("error") or "runtime cleanup failed"),
            )

    def _runtime_id(self) -> str:
        return str(getattr(self.runtime_client, "runtime_id", "runtime") or "runtime")

    def _store_pending_approval(self, pending: PendingApprovalTurn) -> None:
        _PENDING_APPROVAL_TURNS[_pending_key(self._runtime_id(), pending.approval_turn_id)] = pending

    def _load_pending_approval(self, approval_turn_id: str) -> PendingApprovalTurn | None:
        return _PENDING_APPROVAL_TURNS.get(_pending_key(self._runtime_id(), approval_turn_id))

    def _drop_pending_approval(self, approval_turn_id: str) -> None:
        _PENDING_APPROVAL_TURNS.pop(_pending_key(self._runtime_id(), approval_turn_id), None)

    def _policy_after_approval_text(
        self,
        policy: SavePolicy,
        *,
        conversation_id: str,
        original_text: str,
        approval_text: str,
    ) -> SavePolicy:
        if not approval_text:
            return policy
        next_policy = detect_save_policy(f"{original_text}\n{approval_text}", policy.memory_mode)
        if next_policy.scope == "conversation":
            self.conversation_store.disable_saving(
                conversation_id,
                reason=next_policy.reason,
            )
        return next_policy

    def _approval_history_user_text(self, original_text: str, approval_text: str) -> str:
        if not approval_text:
            return original_text
        return f"{original_text}\n\n审批补充：{approval_text}"

    def _with_conversation_meta(
        self,
        data: dict[str, Any],
        conversation_id: str,
        policy: SavePolicy,
    ) -> dict[str, Any]:
        next_data = dict(data)
        next_data["topic_id"] = conversation_id
        next_data["session_id"] = conversation_id
        next_data["memory_mode"] = policy.memory_mode
        return next_data
