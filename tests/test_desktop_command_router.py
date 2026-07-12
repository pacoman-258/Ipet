from __future__ import annotations

import ast
import importlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import main


ROOT_DIR = Path(__file__).resolve().parents[1]


def _parse_source(relative_path: str) -> ast.Module:
    path = ROOT_DIR / relative_path
    if not path.exists():
        raise AssertionError(f"{relative_path} should exist")
    return ast.parse(path.read_text(encoding="utf-8"))


def _router_module():
    try:
        return importlib.import_module("app.desktop_command_router")
    except ImportError as exc:
        raise AssertionError("app.desktop_command_router module should be importable") from exc


class _FakeHost:
    def __init__(self) -> None:
        self.config = {"model_path": "", "vision": {"enabled": True}}
        self.motion_calls: list[dict[str, object]] = []
        self.expression_calls: list[dict[str, object]] = []
        self.refreshed: list[bool] = []
        self.applied = 0
        self.raised = 0
        self.activated = 0
        self.visible = True

    def refresh_motion_list(self, *, prefer_reset: bool) -> None:
        self.refreshed.append(prefer_reset)

    def apply_config_to_web(self) -> None:
        self.applied += 1

    def play_motion(self, group: str, index: int, *, reload_model: bool) -> None:
        self.motion_calls.append({"group": group, "index": index, "reload_model": reload_model})

    def play_expression(self, name: str, *, reload_model: bool) -> None:
        self.expression_calls.append({"name": name, "reload_model": reload_model})

    def raise_(self) -> None:
        self.raised += 1

    def activateWindow(self) -> None:
        self.activated += 1

    def show(self) -> None:
        self.visible = True


