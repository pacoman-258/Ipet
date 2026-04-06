from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path
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

    def test_list_topics_ignores_blank_drafts(self) -> None:
        self.store.create_topic(topic_id="blank-topic")
        self.store.create_topic(topic_id="saved-topic")
        self.store.append_exchange("saved-topic", user_text="Hi", assistant_text="There")

        topics = self.store.list_topics()
        self.assertEqual([item["topic_id"] for item in topics], ["saved-topic"])

    def test_delete_topic_removes_directory(self) -> None:
        self.store.create_topic(topic_id="demo-topic")
        self.store.append_exchange("demo-topic", user_text="hello", assistant_text="world")

        self.assertTrue(self.store.delete_topic("demo-topic"))
        self.assertFalse((self.root / "demo-topic").exists())
        self.assertFalse(self.store.delete_topic("demo-topic"))

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

    def test_runtime_snapshot_reuses_cache_until_topic_updates(self) -> None:
        self.store.create_topic(topic_id="demo-topic")
        self.store.append_exchange("demo-topic", user_text="hello", assistant_text="world")

        snapshot_a = self.store.get_runtime_snapshot("demo-topic")
        snapshot_b = self.store.get_runtime_snapshot("demo-topic")

        self.assertIsNotNone(snapshot_a)
        self.assertIsNotNone(snapshot_b)
        self.assertEqual(len(self.store._snapshot_cache), 1)
        self.assertEqual(snapshot_a.full_messages, snapshot_b.full_messages)
        self.assertEqual(snapshot_a.model_messages, snapshot_b.model_messages)

        self.store.append_exchange("demo-topic", user_text="next", assistant_text="turn")
        snapshot_c = self.store.get_runtime_snapshot("demo-topic")

        self.assertIsNotNone(snapshot_c)
        self.assertEqual(len(snapshot_c.full_messages), 4)
        self.assertNotEqual(snapshot_a.full_messages, snapshot_c.full_messages)

    def test_runtime_snapshot_invalidates_after_summary_and_delete(self) -> None:
        self.store.create_topic(topic_id="demo-topic")
        for index in range(1, 11):
            self.store.append_exchange(
                "demo-topic",
                user_text=f"user-{index}",
                assistant_text=f"assistant-{index}",
            )

        snapshot_before = self.store.get_runtime_snapshot("demo-topic")
        self.assertIsNotNone(snapshot_before)
        self.assertIn("demo-topic", self.store._snapshot_cache)

        candidate = self.store.get_pending_mini_summary("demo-topic", 10)
        self.assertIsNotNone(candidate)
        self.store.apply_mini_summary("demo-topic", candidate, "Mini summary cache test")
        snapshot_after = self.store.get_runtime_snapshot("demo-topic")

        self.assertIsNotNone(snapshot_after)
        self.assertNotEqual(snapshot_before.model_messages, snapshot_after.model_messages)

        self.assertTrue(self.store.delete_topic("demo-topic"))
        self.assertNotIn("demo-topic", self.store._snapshot_cache)

    def test_load_meta_backfills_long_term_memory_marker_defaults(self) -> None:
        meta = self.store.create_topic(topic_id="demo-topic")
        meta.pop("last_long_term_memory_saved_marker", None)
        meta.pop("last_long_term_memory_dismissed_marker", None)
        meta_path = self.root / "demo-topic" / "meta.json"
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        loaded = self.store.load_meta("demo-topic") or {}

        self.assertIn("last_long_term_memory_saved_marker", loaded)
        self.assertIn("last_long_term_memory_dismissed_marker", loaded)
        self.assertIsNone(loaded["last_long_term_memory_saved_marker"])
        self.assertIsNone(loaded["last_long_term_memory_dismissed_marker"])

    def test_update_long_term_memory_marker_persists_values_and_delete_cleans_state(self) -> None:
        self.store.create_topic(topic_id="demo-topic")
        self.store.append_exchange("demo-topic", user_text="hello", assistant_text="world")
        snapshot = self.store.get_runtime_snapshot("demo-topic")

        self.assertIsNotNone(snapshot)
        self.assertIn("demo-topic", self.store._snapshot_cache)

        updated = self.store.update_long_term_memory_marker(
            "demo-topic",
            saved_marker="major:30",
            dismissed_marker="explicit:2",
        )

        self.assertIsNotNone(updated)
        self.assertEqual(updated["last_long_term_memory_saved_marker"], "major:30")
        self.assertEqual(updated["last_long_term_memory_dismissed_marker"], "explicit:2")
        self.assertNotIn("demo-topic", self.store._snapshot_cache)

        loaded = self.store.load_meta("demo-topic") or {}
        self.assertEqual(loaded["last_long_term_memory_saved_marker"], "major:30")
        self.assertEqual(loaded["last_long_term_memory_dismissed_marker"], "explicit:2")

        self.assertTrue(self.store.delete_topic("demo-topic"))
        self.assertIsNone(self.store.load_meta("demo-topic"))
        self.assertNotIn("demo-topic", self.store._snapshot_cache)

    def test_windows_utf8_bom_topic_files_are_still_readable(self) -> None:
        meta = self.store.create_topic(topic_id="demo-topic")
        meta["title"] = "demo-topic"
        meta["preview"] = "world"
        meta["persisted"] = True
        meta["assistant_turn_count"] = 1
        meta["pending_summary_start_assistant_turn"] = 1
        meta["pending_summary_end_assistant_turn"] = 1
        detail_dir = self.root / "demo-topic"
        meta_path = detail_dir / "meta.json"
        summary_path = detail_dir / "summary.json"
        full_path = detail_dir / "full.jsonl"

        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8-sig")
        summary_path.write_text(
            json.dumps(
                {
                    "topic_id": "demo-topic",
                    "updated_at": meta["updated_at"],
                    "blocks": [
                        {
                            "type": BLOCK_RAW_MESSAGES,
                            "start_assistant_turn": 1,
                            "end_assistant_turn": 1,
                            "created_at": meta["updated_at"],
                            "updated_at": meta["updated_at"],
                            "messages": [
                                {"role": "user", "content": "hello", "created_at": meta["updated_at"], "assistant_turn": 1},
                                {
                                    "role": "assistant",
                                    "content": "world",
                                    "created_at": meta["updated_at"],
                                    "assistant_turn": 1,
                                },
                            ],
                        }
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8-sig",
        )
        full_path.write_text(
            "\n".join(
                [
                    json.dumps(
                        {"role": "user", "content": "hello", "created_at": meta["updated_at"], "assistant_turn": 1},
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {"role": "assistant", "content": "world", "created_at": meta["updated_at"], "assistant_turn": 1},
                        ensure_ascii=False,
                    ),
                ]
            )
            + "\n",
            encoding="utf-8-sig",
        )

        topics = self.store.list_topics()
        detail = self.store.get_topic_detail("demo-topic") or {}

        self.assertEqual([item["topic_id"] for item in topics], ["demo-topic"])
        self.assertEqual([item["content"] for item in detail["messages"]], ["hello", "world"])
        self.assertEqual(detail["summary"]["blocks"][0]["type"], BLOCK_RAW_MESSAGES)


if __name__ == "__main__":
    unittest.main()
