from __future__ import annotations

import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from backend.ipet_memory_store import IpetMemoryStore, build_memory_candidates_from_turn


class IpetMemoryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parent / ".tmp_ipet_memory_store" / uuid4().hex
        self.store = IpetMemoryStore(self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_write_search_and_duplicate_suppression(self) -> None:
        first = self.store.write_memory(
            title="用户偏好：红茶",
            summary="用户喜欢红茶，不喜欢太甜的饮品。",
            evidence="用户说：我喜欢红茶，不喜欢太甜。",
            source_conversation_id="conv-1",
            source_turn_id="turn-1",
            tags=["preference"],
        )
        second = self.store.write_memory(
            title="用户偏好：红茶",
            summary="用户喜欢红茶，不喜欢太甜的饮品。",
            evidence="重复来源",
            source_conversation_id="conv-1",
            source_turn_id="turn-2",
            tags=["preference"],
        )

        self.assertTrue(first["saved"])
        self.assertFalse(second["saved"])
        self.assertTrue(second["duplicate"])
        index_text = (self.root / "MEMORY.md").read_text(encoding="utf-8")
        self.assertIn("用户偏好：红茶", index_text)
        results = self.store.search("红茶", limit=3)
        self.assertEqual(results[0]["title"], "用户偏好：红茶")
        self.assertIn("不喜欢太甜", results[0]["summary"])

    def test_build_memory_candidates_from_turn_keeps_only_durable_text(self) -> None:
        candidates = build_memory_candidates_from_turn(
            conversation_id="conv-1",
            turn_id="turn-1",
            user_text="请记住：我以后写 Python 测试时偏好 unittest。",
            assistant_text="好的，我会记住。",
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["summary"], "我以后写 Python 测试时偏好 unittest。")
        self.assertTrue(candidates[0]["title"].startswith("用户偏好：我以后写 Python"))
        self.assertIn("unittest", candidates[0]["summary"])
        self.assertIn("preference", candidates[0]["tags"])

    def test_distinct_preferences_do_not_duplicate_by_generic_title(self) -> None:
        red_tea = build_memory_candidates_from_turn(
            conversation_id="conv-1",
            turn_id="turn-8",
            user_text="请记住：我喜欢红茶。",
            assistant_text="",
        )[0]
        coffee = build_memory_candidates_from_turn(
            conversation_id="conv-1",
            turn_id="turn-9",
            user_text="请记住：我喜欢咖啡。",
            assistant_text="",
        )[0]

        first = self.store.write_memory(**red_tea)
        second = self.store.write_memory(**coffee)

        self.assertTrue(first["saved"])
        self.assertTrue(second["saved"])
        self.assertFalse(second["duplicate"])

    def test_more_specific_summary_is_not_duplicate_by_substring(self) -> None:
        first = self.store.write_memory(
            title="用户偏好：红茶",
            summary="用户喜欢红茶。",
            evidence="用户说：我喜欢红茶。",
            source_conversation_id="conv-1",
            source_turn_id="turn-13",
            tags=["preference"],
        )
        second = self.store.write_memory(
            title="用户偏好：红茶和咖啡",
            summary="用户喜欢红茶和咖啡。",
            evidence="用户说：我喜欢红茶和咖啡。",
            source_conversation_id="conv-1",
            source_turn_id="turn-14",
            tags=["preference"],
        )

        self.assertTrue(first["saved"])
        self.assertTrue(second["saved"])
        self.assertFalse(second["duplicate"])

    def test_search_restores_memory_metadata_from_disk_after_reload(self) -> None:
        self.store.write_memory(
            title="用户偏好：测试茶饮",
            summary="用户调试时喜欢喝乌龙茶。",
            evidence="用户说：调试时我喜欢喝乌龙茶。",
            source_conversation_id="conv-2",
            source_turn_id="turn-15",
            tags=["preference", "debug"],
        )

        new_store = IpetMemoryStore(self.root)
        results = new_store.search("乌龙茶", limit=1)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "用户偏好：测试茶饮")
        self.assertEqual(results[0]["summary"], "用户调试时喜欢喝乌龙茶。")
        self.assertEqual(results[0]["tags"], ["preference", "debug"])
        self.assertEqual(results[0]["source_conversation_id"], "conv-2")
        self.assertEqual(results[0]["source_turn_id"], "turn-15")

    def test_build_memory_candidates_accepts_constraint_always_and_never(self) -> None:
        constraint = build_memory_candidates_from_turn(
            conversation_id="conv-1",
            turn_id="turn-10",
            user_text="请记住：我的约束是不要自动提交。",
            assistant_text="",
        )
        always = build_memory_candidates_from_turn(
            conversation_id="conv-1",
            turn_id="turn-11",
            user_text="Remember: always use unittest for this project.",
            assistant_text="",
        )
        never = build_memory_candidates_from_turn(
            conversation_id="conv-1",
            turn_id="turn-12",
            user_text="Remember: never commit without approval.",
            assistant_text="",
        )

        self.assertEqual(len(constraint), 1)
        self.assertEqual(len(always), 1)
        self.assertEqual(len(never), 1)
        self.assertIn("plan", constraint[0]["tags"])
        self.assertTrue(constraint[0]["title"].startswith("用户约束与计划："))

    def test_build_memory_candidates_ignores_transient_and_secret_text(self) -> None:
        transient = build_memory_candidates_from_turn(
            conversation_id="conv-1",
            turn_id="turn-2",
            user_text="帮我算一下 1+1",
            assistant_text="2",
        )
        secret = build_memory_candidates_from_turn(
            conversation_id="conv-1",
            turn_id="turn-3",
            user_text="请记住 token=abc123",
            assistant_text="我不能保存密钥。",
        )
        self.assertEqual(transient, [])
        self.assertEqual(secret, [])

    def test_assistant_memory_ack_does_not_make_transient_user_text_durable(self) -> None:
        candidates = build_memory_candidates_from_turn(
            conversation_id="conv-1",
            turn_id="turn-16",
            user_text="帮我算一下 1+1",
            assistant_text="好的，我会记住。",
        )

        self.assertEqual(candidates, [])

    def test_build_memory_candidates_rejects_common_sensitive_forms(self) -> None:
        bare_password = build_memory_candidates_from_turn(
            conversation_id="conv-1",
            turn_id="turn-4",
            user_text="请记住我的 password",
            assistant_text="",
        )
        assigned_private_key = build_memory_candidates_from_turn(
            conversation_id="conv-1",
            turn_id="turn-5",
            user_text="请记住 private_key=abc",
            assistant_text="",
        )
        openai_style_key = build_memory_candidates_from_turn(
            conversation_id="conv-1",
            turn_id="turn-6",
            user_text="请记住 sk-abc_def-1234567890",
            assistant_text="",
        )

        self.assertEqual(bare_password, [])
        self.assertEqual(assigned_private_key, [])
        self.assertEqual(openai_style_key, [])

    def test_write_memory_rejects_summary_with_sensitive_keyword(self) -> None:
        result = self.store.write_memory(
            title="敏感信息",
            summary="用户让我保存 password",
            evidence="用户说：请记住我的 password",
            source_conversation_id="conv-1",
            source_turn_id="turn-7",
            tags=["preference"],
        )

        self.assertTrue(result["ok"])
        self.assertFalse(result["saved"])
        self.assertFalse(result["duplicate"])
        self.assertEqual(result["reason"], "empty_or_secret")
