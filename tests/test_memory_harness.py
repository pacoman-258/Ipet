from __future__ import annotations

import asyncio
import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from backend.conversation_store import ConversationStore
from backend.ipet_memory_store import IpetMemoryStore
from backend.memory_harness import MemoryHarness, detect_save_policy


class _FakeRuntimeClient:
    runtime_id = "astrbot"

    def __init__(self, *, events=None, fail_delete: bool = False) -> None:
        self.events = events or [("token", {"delta": "好的"}), ("done", {"text": "好的"})]
        self.fail_delete = fail_delete
        self.stream_payloads: list[dict] = []
        self.delete_calls: list[str] = []

    async def stream_sse(self, path, payload):
        self.stream_payloads.append({"path": path, "payload": dict(payload)})
        for event, data in self.events:
            yield event, dict(data)

    async def delete_runtime_session(self, session_id):
        self.delete_calls.append(session_id)
        if self.fail_delete:
            raise RuntimeError("delete failed")
        return {"ok": True}


class MemoryHarnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parent / ".tmp_memory_harness" / uuid4().hex
        self.conversations = ConversationStore(self.root / "conversations")
        self.memories = IpetMemoryStore(self.root / "memory")

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_detect_save_policy(self) -> None:
        self.assertEqual(detect_save_policy("这次不要保存", "persistent").scope, "turn")
        self.assertEqual(detect_save_policy("本轮不保存", "persistent").scope, "turn")
        self.assertEqual(detect_save_policy("这个话题之后都别记录", "persistent").scope, "conversation")
        self.assertEqual(detect_save_policy("对话不保存", "persistent").scope, "conversation")
        self.assertEqual(detect_save_policy("临时聊聊 Python", "persistent").memory_mode, "temporary")
        self.assertFalse(detect_save_policy("不要使用记忆，只回答当前问题", "persistent").use_memory)
        self.assertFalse(detect_save_policy("跳过检索，只回答当前问题", "persistent").use_memory)

    def test_persistent_turn_injects_memory_saves_and_cleans_runtime(self) -> None:
        conversation = self.conversations.create_conversation(conversation_id="conv-1", title="Demo")
        self.conversations.append_turn(
            conversation["conversation_id"],
            turn_id="old-turn",
            user_text="旧问题",
            assistant_text="旧答案",
            runtime="astrbot",
            runtime_session_id="ipet-temp-old",
            memory_mode="persistent",
            save_policy="save",
        )
        self.memories.write_memory(
            title="用户偏好：红茶",
            summary="用户喜欢红茶。",
            evidence="用户说喜欢红茶。",
            source_conversation_id="conv-1",
            source_turn_id="old-turn",
            tags=["preference"],
        )
        runtime = _FakeRuntimeClient()
        harness = MemoryHarness(conversation_store=self.conversations, memory_store=self.memories, runtime_client=runtime)

        events = asyncio.run(
            self._collect(
                harness.stream_chat(
                    {
                        "session_id": "conv-1",
                        "text": "红茶 请记住：我以后写测试偏好 unittest。",
                        "chat_mode": "react",
                        "memory_mode": "persistent",
                    }
                )
            )
        )

        self.assertEqual(events[-1][0], "done")
        self.assertEqual(events[-1][1]["topic_id"], "conv-1")
        sent_text = runtime.stream_payloads[0]["payload"]["text"]
        self.assertIn("[Long-term Memory]", sent_text)
        self.assertIn("用户喜欢红茶", sent_text)
        self.assertIn("旧问题", sent_text)
        self.assertTrue(runtime.stream_payloads[0]["payload"]["session_id"].startswith("ipet-temp-"))
        self.assertEqual(runtime.delete_calls, [runtime.stream_payloads[0]["payload"]["session_id"]])
        detail = self.conversations.get_topic_detail("conv-1")
        self.assertEqual(detail["messages"][-2]["content"], "红茶 请记住：我以后写测试偏好 unittest。")
        self.assertTrue(self.memories.search("unittest"))

    def test_runtime_payload_removes_durable_identity_fields(self) -> None:
        runtime = _FakeRuntimeClient()
        harness = MemoryHarness(conversation_store=self.conversations, memory_store=self.memories, runtime_client=runtime)

        asyncio.run(
            self._collect(
                harness.stream_chat(
                    {
                        "session_id": "conv-identity",
                        "topic_id": "durable-topic",
                        "conversation_id": "durable-conversation",
                        "runtime_session_id": "durable-runtime-session",
                        "text": "你好",
                        "memory_mode": "persistent",
                    }
                )
            )
        )

        runtime_payload = runtime.stream_payloads[0]["payload"]
        self.assertNotIn("topic_id", runtime_payload)
        self.assertNotIn("conversation_id", runtime_payload)
        self.assertNotIn("runtime_session_id", runtime_payload)
        self.assertTrue(runtime_payload["session_id"].startswith("ipet-temp-"))

    def test_original_text_is_used_for_saved_conversation_and_memory_extraction(self) -> None:
        runtime = _FakeRuntimeClient()
        harness = MemoryHarness(conversation_store=self.conversations, memory_store=self.memories, runtime_client=runtime)
        original_text = "请记住：我喜欢绿茶。"
        augmented_text = "[Visual Evidence]\n屏幕显示银行卡号 1234。\n\n用户消息：请记住：我喜欢绿茶。"

        asyncio.run(
            self._collect(
                harness.stream_chat(
                    {
                        "session_id": "conv-visual",
                        "text": augmented_text,
                        "original_text": original_text,
                        "chat_mode": "react",
                        "memory_mode": "persistent",
                    }
                )
            )
        )

        runtime_text = runtime.stream_payloads[0]["payload"]["text"]
        self.assertIn(augmented_text, runtime_text)
        detail = self.conversations.get_topic_detail("conv-visual")
        self.assertEqual(detail["messages"][-2]["content"], original_text)
        self.assertNotIn("银行卡号", detail["messages"][-2]["content"])
        memories = self.memories.search("绿茶")
        self.assertTrue(memories)
        self.assertNotIn("银行卡号", memories[0]["summary"])

    def test_runtime_meta_event_is_suppressed(self) -> None:
        runtime = _FakeRuntimeClient(events=[("meta", {"runtime": "astrbot"}), ("token", {"delta": "好"}), ("done", {"text": "好"})])
        harness = MemoryHarness(conversation_store=self.conversations, memory_store=self.memories, runtime_client=runtime)

        events = asyncio.run(
            self._collect(
                harness.stream_chat(
                    {
                        "session_id": "conv-meta",
                        "text": "你好",
                        "memory_mode": "persistent",
                    }
                )
            )
        )

        self.assertEqual([event for event, _payload in events].count("meta"), 1)

    def test_temporary_turn_does_not_save_business_content(self) -> None:
        runtime = _FakeRuntimeClient()
        harness = MemoryHarness(conversation_store=self.conversations, memory_store=self.memories, runtime_client=runtime)
        asyncio.run(
            self._collect(
                harness.stream_chat(
                    {
                        "session_id": "conv-temp",
                        "text": "临时问题",
                        "chat_mode": "react",
                        "memory_mode": "temporary",
                    }
                )
            )
        )

        self.assertEqual(self.conversations.list_topics(), [])
        self.assertFalse((self.root / "memory" / "MEMORY.md").exists())
        self.assertEqual(len(runtime.delete_calls), 1)

    def test_no_save_conversation_does_not_persist_or_extract_memory(self) -> None:
        runtime = _FakeRuntimeClient()
        harness = MemoryHarness(conversation_store=self.conversations, memory_store=self.memories, runtime_client=runtime)
        asyncio.run(
            self._collect(
                harness.stream_chat(
                    {
                        "session_id": "conv-private",
                        "text": "这个话题之后都别记录。请记住：我喜欢咖啡。",
                        "chat_mode": "react",
                        "memory_mode": "persistent",
                    }
                )
            )
        )

        detail = self.conversations.get_topic_detail("conv-private")
        self.assertIsNotNone(detail)
        self.assertTrue(detail["meta"]["save_disabled"])
        self.assertEqual(detail["messages"], [])
        self.assertEqual(self.memories.search("咖啡"), [])

    def test_cleanup_failure_writes_cleanup_record_without_transcript(self) -> None:
        runtime = _FakeRuntimeClient(fail_delete=True)
        harness = MemoryHarness(conversation_store=self.conversations, memory_store=self.memories, runtime_client=runtime)
        events = asyncio.run(
            self._collect(
                harness.stream_chat(
                    {
                        "session_id": "conv-1",
                        "text": "你好",
                        "chat_mode": "react",
                        "memory_mode": "persistent",
                    }
                )
            )
        )
        self.assertEqual(events[-1][0], "done")
        cleanup_files = list((self.root / "conversations" / "temp").glob("*.json"))
        self.assertEqual(len(cleanup_files), 1)
        cleanup_text = cleanup_files[0].read_text(encoding="utf-8")
        self.assertNotIn("你好", cleanup_text)
        self.assertNotIn("好的", cleanup_text)

    def test_cleanup_payload_level_failure_writes_cleanup_record(self) -> None:
        runtime = _FakeRuntimeClient()

        async def fail_delete(session_id):
            runtime.delete_calls.append(session_id)
            return {"ok": False, "error": "still exists"}

        runtime.delete_runtime_session = fail_delete
        harness = MemoryHarness(conversation_store=self.conversations, memory_store=self.memories, runtime_client=runtime)

        events = asyncio.run(
            self._collect(
                harness.stream_chat(
                    {
                        "session_id": "conv-1",
                        "text": "你好",
                        "chat_mode": "react",
                        "memory_mode": "persistent",
                    }
                )
            )
        )

        self.assertEqual(events[-1][0], "done")
        cleanup_files = list((self.root / "conversations" / "temp").glob("*.json"))
        self.assertEqual(len(cleanup_files), 1)
        self.assertIn("still exists", cleanup_files[0].read_text(encoding="utf-8"))

    async def _collect(self, stream):
        return [(event, payload) async for event, payload in stream]
