from __future__ import annotations

import unittest
from pathlib import Path
import shutil
from uuid import uuid4

from backend.chat_topics import (
    BLOCK_MAJOR_SUMMARY,
    BLOCK_MINI_SUMMARY,
    BLOCK_RAW_MESSAGES,
    DEFAULT_TOPIC_TITLE,
    TopicStore,
)


class TopicStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root = Path(__file__).resolve().parent / ".tmp_chat_topics" / f"tmp_{uuid4().hex}"
        self.tmp_root.mkdir(parents=True, exist_ok=True)
        self.root = self.tmp_root / "chat_topics"
        self.store = TopicStore(self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def test_create_topic_writes_expected_files(self) -> None:
        meta = self.store.create_topic(topic_id="demo-topic")

        self.assertEqual(meta["topic_id"], "demo-topic")
        self.assertEqual(meta["title"], DEFAULT_TOPIC_TITLE)
        self.assertTrue((self.root / "demo-topic" / "meta.json").exists())
        self.assertTrue((self.root / "demo-topic" / "full.jsonl").exists())
        self.assertTrue((self.root / "demo-topic" / "summary.json").exists())

    def test_append_exchange_updates_full_and_summary_files(self) -> None:
        self.store.create_topic(topic_id="demo-topic")
        self.store.append_exchange("demo-topic", user_text="Hello", assistant_text="World")
        detail = self.store.get_topic_detail("demo-topic") or {}

        self.assertEqual([item["content"] for item in detail["messages"]], ["Hello", "World"])
        blocks = detail["summary"]["blocks"]
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["type"], BLOCK_RAW_MESSAGES)
        self.assertEqual([item["content"] for item in blocks[0]["messages"]], ["Hello", "World"])
        self.assertEqual(detail["meta"]["title"], "Hello")

    def test_apply_mini_summary_replaces_first_pending_turn_window(self) -> None:
        self.store.create_topic(topic_id="demo-topic")
        for index in range(1, 13):
            self.store.append_exchange(
                "demo-topic",
                user_text=f"user-{index}",
                assistant_text=f"assistant-{index}",
            )

        candidate = self.store.get_pending_mini_summary("demo-topic", 10)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["start_assistant_turn"], 1)
        self.assertEqual(candidate["end_assistant_turn"], 10)

        self.store.apply_mini_summary("demo-topic", candidate, "Mini summary for turns 1-10")
        detail = self.store.get_topic_detail("demo-topic") or {}
        blocks = detail["summary"]["blocks"]

        self.assertEqual([block["type"] for block in blocks], [BLOCK_MINI_SUMMARY, BLOCK_RAW_MESSAGES])
        self.assertEqual(blocks[0]["content"], "Mini summary for turns 1-10")
        self.assertEqual(blocks[1]["start_assistant_turn"], 11)
        self.assertEqual(detail["meta"]["mini_summary_count"], 1)

        model_messages = self.store.build_model_messages("demo-topic")
        self.assertEqual(model_messages[0]["role"], "system")
        self.assertIn("Mini summary for turns 1-10", model_messages[0]["content"])
        self.assertEqual(model_messages[-1]["content"], "assistant-12")

    def test_apply_major_summary_replaces_three_mini_summaries(self) -> None:
        self.store.create_topic(topic_id="demo-topic")
        for index in range(1, 31):
            self.store.append_exchange(
                "demo-topic",
                user_text=f"user-{index}",
                assistant_text=f"assistant-{index}",
            )

        for mini_index in range(1, 4):
            candidate = self.store.get_pending_mini_summary("demo-topic", 10)
            self.assertIsNotNone(candidate)
            self.store.apply_mini_summary(
                "demo-topic",
                candidate,
                f"Mini summary block {mini_index}",
            )

        major_candidate = self.store.get_pending_major_summary("demo-topic", 3)
        self.assertIsNotNone(major_candidate)
        self.assertEqual(major_candidate["start_assistant_turn"], 1)
        self.assertEqual(major_candidate["end_assistant_turn"], 30)

        self.store.apply_major_summary("demo-topic", major_candidate, "Major summary block")
        detail = self.store.get_topic_detail("demo-topic") or {}
        blocks = detail["summary"]["blocks"]

        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["type"], BLOCK_MAJOR_SUMMARY)
        self.assertEqual(blocks[0]["content"], "Major summary block")
        self.assertEqual(detail["meta"]["mini_summary_count"], 3)
        self.assertEqual(detail["meta"]["major_summary_count"], 1)


if __name__ == "__main__":
    unittest.main()
