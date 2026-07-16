from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

from fastapi.testclient import TestClient

from backend import app as backend_app
from backend.chat_topics import TopicStore
from backend.ipet_memory_store import IpetMemoryStore
from brain.decisions import BrainDecision


def _sse_events(body: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    event_name = "message"
    data_lines: list[str] = []
    for line in body.splitlines():
        if not line.strip():
            if data_lines:
                events.append((event_name, json.loads("\n".join(data_lines))))
            event_name = "message"
            data_lines = []
        elif line.startswith("event:"):
            event_name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].strip())
    return events


class RelationshipMemoryFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parent / ".tmp_relationship_memory" / uuid4().hex
        self.memory_store = IpetMemoryStore(self.root / "memory")
        self.topic_store = TopicStore(self.root / "topics")
        self.brain_calls: list[dict] = []

        async def run_brain_turn(_config: dict, **kwargs):
            self.brain_calls.append(kwargs)
            user_text = str(kwargs.get("user_text") or "")
            if user_text.startswith("记住"):
                decision = BrainDecision.propose_remember("preference", user_text)
            elif user_text.startswith("更正"):
                decision = BrainDecision.propose_remember("preference", user_text)
            elif user_text.startswith("忘记"):
                decision = BrainDecision.propose_remember("forget", user_text)
            else:
                decision = BrainDecision.say("我在听。")
            return SimpleNamespace(
                text=decision.summary,
                decision=decision,
                provider="codex",
                model="test",
                usage={},
            )

        config = {
            "brain": {"provider": "codex", "model_name": "test", "streaming_enabled": False},
            "human_ops": {"require_memory_review": True},
            "memory": {
                "conversation_saving": True,
                "long_term_enabled": True,
                "preferences_enabled": True,
                "relationship_enabled": True,
                "retention_days": 365,
                "review_limit": 20,
            },
        }
        self.patches = [
            mock.patch.object(backend_app, "MEMORY_STORE", self.memory_store),
            mock.patch.object(backend_app, "TOPIC_STORE", self.topic_store),
            mock.patch.object(backend_app, "_normalize_private_config", lambda: config),
            mock.patch.object(backend_app, "run_brain_turn", run_brain_turn),
        ]
        for patcher in self.patches:
            patcher.start()
        backend_app.HUMAN_OPS_PENDING_PROPOSALS.clear()
        self.client = TestClient(backend_app.app)

    def tearDown(self) -> None:
        backend_app.HUMAN_OPS_PENDING_PROPOSALS.clear()
        for patcher in reversed(self.patches):
            patcher.stop()
        shutil.rmtree(self.root, ignore_errors=True)

    def _chat(self, text: str, *, memory_mode: str = "persistent") -> list[tuple[str, dict]]:
        response = self.client.post(
            "/api/chat/stream",
            json={"text": text, "session_id": "friend", "memory_mode": memory_mode},
        )
        self.assertEqual(response.status_code, 200)
        return _sse_events(response.text)

    def _decide(self, proposal_id: str, approved: bool) -> list[tuple[str, dict]]:
        response = self.client.post(
            f"/api/human-ops/proposals/{proposal_id}/decision",
            json={"approved": approved},
        )
        self.assertEqual(response.status_code, 200)
        return _sse_events(response.text)

    def test_review_save_restart_recall_implicit_candidate_and_forget_close_the_loop(self) -> None:
        first = self._chat("记住我喜欢红茶")
        first_proposal = first[-1][1]["proposal_id"]
        self.assertEqual(self.memory_store.list_memories(), [])

        rejected = self._decide(first_proposal, False)
        self.assertFalse(rejected[-1][1]["approved"])
        self.assertEqual(self.memory_store.list_memories(), [])

        second = self._chat("记住我喜欢红茶")
        second_proposal = second[-1][1]["proposal_id"]
        approved = self._decide(second_proposal, True)
        self.assertTrue(approved[-1][1]["execution"]["saved"])
        self.assertEqual(len(IpetMemoryStore(self.root / "memory").search("红茶")), 1)

        correction = self._chat("更正：我现在喜欢咖啡")
        self._decide(correction[-1][1]["proposal_id"], True)
        self.assertEqual(self.memory_store.search("红茶"), [])
        self.assertEqual(len(self.memory_store.search("咖啡")), 1)

        self._chat("咖啡适合我吗")
        history = self.brain_calls[-1]["conversation_history"]
        self.assertTrue(any(item["role"] == "system" and "我现在喜欢咖啡" in item["content"] for item in history))

        implicit = self._chat("我不喜欢被催促")
        self.assertEqual(implicit[-1][1]["memory_candidate_count"], 1)
        catalog = self.client.get("/api/memory").json()
        implicit_proposal = next(item for item in catalog["pending"] if item["origin"] == "implicit")
        self._decide(implicit_proposal["proposal_id"], True)
        self.assertEqual(len(self.memory_store.search("催促")), 1)

        forget = self._chat("忘记我现在喜欢咖啡")
        self._decide(forget[-1][1]["proposal_id"], True)
        self.assertEqual(self.memory_store.search("咖啡"), [])

        before_temporary = len(self.brain_calls)
        self._chat("红茶呢", memory_mode="temporary")
        temporary_call = self.brain_calls[before_temporary]
        self.assertFalse(
            any(
                item.get("role") == "system" and "已由用户批准的关系记忆" in item.get("content", "")
                for item in temporary_call.get("conversation_history", [])
            )
        )


if __name__ == "__main__":
    unittest.main()
