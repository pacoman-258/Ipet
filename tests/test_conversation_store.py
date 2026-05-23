from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from backend.conversation_store import (
    ConversationStore,
    MEMORY_MODE_PERSISTENT,
    MEMORY_MODE_TEMPORARY,
    MEMORY_MODES,
)


class ConversationStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parent / ".tmp_conversation_store" / uuid4().hex
        self.store = ConversationStore(self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_create_append_list_detail_and_delete_conversation(self) -> None:
        meta = self.store.create_conversation(title="新对话")
        conversation_id = meta["conversation_id"]

        saved = self.store.append_turn(
            conversation_id,
            turn_id="turn-1",
            user_text="我喜欢红茶",
            assistant_text="记住啦",
            runtime="astrbot",
            runtime_session_id="ipet-temp-turn-1",
            memory_mode=MEMORY_MODE_PERSISTENT,
            save_policy="save",
            metadata={"chat_mode": "react"},
        )
        self.assertTrue(saved["ok"])
        self.assertTrue(saved["saved"])
        self.assertEqual(saved["turn"]["turn_id"], "turn-1")

        topics = self.store.list_topics()
        self.assertEqual([item["topic_id"] for item in topics], [conversation_id])
        self.assertEqual(topics[0]["title"], "我喜欢红茶")
        self.assertEqual(topics[0]["assistant_turn_count"], 1)
        self.assertTrue(topics[0]["supports_history_detail"])
        self.assertTrue(topics[0]["supports_delete"])

        detail = self.store.get_topic_detail(conversation_id)
        self.assertIsNotNone(detail)
        self.assertEqual([item["role"] for item in detail["messages"]], ["user", "assistant"])
        self.assertEqual(detail["messages"][0]["content"], "我喜欢红茶")
        turn_record = json.loads((self.root / "conversations" / conversation_id / "turns.jsonl").read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(turn_record["status"], "completed")
        self.assertEqual(turn_record["user"], {"role": "user", "content": "我喜欢红茶"})
        self.assertEqual(turn_record["assistant"], {"role": "assistant", "content": "记住啦"})
        self.assertIn("我喜欢红茶", (self.root / "conversations" / conversation_id / "conversation.md").read_text(encoding="utf-8"))
        self.assertIn(conversation_id, (self.root / "INDEX.md").read_text(encoding="utf-8"))

        payload = self.store.delete_conversation(conversation_id)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["deleted_topic_id"], conversation_id)
        self.assertEqual(payload["topics"], [])
        self.assertEqual(self.store.list_topics(), [])

    def test_create_and_load_state_include_plan_defaults_and_transcript_path(self) -> None:
        self.assertEqual(MEMORY_MODES, {MEMORY_MODE_PERSISTENT, MEMORY_MODE_TEMPORARY})
        meta = self.store.create_conversation(title="默认字段")
        conversation_id = meta["conversation_id"]
        state = self.store.load_state(conversation_id)
        self.assertIsNotNone(state)
        self.assertEqual(state["session_id"], conversation_id)
        self.assertEqual(state["runtime"], "ipet")
        self.assertEqual(state["source"], "ipet")
        self.assertEqual(state["tags"], [])
        self.assertEqual(state["last_summary_turn"], 0)
        self.assertEqual(state["last_memory_extracted_turn"], 0)
        self.assertEqual(state["save_disabled_reason"], "")
        self.assertEqual(self.store.transcript_path(conversation_id), self.root / "conversations" / conversation_id / "conversation.md")
        self.assertEqual(self.store.conversation_path(conversation_id), self.store.transcript_path(conversation_id))

    def test_create_conversation_existing_id_is_non_destructive(self) -> None:
        meta = self.store.create_conversation(conversation_id="stable-id", title="原始标题")
        conversation_id = meta["conversation_id"]
        self.store.append_turn(
            conversation_id,
            turn_id="turn-1",
            user_text="保留下来",
            assistant_text="不会清空",
            runtime="astrbot",
            runtime_session_id="ipet-temp-turn-1",
            memory_mode=MEMORY_MODE_PERSISTENT,
            save_policy="save",
        )

        existing = self.store.create_conversation(conversation_id=conversation_id, title="新标题")

        self.assertEqual(existing["conversation_id"], conversation_id)
        self.assertEqual(existing["assistant_turn_count"], 1)
        self.assertEqual([item["content"] for item in self.store.load_messages(conversation_id)], ["保留下来", "不会清空"])
        self.assertIn("保留下来", self.store.transcript_path(conversation_id).read_text(encoding="utf-8"))

    def test_load_state_backfills_final_plan_defaults_for_old_state(self) -> None:
        conversation_id = "old-state"
        state_dir = self.root / "conversations" / conversation_id
        state_dir.mkdir(parents=True)
        (state_dir / "state.json").write_text(
            json.dumps({"conversation_id": conversation_id, "title": "旧状态"}, ensure_ascii=False),
            encoding="utf-8",
        )

        state = self.store.load_state(conversation_id)
        self.assertIsNotNone(state)
        self.assertEqual(state["last_summary_turn"], 0)
        self.assertEqual(state["last_memory_extracted_turn"], 0)
        self.assertEqual(state["save_disabled_reason"], "")

    def test_json_writes_leave_no_temp_files_after_normal_write(self) -> None:
        meta = self.store.create_conversation(title="原子写")
        conversation_id = meta["conversation_id"]
        self.store.disable_saving(conversation_id, reason="check_atomic_json")

        conversation_dir = self.root / "conversations" / conversation_id
        leftovers = [path.name for path in conversation_dir.iterdir() if path.name.startswith(".") and path.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_append_turn_accepts_status_and_load_context_recent_turns(self) -> None:
        meta = self.store.create_conversation(title="状态测试")
        conversation_id = meta["conversation_id"]
        self.store.append_turn(
            conversation_id,
            turn_id="turn-1",
            user_text="第一轮",
            assistant_text="一",
            runtime="astrbot",
            runtime_session_id="ipet-temp-turn-1",
            memory_mode=MEMORY_MODE_PERSISTENT,
            save_policy="save",
        )
        result = self.store.append_turn(
            conversation_id,
            turn_id="turn-2",
            user_text="第二轮",
            assistant_text="二",
            runtime="astrbot",
            runtime_session_id="ipet-temp-turn-2",
            memory_mode=MEMORY_MODE_PERSISTENT,
            save_policy="save",
            status="interrupted",
        )
        self.assertEqual(result["turn"]["status"], "interrupted")
        raw_records = [
            json.loads(line)
            for line in (self.root / "conversations" / conversation_id / "turns.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(raw_records[1]["status"], "interrupted")

        context = self.store.load_context(conversation_id, recent_turns=1)
        self.assertIsNotNone(context)
        self.assertEqual([item["content"] for item in context.recent_messages], ["第二轮", "二"])

    def test_temporary_mode_does_not_create_conversation_content(self) -> None:
        meta = self.store.create_conversation(title="临时", memory_mode=MEMORY_MODE_TEMPORARY, persisted=False)
        self.assertFalse(meta["persisted"])
        self.assertFalse((self.root / "conversations").exists())
        self.assertEqual(self.store.list_topics(), [])

    def test_save_disabled_state_skips_future_appends(self) -> None:
        meta = self.store.create_conversation(title="隐私对话")
        conversation_id = meta["conversation_id"]
        self.store.disable_saving(conversation_id, reason="user_requested_no_save")
        saved = self.store.append_turn(
            conversation_id,
            turn_id="turn-1",
            user_text="这段不要保存",
            assistant_text="好的",
            runtime="astrbot",
            runtime_session_id="ipet-temp-turn-1",
            memory_mode=MEMORY_MODE_PERSISTENT,
            save_policy="conversation_disabled",
        )
        self.assertFalse(saved["saved"])
        detail = self.store.get_topic_detail(conversation_id)
        self.assertIsNotNone(detail)
        self.assertEqual(detail["messages"], [])
        self.assertTrue(detail["meta"]["save_disabled"])

    def test_disable_saving_missing_conversation_creates_disabled_state(self) -> None:
        conversation_id = "missing-conversation"
        meta = self.store.disable_saving(conversation_id, reason="memory_harness_no_save")
        self.assertIsNotNone(meta)
        self.assertTrue(meta["save_disabled"])
        self.assertEqual(meta["save_disabled_reason"], "memory_harness_no_save")

        saved = self.store.append_turn(
            conversation_id,
            turn_id="turn-1",
            user_text="不要落盘",
            assistant_text="收到",
            runtime="astrbot",
            runtime_session_id="ipet-temp-turn-1",
            memory_mode=MEMORY_MODE_PERSISTENT,
            save_policy="conversation_disabled",
        )
        self.assertFalse(saved["saved"])
        detail = self.store.get_topic_detail(conversation_id)
        self.assertIsNotNone(detail)
        self.assertEqual(detail["messages"], [])
        self.assertTrue(detail["meta"]["save_disabled"])

    def test_topic_payloads_are_ipet_owned_after_astrbot_append(self) -> None:
        meta = self.store.create_conversation(title="新对话")
        conversation_id = meta["conversation_id"]
        self.store.append_turn(
            conversation_id,
            turn_id="turn-1",
            user_text="运行时来自 AstrBot",
            assistant_text="历史归 Ipet 管",
            runtime="astrbot",
            runtime_session_id="ipet-temp-turn-1",
            memory_mode=MEMORY_MODE_PERSISTENT,
            save_policy="save",
        )

        topic = self.store.list_topics()[0]
        self.assertEqual(topic["runtime"], "ipet")
        self.assertEqual(topic["source"], "ipet")
        detail = self.store.get_topic_detail(conversation_id)
        self.assertIsNotNone(detail)
        self.assertEqual(detail["runtime"], "ipet")
        self.assertEqual(detail["topic"]["runtime"], "ipet")
        self.assertEqual(detail["topic"]["source"], "ipet")

    def test_cleanup_record_contains_no_transcript(self) -> None:
        path = self.store.write_cleanup_record(
            runtime_turn_id="turn-cleanup",
            runtime="astrbot",
            runtime_session_id="ipet-temp-turn-cleanup",
            error="delete failed",
        )
        self.assertIsInstance(path, Path)
        self.assertEqual(path, self.root / "temp" / "turn-cleanup.json")
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["runtime_session_id"], "ipet-temp-turn-cleanup")
        self.assertNotIn("user", payload)
        self.assertNotIn("assistant", payload)
        self.assertNotIn("content", json.dumps(payload, ensure_ascii=False))
