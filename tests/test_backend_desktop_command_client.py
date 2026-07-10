from __future__ import annotations

import asyncio
import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from human_ops import ReviewableProposal


ROOT_DIR = Path(__file__).resolve().parents[1]


class DesktopCommandClientTests(unittest.IsolatedAsyncioTestCase):
    def _import_client(self):
        try:
            return importlib.import_module("backend.desktop_command_client")
        except ModuleNotFoundError as exc:
            if exc.name == "backend.desktop_command_client":
                self.fail("backend.desktop_command_client module should be importable")
            raise

    async def test_module_import_does_not_import_backend_app(self) -> None:
        app_was_loaded = "backend.app" in sys.modules

        client = self._import_client()

        source = Path(client.__file__).read_text(encoding="utf-8")
        self.assertNotIn("backend.app", source)
        if not app_was_loaded:
            self.assertNotIn("backend.app", sys.modules)

    async def test_send_desktop_command_writes_command_and_reads_matching_response(self) -> None:
        client = self._import_client()
        with tempfile.TemporaryDirectory() as temp_dir:
            command_path = Path(temp_dir) / ".pet_desktop_command.json"

            async def respond() -> None:
                for _ in range(50):
                    if command_path.exists():
                        break
                    await asyncio.sleep(0.01)
                command = json.loads(command_path.read_text(encoding="utf-8"))
                response_path = Path(command["payload"]["response_path"])
                response_path.write_text(
                    json.dumps(
                        {
                            "nonce": command["nonce"],
                            "status": "success",
                            "ok": True,
                            "result": {"frame": {"capture_backend": "test"}},
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

            responder = asyncio.create_task(respond())
            result = await client.send_desktop_command(
                "active_vision_capture",
                {"mode": "desktop_survey", "target": "screen"},
                command_path=command_path,
                timeout_sec=1,
            )
            await responder

            command = json.loads(command_path.read_text(encoding="utf-8"))
            self.assertEqual(command["type"], "active_vision_capture")
            self.assertEqual(command["payload"]["mode"], "desktop_survey")
            self.assertEqual(command["payload"]["target"], "screen")
            self.assertTrue(command["payload"]["response_path"].endswith(".response.json"))
            self.assertIsInstance(command["timestamp_ns"], int)
            self.assertEqual(result, {"frame": {"capture_backend": "test"}})

    async def test_send_desktop_command_ignores_nonce_mismatch_until_matching_response(self) -> None:
        client = self._import_client()
        with tempfile.TemporaryDirectory() as temp_dir:
            command_path = Path(temp_dir) / ".pet_desktop_command.json"

            async def respond() -> None:
                for _ in range(50):
                    if command_path.exists():
                        break
                    await asyncio.sleep(0.01)
                command = json.loads(command_path.read_text(encoding="utf-8"))
                response_path = Path(command["payload"]["response_path"])
                response_path.write_text(
                    json.dumps(
                        {
                            "nonce": "not-" + command["nonce"],
                            "status": "success",
                            "ok": True,
                            "result": {"wrong": True},
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                for _ in range(50):
                    if not response_path.exists():
                        break
                    await asyncio.sleep(0.01)
                response_path.write_text(
                    json.dumps(
                        {
                            "nonce": command["nonce"],
                            "status": "success",
                            "ok": True,
                            "result": {"matched": True},
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

            responder = asyncio.create_task(respond())
            result = await client.send_desktop_command(
                "human_ops_click",
                {"x": 10, "y": 20},
                command_path=command_path,
                timeout_sec=1,
            )
            await responder

            self.assertEqual(result, {"matched": True})

    async def test_perform_human_ops_action_preserves_click_type_and_key_shapes(self) -> None:
        client = self._import_client()
        calls: list[tuple[str, dict[str, Any], float]] = []

        async def send_command(command_type: str, payload: dict[str, Any], *, timeout_sec: float) -> dict[str, Any]:
            calls.append((command_type, dict(payload), timeout_sec))
            return {"source": "desktop"}

        click = ReviewableProposal.act(
            action_type="click",
            summary="Ipet 想点击：确认",
            payload={"x": "10.6", "y": "20.2", "target": "确认"},
        )
        typed = ReviewableProposal.act(
            action_type="type_text",
            summary="Ipet 想输入：你好",
            payload={"text": "你好", "target": "聊天框"},
        )
        pressed = ReviewableProposal.act(
            action_type="key_press",
            summary="Ipet 想按下回车：发送",
            payload={"key": "Enter", "target": "发送"},
        )

        click_result = await client.perform_human_ops_action(click, send_command=send_command)
        type_result = await client.perform_human_ops_action(typed, send_command=send_command)
        key_result = await client.perform_human_ops_action(pressed, send_command=send_command)

        self.assertEqual(
            calls,
            [
                ("human_ops_click", {"x": 11, "y": 20, "label": "确认"}, 5),
                ("human_ops_type_text", {"text": "你好", "label": "聊天框"}, 5),
                ("human_ops_key_press", {"key": "enter", "label": "发送"}, 5),
            ],
        )
        self.assertEqual(click_result, {"clicked": True, "x": 11, "y": 20, "label": "确认", "source": "desktop"})
        self.assertEqual(type_result, {"typed": True, "text": "你好", "label": "聊天框", "source": "desktop"})
        self.assertEqual(key_result, {"pressed": True, "key": "enter", "label": "发送", "source": "desktop"})

    async def test_backend_app_no_longer_inlines_desktop_response_polling(self) -> None:
        source = (ROOT_DIR / "backend" / "app.py").read_text(encoding="utf-8")

        self.assertNotIn("tmp_path.replace(DESKTOP_COMMAND_PATH)", source)
        self.assertNotIn("desktop command timed out", source)


if __name__ == "__main__":
    unittest.main()
