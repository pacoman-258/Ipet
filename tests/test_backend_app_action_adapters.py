from __future__ import annotations

import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from human_ops import ReviewableProposal

import backend.app as backend_app


ROOT_DIR = Path(__file__).resolve().parents[1]


class BackendAppActionAdapterTests(unittest.IsolatedAsyncioTestCase):
    def _import_adapters(self):
        try:
            return importlib.import_module("backend.app_action_adapters")
        except ModuleNotFoundError as exc:
            if exc.name == "backend.app_action_adapters":
                self.fail("backend.app_action_adapters module should be importable")
            raise

    async def test_module_import_does_not_import_backend_app(self) -> None:
        app_was_loaded = "backend.app" in sys.modules

        adapters = self._import_adapters()

        source = Path(adapters.__file__).read_text(encoding="utf-8")
        self.assertNotIn("backend.app", source)
        if not app_was_loaded:
            self.assertNotIn("backend.app", sys.modules)

    async def test_send_desktop_command_passes_command_path_and_timeout_to_client(self) -> None:
        adapters = self._import_adapters()
        with tempfile.TemporaryDirectory() as temp_dir:
            command_path = Path(temp_dir) / ".pet_desktop_command.json"
            calls: list[tuple[str, dict[str, object] | None, Path, float]] = []

            async def send_command(command_type: str, payload: dict[str, object] | None, *, command_path: Path, timeout_sec: float) -> dict[str, object]:
                calls.append((command_type, payload, command_path, timeout_sec))
                return {"ok": True}

            deps = adapters.AppActionAdapterDependencies(
                command_path=command_path,
                send_desktop_command=send_command,
            )

            result = await adapters.send_desktop_command(
                "human_ops_click",
                {"x": 10, "y": 20},
                deps=deps,
            )

            self.assertEqual(result, {"ok": True})
            self.assertEqual(calls, [("human_ops_click", {"x": 10, "y": 20}, command_path, 8.0)])

    async def test_click_branch_uses_backend_app_patch_point(self) -> None:
        proposal = ReviewableProposal.act(
            action_type="click",
            summary="Ipet 想点击：确认",
            payload={"x": 10, "y": 20, "target": "确认"},
        )

        with mock.patch.object(backend_app, "_perform_human_ops_click", new=mock.AsyncMock(return_value={"clicked": True})) as click_mock:
            result = await backend_app._perform_human_ops_action(proposal)

        self.assertEqual(result, {"clicked": True})
        click_mock.assert_awaited_once_with(proposal)

    async def test_non_click_action_delegates_to_desktop_command_client_helper(self) -> None:
        adapters = self._import_adapters()
        proposal = ReviewableProposal.act(
            action_type="type_text",
            summary="Ipet 想输入：你好",
            payload={"text": "你好", "target": "聊天框"},
        )

        async def capture_send_command(command_type: str, payload: dict[str, object] | None, *, command_path: Path, timeout_sec: float) -> dict[str, object]:
            self.assertEqual(command_type, "human_ops_type_text")
            self.assertEqual(command_path, ROOT_DIR / ".pet_desktop_command.json")
            self.assertEqual(timeout_sec, 8.0)
            self.assertEqual(payload, {"text": "你好", "label": "聊天框"})
            return {"source": "adapter"}

        deps = adapters.AppActionAdapterDependencies(
            command_path=ROOT_DIR / ".pet_desktop_command.json",
            send_desktop_command=capture_send_command,
        )

        with mock.patch.object(
            adapters._desktop_command_client_helpers,
            "perform_human_ops_action",
            new=mock.AsyncMock(return_value={"desktop": True}),
        ) as action_mock:
            result = await adapters.perform_human_ops_action(proposal, deps=deps)

        self.assertEqual(result, {"desktop": True})
        action_mock.assert_awaited_once()
        call = action_mock.await_args
        self.assertIs(call.args[0], proposal)
        self.assertTrue(call.kwargs["send_command"])  # callback is forwarded


if __name__ == "__main__":
    unittest.main()