class DesktopCommandRouterSplitTests(unittest.TestCase):
    def test_router_module_exists_without_importing_main(self) -> None:
        tree = _parse_source("app/desktop_command_router.py")

        imported_modules: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.append(node.module)

        self.assertNotIn("main", imported_modules)

        module = _router_module()
        self.assertTrue(hasattr(module, "DesktopCommandRouter"))

    def test_main_imports_constructs_and_delegates_to_router(self) -> None:
        tree = _parse_source("main.py")

        imports_wiring = any(
            isinstance(node, ast.ImportFrom)
            and node.module == "app.desktop_command_wiring"
            and any(alias.name == "create_desktop_command_router" for alias in node.names)
            for node in ast.walk(tree)
        )
        self.assertTrue(imports_wiring)

        desktop_pet = next(
            node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == "DesktopPet"
        )
        constructs_router = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "create_desktop_command_router"
            for node in ast.walk(desktop_pet)
        )
        self.assertTrue(constructs_router)
        direct_router_calls = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "DesktopCommandRouter"
            for node in ast.walk(desktop_pet)
        )
        self.assertFalse(direct_router_calls)

        router = mock.Mock()
        command = {"nonce": "delegated", "type": "play_motion", "payload": {}}
        host = SimpleNamespace(desktop_command_router=router)

        self.assertIs(main.DesktopPet._desktop_command_mtime_token(host), router.desktop_command_mtime_token.return_value)
        main.DesktopPet._write_desktop_host_heartbeat(host)
        main.DesktopPet._response_path_for_command(host, command)
        main.DesktopPet._write_desktop_command_response(host, command, "success", {"ok": True})
        main.DesktopPet.on_desktop_command_poll(host)
        main.DesktopPet.process_desktop_command(host, command)

        router.write_desktop_host_heartbeat.assert_called_once_with()
        router.response_path_for_command.assert_called_once_with(command)
        router.write_desktop_command_response.assert_called_once_with(command, "success", {"ok": True})
        router.on_desktop_command_poll.assert_called_once_with()
        router.process_desktop_command.assert_called_once_with(command)

    def test_play_motion_updates_model_and_writes_success_response(self) -> None:
        module = _router_module()
        host = _FakeHost()
        responses: list[dict[str, object]] = []
        router = module.DesktopCommandRouter(
            host,
            root_dir=Path("/tmp/ipet-router-test"),
            command_path=Path("/tmp/ipet-router-test/command.json"),
            default_response_path=Path("/tmp/ipet-router-test/response.json"),
            heartbeat_path=Path("/tmp/ipet-router-test/heartbeat.json"),
            normalize_model_path=lambda value: f"normalized:{value}",
            write_response_func=lambda command, status, result=None: responses.append(
                {"command": command, "status": status, "result": result}
            ),
        )

        router.process_desktop_command(
            {
                "nonce": "motion-1",
                "type": "play_motion",
                "payload": {"model_path": "model/Haru.model3.json", "group": "Tap", "index": "2"},
            }
        )

        self.assertEqual(host.config["model_path"], "normalized:model/Haru.model3.json")
        self.assertEqual(host.refreshed, [False])
        self.assertEqual(host.motion_calls, [{"group": "Tap", "index": 2, "reload_model": True}])
        self.assertEqual(responses[0]["status"], "success")
        self.assertEqual(
            responses[0]["result"],
            {"group": "Tap", "index": 2, "model_path": "normalized:model/Haru.model3.json"},
        )

    def test_human_ops_click_focuses_target_without_hiding_or_restoring_ipet(self) -> None:
        module = _router_module()
        host = _FakeHost()
        calls: list[str] = []
        responses: list[dict[str, object]] = []

        def click(payload):
            calls.append("click")
            self.assertTrue(host.visible)
            return {"clicked": True, "x": payload["x"], "y": payload["y"], "label": payload["label"]}

        router = module.DesktopCommandRouter(
            host,
            root_dir=Path("/tmp/ipet-router-test"),
            command_path=Path("/tmp/ipet-router-test/command.json"),
            default_response_path=Path("/tmp/ipet-router-test/response.json"),
            heartbeat_path=Path("/tmp/ipet-router-test/heartbeat.json"),
            execute_human_ops_click=click,
            focus_target_application=lambda payload: calls.append(f"focus:{payload['target_app']}") or {
                "focused": True,
                "target_app": payload["target_app"],
            },
            write_response_func=lambda command, status, result=None: responses.append(
                {"command": command, "status": status, "result": result}
            ),
            heartbeat_func=lambda: calls.append("heartbeat"),
        )

        router.process_desktop_command(
            {
                "nonce": "click-1",
                "type": "human_ops_click",
                "payload": {"target_app": "WeChat", "x": 12, "y": 34, "label": "发送按钮"},
            }
        )

        self.assertEqual(calls, ["focus:WeChat", "click", "heartbeat"])
        self.assertTrue(host.visible)
        self.assertEqual(responses[0]["status"], "success")
        self.assertEqual(
            responses[0]["result"],
            {"focused": True, "target_app": "WeChat", "clicked": True, "x": 12, "y": 34, "label": "发送按钮"},
        )

    def test_active_vision_capture_writes_frame_and_trace(self) -> None:
        module = _router_module()
        host = _FakeHost()
        responses: list[dict[str, object]] = []
        frame = {
            "mime_type": "image/jpeg",
            "data_url": "data:image/jpeg;base64,abc",
            "active_observation": {"status": "ok", "actions": []},
        }

        router = module.DesktopCommandRouter(
            host,
            root_dir=Path("/tmp/ipet-router-test"),
            command_path=Path("/tmp/ipet-router-test/command.json"),
            default_response_path=Path("/tmp/ipet-router-test/response.json"),
            heartbeat_path=Path("/tmp/ipet-router-test/heartbeat.json"),
            capture_active_vision_frame_payload=lambda window, payload, vision: frame,
            write_response_func=lambda command, status, result=None: responses.append(
                {"command": command, "status": status, "result": result}
            ),
        )

        router.process_desktop_command({"nonce": "vision-1", "type": "active_vision_capture", "payload": {}})

        self.assertEqual(responses[0]["status"], "success")
        self.assertEqual(responses[0]["result"], {"frame": frame, "trace": frame["active_observation"]})

    def test_native_approval_only_focuses_ipet_after_rejection(self) -> None:
        module = _router_module()
        for approved, expected_focus_count in ((True, 0), (False, 1)):
            with self.subTest(approved=approved):
                host = _FakeHost()
                responses = []
                router = module.DesktopCommandRouter(
                    host,
                    root_dir=Path("/tmp/ipet-router-test"),
                    command_path=Path("/tmp/ipet-router-test/command.json"),
                    default_response_path=Path("/tmp/ipet-router-test/response.json"),
                    heartbeat_path=Path("/tmp/ipet-router-test/heartbeat.json"),
                    execute_human_ops_native_approval=lambda payload: {"approved": approved},
                    write_response_func=lambda command, status, result=None: responses.append((status, result)),
                )

                router.process_desktop_command(
                    {"nonce": "approval-1", "type": "human_ops_native_approval", "payload": {"message": "test"}}
                )

                self.assertEqual(responses[0][0], "success")
                self.assertEqual(host.raised, expected_focus_count)
                self.assertEqual(host.activated, expected_focus_count)

    def test_human_ops_launch_app_routes_without_hiding_pet_window(self) -> None:
        module = _router_module()
        host = _FakeHost()
        calls: list[str] = []
        responses: list[dict[str, object]] = []
        router = module.DesktopCommandRouter(
            host,
            root_dir=Path("/tmp/ipet-router-test"),
            command_path=Path("/tmp/ipet-router-test/command.json"),
            default_response_path=Path("/tmp/ipet-router-test/response.json"),
            heartbeat_path=Path("/tmp/ipet-router-test/heartbeat.json"),
            execute_human_ops_launch_app=lambda payload: calls.append(payload["app"]) or {"launched": True},
            focus_target_application=lambda payload: calls.append(f"focus:{payload['target_app']}") or {
                "focused": True,
                "target_app": payload["target_app"],
            },
            hide_window_for_desktop_click=lambda _host: calls.append("hide") or True,
            write_response_func=lambda command, status, result=None: responses.append(
                {"command": command, "status": status, "result": result}
            ),
        )

        router.process_desktop_command(
            {"nonce": "launch-1", "type": "human_ops_launch_app", "payload": {"app": "WeChat"}}
        )

        self.assertEqual(calls, ["WeChat", "focus:WeChat"])
        self.assertEqual(responses[0]["status"], "success")
        self.assertEqual(responses[0]["result"], {"launched": True, "focused": True, "target_app": "WeChat"})

    def test_poll_syncs_nonce_and_dispatches_new_command_once(self) -> None:
        module = _router_module()
        host = _FakeHost()
        dispatched: list[dict] = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            command_path = root / "command.json"
            command_path.write_text(
                json.dumps({"nonce": "poll-1", "type": "play_expression", "payload": {"name": "smile"}}),
                encoding="utf-8",
            )
            router = module.DesktopCommandRouter(
                host,
                root_dir=root,
                command_path=command_path,
                default_response_path=root / "response.json",
                heartbeat_path=root / "heartbeat.json",
                dispatch_func=lambda command: dispatched.append(command),
            )
            host._desktop_command_mtime = None
            host._last_desktop_command_nonce = ""

            router.on_desktop_command_poll()
            router.on_desktop_command_poll()

        self.assertEqual([item["nonce"] for item in dispatched], ["poll-1"])
        self.assertEqual(host._last_desktop_command_nonce, "poll-1")
        self.assertIsNotNone(host._desktop_command_mtime)

    def test_response_writer_preserves_shape_and_custom_path(self) -> None:
        module = _router_module()
        host = _FakeHost()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            response_path = root / "nested" / "custom.response.json"
            router = module.DesktopCommandRouter(
                host,
                root_dir=root,
                command_path=root / "command.json",
                default_response_path=root / "response.json",
                heartbeat_path=root / "heartbeat.json",
                time_module=SimpleNamespace(time_ns=lambda: 123456789),
                os_module=SimpleNamespace(getpid=lambda: 4242),
            )

            router.write_desktop_command_response(
                {
                    "nonce": "response-1",
                    "type": "play_motion",
                    "payload": {"response_path": str(response_path)},
                },
                "success",
                {"group": "Tap"},
            )

            response = json.loads(response_path.read_text(encoding="utf-8"))
            heartbeat = json.loads((root / "heartbeat.json").read_text(encoding="utf-8"))

        self.assertEqual(response["nonce"], "response-1")
        self.assertEqual(response["type"], "play_motion")
        self.assertEqual(response["status"], "success")
        self.assertTrue(response["ok"])
        self.assertEqual(response["result"], {"group": "Tap"})
        self.assertEqual(response["timestamp_ns"], 123456789)
        self.assertEqual(heartbeat, {"pid": 4242, "timestamp_ns": 123456789})


if __name__ == "__main__":
    unittest.main()
