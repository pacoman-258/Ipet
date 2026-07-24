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

    async def test_click_adapter_forwards_ax_reference_without_fake_zero_coordinates(self) -> None:
        adapters = self._import_adapters()
        ax_ref = {"app_id": "finder", "role": "AXRow", "path": [2], "fingerprint": "copied"}
        proposal = ReviewableProposal.act(
            action_type="click",
            summary="选择文件",
            payload={"target_app": "Finder", "ax_ref": ax_ref, "label": "报告.pdf"},
        )
        send_mock = mock.AsyncMock(return_value={"ax_target_verified": True})
        deps = adapters.AppActionAdapterDependencies(
            command_path=ROOT_DIR / ".pet_desktop_command.json",
            send_desktop_command=send_mock,
        )

        result = await adapters.perform_human_ops_click(proposal, deps=deps)

        payload = send_mock.await_args.args[1]
        self.assertEqual(payload["ax_ref"], ax_ref)
        self.assertNotIn("x", payload)
        self.assertNotIn("y", payload)
        self.assertTrue(result["ax_target_verified"])

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

    async def test_playwright_action_stays_in_human_ops_instead_of_desktop_bridge(self) -> None:
        adapters = self._import_adapters()
        proposal = ReviewableProposal.act(
            action_type="playwright",
            summary="Ipet 想用 Playwright 打开网页",
            payload={"profile": "工作", "operation": "open", "url": "https://example.com"},
        ).approve()
        playwright_mock = mock.AsyncMock(return_value={"playwright_done": True, "output": "page snapshot"})
        desktop_mock = mock.AsyncMock()
        deps = adapters.AppActionAdapterDependencies(
            command_path=ROOT_DIR / ".pet_desktop_command.json",
            send_desktop_command=mock.AsyncMock(),
            perform_human_ops_action=desktop_mock,
            perform_playwright_action=playwright_mock,
        )

        result = await adapters.perform_human_ops_action(proposal, deps=deps)

        self.assertTrue(result["playwright_done"])
        playwright_mock.assert_awaited_once_with(proposal)
        desktop_mock.assert_not_awaited()

    async def test_playwright_native_approval_discloses_network_and_first_run_bootstrap(self) -> None:
        adapters = self._import_adapters()
        proposal = ReviewableProposal.act(
            action_type="playwright",
            summary="Ipet 想用 Playwright 打开 YouTube",
            payload={"profile": "个人", "operation": "open", "url": "https://www.youtube.com"},
        )
        calls: list[tuple[str, dict[str, object], float]] = []

        async def send_command(command_type, payload, *, command_path, timeout_sec):
            calls.append((command_type, payload, timeout_sec))
            return {"approved": True}

        deps = adapters.AppActionAdapterDependencies(
            command_path=ROOT_DIR / ".pet_desktop_command.json",
            send_desktop_command=send_command,
        )
        result = await adapters.request_native_human_ops_approval(proposal, deps=deps)

        self.assertTrue(result["approved"])
        self.assertEqual(calls[0][0], "human_ops_native_approval")
        self.assertIn("首次运行可能通过 npx 获取 @playwright/cli", calls[0][1]["message"])
        self.assertIn("可能访问目标网络地址", calls[0][1]["message"])
        self.assertIn("不会执行任意网页脚本", calls[0][1]["message"])
        self.assertIn("个人资料：个人", calls[0][1]["message"])
        self.assertIn("只读复制 cookies 与网页存储到 Ipet 私有持久快照", calls[0][1]["message"])
        self.assertIn("绝不写回原 Chrome 资料", calls[0][1]["message"])
        self.assertIn("attach 只连接唯一活跃且允许远程调试的所选资料", calls[0][1]["message"])

    async def test_full_authorization_notice_omits_typed_content_and_needs_delivery_ack(self) -> None:
        adapters = self._import_adapters()
        proposal = ReviewableProposal.act(
            action_type="type_text",
            summary="Ipet 想输入：绝密内容",
            payload={"target_app": "WeChat", "text": "绝密内容", "label": "聊天框"},
        )
        calls: list[tuple[str, dict[str, object], float]] = []

        async def send_command(command_type, payload, *, command_path, timeout_sec):
            calls.append((command_type, payload, timeout_sec))
            return {"notified": True, "method": "macos_notification"}

        deps = adapters.AppActionAdapterDependencies(
            command_path=ROOT_DIR / ".pet_desktop_command.json",
            send_desktop_command=send_command,
        )

        result = await adapters.notify_human_ops_action(
            proposal,
            task_id="task-full",
            deps=deps,
        )

        self.assertTrue(result["notified"])
        self.assertEqual(calls[0][0], "human_ops_native_approval")
        self.assertTrue(calls[0][1]["notice_only"])
        self.assertEqual(calls[0][1]["task_id"], "task-full")
        self.assertIn("输入文字", calls[0][1]["message"])
        self.assertIn("WeChat", calls[0][1]["message"])
        self.assertNotIn("绝密内容", calls[0][1]["message"])
        self.assertEqual(calls[0][2], 8)

    async def test_filesystem_action_uses_configured_root_without_desktop_command(self) -> None:
        adapters = self._import_adapters()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            proposal = ReviewableProposal.act(
                action_type="file_write",
                summary="Ipet 想写入文件",
                payload={"path": "note.txt", "content": "hello"},
            ).approve()
            deps = adapters.AppActionAdapterDependencies(
                command_path=ROOT_DIR / ".pet_desktop_command.json",
                send_desktop_command=mock.AsyncMock(),
                filesystem_roots=(root,),
            )

            result = await adapters.perform_human_ops_action(proposal, deps=deps)

            self.assertTrue(result["written"])
            self.assertEqual((root / "note.txt").read_text(encoding="utf-8"), "hello")
            deps.send_desktop_command.assert_not_awaited()

    async def test_filesystem_native_approval_discloses_scope_and_rejection_effect(self) -> None:
        adapters = self._import_adapters()
        proposal = ReviewableProposal.act(
            action_type="file_delete",
            summary="Ipet 想删除文件",
            payload={"path": "notes.txt"},
        )

        async def send_command(command_type, payload, *, command_path, timeout_sec):
            return {"approved": False, "message": payload["message"]}

        deps = adapters.AppActionAdapterDependencies(
            command_path=ROOT_DIR / ".pet_desktop_command.json",
            send_desktop_command=send_command,
        )
        result = await adapters.request_native_human_ops_approval(proposal, deps=deps)

        self.assertFalse(result["approved"])
        self.assertIn("项目根目录或用户配置的允许根目录", result["message"])


if __name__ == "__main__":
    unittest.main()
