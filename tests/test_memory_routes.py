from __future__ import annotations

import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.ipet_memory_store import IpetMemoryStore
from backend.memory_routes import MemoryRouteDependencies, register_memory_routes
from human_ops.approvals import ReviewableProposal


class MemoryRoutesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parent / ".tmp_memory_routes" / uuid4().hex
        self.store = IpetMemoryStore(self.root)
        self.pending: dict[str, dict] = {}
        app = FastAPI()
        register_memory_routes(
            app,
            MemoryRouteDependencies(
                memory_store=self.store,
                pending_proposals=self.pending,
                normalize_private_config=lambda: {"memory": {"review_limit": 10}},
            ),
        )
        self.client = TestClient(app)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_catalog_edit_resolve_and_forget(self) -> None:
        memory = self.store.write_memory(
            title="待回访事项：答辩",
            summary="答辩后问我感觉怎么样。",
            kind="open_loop",
            source_conversation_id="friend",
        )["memory"]
        self.pending["candidate-1"] = {
            "proposal": ReviewableProposal.remember(
                summary="保存偏好",
                payload={
                    "operation": "save",
                    "memory": {"summary": "我喜欢红茶。", "kind": "preference"},
                },
            ),
            "session_id": "friend",
            "origin": "implicit",
            "status": "pending",
            "created_at": 1.0,
        }

        catalog = self.client.get("/api/memory").json()
        edited = self.client.patch(
            f"/api/memory/{memory['id']}",
            json={"changes": {"summary": "答辩后温柔地问我感觉怎么样。", "status": "resolved"}},
        ).json()
        forgotten = self.client.delete(f"/api/memory/{memory['id']}").json()

        self.assertEqual(catalog["records"][0]["id"], memory["id"])
        self.assertNotIn("path", catalog["records"][0])
        self.assertEqual(catalog["pending"][0]["proposal_id"], "candidate-1")
        self.assertEqual(edited["memory"]["status"], "resolved")
        self.assertIn("温柔地问我", edited["memory"]["summary"])
        self.assertTrue(forgotten["forgotten"])
        self.assertTrue(forgotten["source_conversation_retained"])
        self.assertEqual(self.client.get("/api/memory").json()["records"], [])


if __name__ == "__main__":
    unittest.main()
