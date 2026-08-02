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

    def test_active_vision_reads_target_ax_without_changing_focus(self) -> None:
        module = _router_module()
        host = _FakeHost()
        responses = []
        frame = {
            "capture_backend": "macos_accessibility",
            "accessibility": {"usable": True},
            "active_observation": {"status": "ok", "actions": []},
        }
        router = module.DesktopCommandRouter(
            host,
            root_dir=Path("/tmp/ipet-router-test"),
            command_path=Path("/tmp/ipet-router-test/command.json"),
            default_response_path=Path("/tmp/ipet-router-test/response.json"),
            heartbeat_path=Path("/tmp/ipet-router-test/heartbeat.json"),
            focus_target_application=lambda _payload: self.fail(
                "background AX reads must not focus the target app"
            ),
            capture_active_vision_frame_payload=lambda _window, _payload, _vision: frame,
            write_response_func=lambda command, status, result=None: responses.append(
                {"command": command, "status": status, "result": result}
            ),
        )

        router.process_desktop_command(
            {
                "nonce": "vision-ax",
                "type": "active_vision_capture",
                "payload": {
                    "target_app": "QQ",
                    "accessibility_enabled": True,
                },
            }
        )

        self.assertEqual(responses[0]["status"], "success")
        self.assertNotIn("focus_verification", responses[0]["result"]["frame"])

    def test_active_vision_refocuses_and_retries_menu_only_ax_tree(self) -> None:
        module = _router_module()
        host = _FakeHost()
        responses = []
        captures = []
        restorations = []

        def capture(_window, payload, _vision):
            captures.append(dict(payload))
            window_count = 0 if len(captures) == 1 else 1
            return {
                "capture_backend": "macos_accessibility",
                "accessibility": {
                    "usable": True,
                    "search": {
                        # A menu-only tree can accidentally match the query and
                        # claim sufficiency. Window presence, not that claim,
                        # decides whether interactive AX needs one focused retry.
                        "sufficient": True,
                        "insufficiency_reason": (
                            "" if window_count > 0 else "only_hidden_menu_matches"
                        ),
                        "index": {
                            "roles": (
                                {"AXWindow": window_count, "AXButton": 1}
                                if window_count
                                else {"AXApplication": 1, "AXMenuItem": 12}
                            )
                        },
                    },
                },
                "active_observation": {"status": "ok", "actions": []},
            }

        router = module.DesktopCommandRouter(
            host,
            root_dir=Path("/tmp/ipet-router-test"),
            command_path=Path("/tmp/ipet-router-test/command.json"),
            default_response_path=Path("/tmp/ipet-router-test/response.json"),
            heartbeat_path=Path("/tmp/ipet-router-test/heartbeat.json"),
            focus_target_application=lambda payload: {
                "focused": True,
                "surface_visible": True,
                "target_app": payload["target_app"],
                "frontmost_app": payload["target_app"],
                "previous_frontmost_app": "Ipet",
            },
            restore_target_application=lambda focus: restorations.append(
                dict(focus)
            )
            or {"restored": True},
            capture_active_vision_frame_payload=capture,
            write_response_func=lambda command, status, result=None: responses.append(
                {"command": command, "status": status, "result": result}
            ),
        )

        router.process_desktop_command(
            {
                "nonce": "vision-ax-focus-retry",
                "type": "active_vision_capture",
                "payload": {
                    "target_app": "Music",
                    "accessibility_enabled": True,
                    "accessibility_cache_mode": "prefer_cache",
                },
            }
        )

        self.assertEqual(len(captures), 2)
        self.assertEqual(captures[1]["accessibility_cache_mode"], "refresh")
        self.assertEqual(captures[1]["settle_ms"], 0)
        self.assertEqual(
            restorations[0]["previous_frontmost_app"],
            "Ipet",
        )
        result_frame = responses[0]["result"]["frame"]
        self.assertTrue(result_frame["accessibility"]["search"]["sufficient"])
        self.assertTrue(result_frame["focus_verification"]["focused"])

    def test_active_vision_does_not_attach_wrong_screen_when_target_window_stays_absent(self) -> None:
        module = _router_module()
        host = _FakeHost()
        responses = []
        captures = []

        def capture(_window, payload, _vision):
            captures.append(dict(payload))
            if payload.get("accessibility_enabled") is False:
                self.fail("an unverified target surface must not capture another app")
            return {
                "capture_backend": "macos_accessibility",
                "target_app": None,
                "accessibility": {
                    "usable": True,
                    "app": {"app_id": "qq", "name": "QQ"},
                    "search": {
                        "sufficient": False,
                        "insufficiency_reason": "requested_role_not_found",
                        "window_candidates": [
                            {
                                "bounds": {
                                    "x": 15,
                                    "y": 125,
                                    "width": 880,
                                    "height": 640,
                                },
                                "focused": False,
                                "enabled": True,
                            }
                        ],
                        "index": {
                            "roles": {
                                "AXApplication": 1,
                                "AXMenuItem": 12,
                            }
                        },
                    },
                },
                "active_observation": {
                    "status": "partial",
                    "target_app": None,
                    "actions": [],
                },
            }

        router = module.DesktopCommandRouter(
            host,
            root_dir=Path("/tmp/ipet-router-test"),
            command_path=Path("/tmp/ipet-router-test/command.json"),
            default_response_path=Path("/tmp/ipet-router-test/response.json"),
            heartbeat_path=Path("/tmp/ipet-router-test/heartbeat.json"),
            focus_target_application=lambda payload: {
                "focused": True,
                "surface_visible": True,
                "target_app": payload["target_app"],
                "frontmost_app": payload["target_app"],
            },
            capture_active_vision_frame_payload=capture,
            write_response_func=lambda command, status, result=None: responses.append(
                {"command": command, "status": status, "result": result}
            ),
        )

        router.process_desktop_command(
            {
                "nonce": "vision-window-absent",
                "type": "active_vision_capture",
                "payload": {
                    "target_app": "QQ",
                    "accessibility_enabled": True,
                },
            }
        )

        self.assertEqual(len(captures), 2)
        frame = responses[0]["result"]["frame"]
        self.assertFalse(frame["focus_verification"]["focused"])
        self.assertFalse(frame["focus_verification"]["surface_visible"])
        self.assertIn(
            "application_focus_unverified",
            frame["active_observation"]["unknowns"][0],
        )

    def test_active_vision_refocuses_when_auxiliary_window_lacks_query_evidence(self) -> None:
        module = _router_module()
        host = _FakeHost()
        responses = []
        captures = []

        def capture(_window, payload, _vision):
            captures.append(dict(payload))
            sufficient = len(captures) > 1
            return {
                "capture_backend": "macos_accessibility",
                "accessibility": {
                    "usable": True,
                    "search": {
                        "sufficient": sufficient,
                        "insufficiency_reason": (
                            "" if sufficient else "no_actionable_match"
                        ),
                        "index": {
                            "roles": {
                                "AXWindow": 1,
                                "AXGroup": 3,
                                "AXButton": 3,
                                "AXMenuItem": 16,
                            }
                        },
                    },
                },
                "active_observation": {
                    "status": "ok" if sufficient else "partial",
                    "actions": [],
                },
            }

        router = module.DesktopCommandRouter(
            host,
            root_dir=Path("/tmp/ipet-router-test"),
            command_path=Path("/tmp/ipet-router-test/command.json"),
            default_response_path=Path("/tmp/ipet-router-test/response.json"),
            heartbeat_path=Path("/tmp/ipet-router-test/heartbeat.json"),
            focus_target_application=lambda payload: {
                "focused": True,
                "target_app": payload["target_app"],
                "frontmost_app": payload["target_app"],
            },
            capture_active_vision_frame_payload=capture,
            write_response_func=lambda command, status, result=None: responses.append(
                {"command": command, "status": status, "result": result}
            ),
        )

        router.process_desktop_command(
            {
                "nonce": "vision-ax-aux-window-retry",
                "type": "active_vision_capture",
                "payload": {
                    "target_app": "QQ",
                    "accessibility_enabled": True,
                    "accessibility_query": "莉森溪 会话 输入框",
                },
            }
        )

        self.assertEqual(len(captures), 2)
        self.assertEqual(captures[1]["accessibility_cache_mode"], "refresh")
        self.assertEqual(captures[1]["settle_ms"], 0)
        self.assertTrue(
            responses[0]["result"]["frame"]["focus_verification"]["focused"]
        )

    def test_active_vision_refocuses_before_visual_fallback(self) -> None:
        module = _router_module()
        host = _FakeHost()
        responses = []
        captures = []

        def capture(_window, payload, _vision):
            captures.append(dict(payload))
            return {
                "capture_backend": "macos_screencapture",
                "active_observation": {"status": "ok", "actions": []},
            }

        router = module.DesktopCommandRouter(
            host,
            root_dir=Path("/tmp/ipet-router-test"),
            command_path=Path("/tmp/ipet-router-test/command.json"),
            default_response_path=Path("/tmp/ipet-router-test/response.json"),
            heartbeat_path=Path("/tmp/ipet-router-test/heartbeat.json"),
            focus_target_application=lambda payload: {
                "focused": True,
                "surface_visible": True,
                "target_app": payload["target_app"],
                "frontmost_app": payload["target_app"],
            },
            capture_active_vision_frame_payload=capture,
            write_response_func=lambda command, status, result=None: responses.append(
                {"command": command, "status": status, "result": result}
            ),
        )

        router.process_desktop_command(
            {
                "nonce": "vision-fallback",
                "type": "active_vision_capture",
                "payload": {
                    "target_app": "WeChat",
                    "accessibility_enabled": True,
                },
            }
        )

        self.assertEqual(len(captures), 2)
        self.assertTrue(captures[0]["accessibility_enabled"])
        self.assertFalse(captures[1]["accessibility_enabled"])
        self.assertTrue(captures[1]["target_surface_verified"])
        self.assertTrue(
            responses[0]["result"]["frame"]["focus_verification"]["focused"]
        )

    def test_active_vision_returns_visual_fallback_in_same_command_after_ax_retry(self) -> None:
        module = _router_module()
        host = _FakeHost()
        responses = []
        captures = []

        def capture(_window, payload, _vision):
            captures.append(dict(payload))
            if payload.get("accessibility_enabled") is False:
                return {
                    "capture_backend": "macos_screencapture",
                    "active_observation": {
                        "status": "ok",
                        "actions": [],
                    },
                }
            return {
                "capture_backend": "macos_accessibility",
                "accessibility": {
                    "usable": True,
                    "snapshot_id": "wechat-window-chrome",
                    "search": {
                        "sufficient": False,
                        "insufficiency_reason": "requested_role_not_found",
                        "window_candidates": [
                            {
                                "bounds": {
                                    "x": 15,
                                    "y": 125,
                                    "width": 880,
                                    "height": 640,
                                },
                                "focused": False,
                                "enabled": True,
                            }
                        ],
                        "index": {
                            "roles": {
                                "AXWindow": 1,
                                "AXButton": 3,
                                "AXMenuItem": 12,
                            }
                        },
                    },
                },
                "active_observation": {
                    "status": "partial",
                    "actions": [],
                },
            }

        router = module.DesktopCommandRouter(
            host,
            root_dir=Path("/tmp/ipet-router-test"),
            command_path=Path("/tmp/ipet-router-test/command.json"),
            default_response_path=Path("/tmp/ipet-router-test/response.json"),
            heartbeat_path=Path("/tmp/ipet-router-test/heartbeat.json"),
            focus_target_application=lambda payload: {
                "focused": True,
                "surface_visible": True,
                "target_app": payload["target_app"],
                "frontmost_app": payload["target_app"],
            },
            capture_active_vision_frame_payload=capture,
            write_response_func=lambda command, status, result=None: responses.append(
                {"command": command, "status": status, "result": result}
            ),
        )

        router.process_desktop_command(
            {
                "nonce": "vision-ax-to-visual",
                "type": "active_vision_capture",
                "payload": {
                    "target_app": "WeChat",
                    "accessibility_enabled": True,
                },
            }
        )

        self.assertEqual(len(captures), 3)
        self.assertTrue(captures[0]["accessibility_enabled"])
        self.assertEqual(captures[1]["accessibility_cache_mode"], "refresh")
        self.assertFalse(captures[2]["accessibility_enabled"])
        self.assertTrue(captures[2]["target_surface_verified"])
        self.assertEqual(
            captures[2]["target_bounds"],
            {"x": 15, "y": 125, "width": 880, "height": 640},
        )
        frame = responses[0]["result"]["frame"]
        self.assertEqual(frame["capture_backend"], "macos_screencapture")
        self.assertEqual(
            frame["accessibility"]["snapshot_id"],
            "wechat-window-chrome",
        )
        self.assertEqual(
            frame["accessibility_fallback"]["reason"],
            "requested_role_not_found",
        )
        self.assertTrue(frame["focus_verification"]["focused"])

    def test_active_vision_does_not_capture_pixels_when_screen_capture_is_disabled(self) -> None:
        module = _router_module()
        host = _FakeHost()
        responses = []
        captures = []

        def capture(_window, payload, _vision):
            captures.append(dict(payload))
            if payload.get("accessibility_enabled") is False:
                self.fail("disabled screen capture must not fall back to pixels")
            return {
                "capture_backend": "macos_accessibility",
                "accessibility": {
                    "usable": True,
                    "search": {
                        "sufficient": False,
                        "insufficiency_reason": "requested_role_not_found",
                        "visual_fallback_required": True,
                        "index": {"roles": {"AXWindow": 1}},
                    },
                },
                "active_observation": {"status": "partial", "actions": []},
            }

        router = module.DesktopCommandRouter(
            host,
            root_dir=Path("/tmp/ipet-router-test"),
            command_path=Path("/tmp/ipet-router-test/command.json"),
            default_response_path=Path("/tmp/ipet-router-test/response.json"),
            heartbeat_path=Path("/tmp/ipet-router-test/heartbeat.json"),
            focus_target_application=lambda payload: {
                "focused": True,
                "surface_visible": True,
                "target_app": payload["target_app"],
                "frontmost_app": payload["target_app"],
            },
            capture_active_vision_frame_payload=capture,
            write_response_func=lambda command, status, result=None: responses.append(
                {"command": command, "status": status, "result": result}
            ),
        )

        router.process_desktop_command(
            {
                "nonce": "vision-ax-only",
                "type": "active_vision_capture",
                "payload": {
                    "target_app": "WeChat",
                    "accessibility_enabled": True,
                    "screen_capture_enabled": False,
                },
            }
        )

        self.assertEqual(len(captures), 2)
        self.assertTrue(all(item["accessibility_enabled"] for item in captures))
        self.assertEqual(
            responses[0]["result"]["frame"]["capture_backend"],
            "macos_accessibility",
        )

    def test_active_vision_keeps_near_match_structured_without_visual_fallback(self) -> None:
        module = _router_module()
        host = _FakeHost()
        responses = []
        captures = []

        def capture(_window, payload, _vision):
            captures.append(dict(payload))
            if payload.get("accessibility_enabled") is False:
                self.fail("a structurally proven near match must not capture pixels")
            return {
                "capture_backend": "macos_accessibility",
                "accessibility": {
                    "usable": True,
                    "app": {"app_id": "qq", "name": "QQ"},
                    "search": {
                        "sufficient": False,
                        "insufficiency_reason": "near_match_requires_confirmation",
                        "near_match_labels": ["测试联系人"],
                        "visual_fallback_required": False,
                        "index": {
                            "roles": {
                                "AXWindow": 1,
                                "AXGroup": 2,
                            }
                        },
                    },
                },
                "active_observation": {
                    "status": "partial",
                    "actions": [],
                },
            }

        router = module.DesktopCommandRouter(
            host,
            root_dir=Path("/tmp/ipet-router-test"),
            command_path=Path("/tmp/ipet-router-test/command.json"),
            default_response_path=Path("/tmp/ipet-router-test/response.json"),
            heartbeat_path=Path("/tmp/ipet-router-test/heartbeat.json"),
            focus_target_application=lambda payload: {
                "focused": True,
                "surface_visible": True,
                "target_app": payload["target_app"],
                "frontmost_app": payload["target_app"],
            },
            capture_active_vision_frame_payload=capture,
            write_response_func=lambda command, status, result=None: responses.append(
                {"command": command, "status": status, "result": result}
            ),
        )

        router.process_desktop_command(
            {
                "nonce": "vision-near-match",
                "type": "active_vision_capture",
                "payload": {
                    "target_app": "QQ",
                    "accessibility_enabled": True,
                },
            }
        )

        self.assertEqual(len(captures), 2)
        self.assertTrue(captures[0]["accessibility_enabled"])
        self.assertEqual(captures[1]["accessibility_cache_mode"], "refresh")
        frame = responses[0]["result"]["frame"]
        self.assertEqual(frame["capture_backend"], "macos_accessibility")
        self.assertNotIn("accessibility_fallback", frame)
        self.assertEqual(frame["target_app"], "QQ")
        self.assertEqual(frame["active_observation"]["target_app"], "QQ")
        self.assertTrue(frame["focus_verification"]["focused"])

    def test_active_vision_focus_failure_returns_explicit_partial_frame(self) -> None:
        module = _router_module()
        host = _FakeHost()
        responses = []
        captures = []
        router = module.DesktopCommandRouter(
            host,
            root_dir=Path("/tmp/ipet-router-test"),
            command_path=Path("/tmp/ipet-router-test/command.json"),
            default_response_path=Path("/tmp/ipet-router-test/response.json"),
            heartbeat_path=Path("/tmp/ipet-router-test/heartbeat.json"),
            focus_target_application=lambda _payload: (_ for _ in ()).throw(
                RuntimeError("expected WeChat, got ChatGPT")
            ),
            capture_active_vision_frame_payload=lambda _window, _payload, _vision: captures.append(
                dict(_payload)
            ),
            write_response_func=lambda command, status, result=None: responses.append(
                {"command": command, "status": status, "result": result}
            ),
        )

        router.process_desktop_command(
            {
                "nonce": "vision-focus-failed",
                "type": "active_vision_capture",
                "payload": {
                    "target_app": "WeChat",
                    "accessibility_enabled": False,
                },
            }
        )

        result = responses[0]["result"]
        self.assertEqual(responses[0]["status"], "success")
        self.assertEqual(captures, [])
        self.assertEqual(result["frame"]["capture_backend"], "unavailable")
        self.assertFalse(result["frame"]["focus_verification"]["focused"])
        self.assertEqual(result["trace"]["status"], "partial")
        self.assertIn(
            "application_focus_unverified",
            result["trace"]["unknowns"][0],
        )

    def test_accessibility_index_status_and_refresh_do_not_focus_an_app(self) -> None:
        module = _router_module()
        host = _FakeHost()
        responses: list[tuple[str, str, dict[str, object] | None]] = []
        calls: list[str] = []
        router = module.DesktopCommandRouter(
            host,
            root_dir=Path("/tmp/ipet-router-test"),
            command_path=Path("/tmp/ipet-router-test/command.json"),
            default_response_path=Path("/tmp/ipet-router-test/response.json"),
            heartbeat_path=Path("/tmp/ipet-router-test/heartbeat.json"),
            accessibility_index_status=lambda: calls.append("status") or {"app_count": 2},
            refresh_accessibility_index=lambda: calls.append("refresh") or {"updated_count": 2},
            background_runner=lambda task: task(),
            focus_target_application=lambda _payload: self.fail("AX index maintenance must not focus an app"),
            write_response_func=lambda command, status, result=None: responses.append(
                (command["type"], status, result)
            ),
        )

        router.process_desktop_command(
            {"nonce": "ax-status", "type": "accessibility_index_status", "payload": {}}
        )
        router.process_desktop_command(
            {"nonce": "ax-refresh", "type": "accessibility_index_refresh", "payload": {}}
        )

        self.assertEqual(calls, ["status", "refresh"])
        self.assertEqual(
            responses,
            [
                ("accessibility_index_status", "success", {"app_count": 2}),
                ("accessibility_index_refresh", "success", {"updated_count": 2}),
            ],
        )

    def test_native_approval_never_changes_ipet_window_focus(self) -> None:
        module = _router_module()
        for approved in (True, False):
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
                self.assertEqual(host.raised, 0)
                self.assertEqual(host.activated, 0)

    def test_action_notice_does_not_raise_ipet_window(self) -> None:
        module = _router_module()
        host = _FakeHost()
        responses = []
        router = module.DesktopCommandRouter(
            host,
            root_dir=Path("/tmp/ipet-router-test"),
            command_path=Path("/tmp/ipet-router-test/command.json"),
            default_response_path=Path("/tmp/ipet-router-test/response.json"),
            heartbeat_path=Path("/tmp/ipet-router-test/heartbeat.json"),
            execute_human_ops_native_approval=lambda payload: {"notified": payload["notice_only"]},
            write_response_func=lambda command, status, result=None: responses.append((status, result)),
        )

        router.process_desktop_command(
            {
                "nonce": "notice-1",
                "type": "human_ops_native_approval",
                "payload": {"notice_only": True, "message": "即将执行"},
            }
        )

        self.assertEqual(responses[0], ("success", {"notified": True}))
        self.assertEqual(host.raised, 0)
        self.assertEqual(host.activated, 0)

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

    def test_poll_drains_atomic_command_queue_without_overwrite(self) -> None:
        module = _router_module()
        host = _FakeHost()
        dispatched: list[dict] = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            command_path = root / "command.json"
            queue_dir = root / "command.queue"
            queue_dir.mkdir()
            for index in (1, 2):
                (queue_dir / f"{index:03d}.json").write_text(
                    json.dumps(
                        {
                            "protocol": "ipet.desktop-command.v1",
                            "nonce": f"queued-{index}",
                            "type": "play_expression",
                            "deadline_ns": 9_999_999_999_999_999_999,
                            "payload": {"name": f"face-{index}"},
                        }
                    ),
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

            self.assertEqual(list(queue_dir.glob("*.json")), [])

        self.assertEqual(
            [item["nonce"] for item in dispatched],
            ["queued-1", "queued-2"],
        )

    def test_poll_does_not_replay_legacy_command_after_queue_drains(self) -> None:
        module = _router_module()
        host = _FakeHost()
        dispatched: list[dict] = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            command_path = root / "command.json"
            command_path.write_text(
                json.dumps(
                    {
                        "nonce": "legacy-music",
                        "type": "active_vision_capture",
                        "payload": {"target_app": "音乐"},
                    }
                ),
                encoding="utf-8",
            )
            queue_dir = root / "command.queue"
            queue_dir.mkdir()
            (queue_dir / "001.json").write_text(
                json.dumps(
                    {
                        "protocol": "ipet.desktop-command.v1",
                        "nonce": "queued-status",
                        "type": "accessibility_index_status",
                        "deadline_ns": 9_999_999_999_999_999_999,
                        "payload": {},
                    }
                ),
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

            self.assertEqual(list(queue_dir.glob("*.json")), [])

        self.assertEqual(
            [item["nonce"] for item in dispatched],
            ["queued-status"],
        )

    def test_poll_rejects_expired_queue_command_before_dispatch(self) -> None:
        module = _router_module()
        host = _FakeHost()
        dispatched: list[dict] = []
        responses: list[tuple[str, dict[str, object] | None]] = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            command_path = root / "command.json"
            queue_dir = root / "command.queue"
            queue_dir.mkdir()
            queued_path = queue_dir / "001.json"
            queued_path.write_text(
                json.dumps(
                    {
                        "protocol": "ipet.desktop-command.v1",
                        "nonce": "expired-1",
                        "type": "human_ops_click",
                        "deadline_ns": 100,
                        "payload": {"target_app": "QQ"},
                    }
                ),
                encoding="utf-8",
            )
            router = module.DesktopCommandRouter(
                host,
                root_dir=root,
                command_path=command_path,
                default_response_path=root / "response.json",
                heartbeat_path=root / "heartbeat.json",
                dispatch_func=lambda command: dispatched.append(command),
                write_response_func=lambda _command, status, result=None: responses.append(
                    (status, result)
                ),
                time_module=SimpleNamespace(time_ns=lambda: 200),
            )
            host._desktop_command_mtime = None
            host._last_desktop_command_nonce = ""

            router.on_desktop_command_poll()

            self.assertFalse(queued_path.exists())

        self.assertEqual(dispatched, [])
        self.assertEqual(
            responses,
            [("error", {"error": "desktop command expired before dispatch"})],
        )

    def test_action_rechecks_deadline_after_focus_before_click(self) -> None:
        module = _router_module()
        host = _FakeHost()
        now = {"value": 100}
        clicks: list[dict] = []
        restorations: list[dict] = []
        responses: list[tuple[str, dict[str, object] | None]] = []

        def focus(payload):
            now["value"] = 250
            return {
                "focused": True,
                "target_app": payload["target_app"],
                "previous_frontmost_app": "Ipet",
            }

        router = module.DesktopCommandRouter(
            host,
            root_dir=Path("/tmp/ipet-router-test"),
            command_path=Path("/tmp/ipet-router-test/command.json"),
            default_response_path=Path("/tmp/ipet-router-test/response.json"),
            heartbeat_path=Path("/tmp/ipet-router-test/heartbeat.json"),
            focus_target_application=focus,
            execute_human_ops_click=lambda payload: clicks.append(payload) or {},
            restore_target_application=lambda focus_result: restorations.append(
                focus_result
            )
            or {"restored": True},
            write_response_func=lambda _command, status, result=None: responses.append(
                (status, result)
            ),
            time_module=SimpleNamespace(time_ns=lambda: now["value"]),
        )

        router.process_desktop_command(
            {
                "nonce": "expires-during-focus",
                "type": "human_ops_click",
                "deadline_ns": 200,
                "payload": {"target_app": "QQ", "x": 10, "y": 20},
            }
        )

        self.assertEqual(clicks, [])
        self.assertEqual(restorations[0]["previous_frontmost_app"], "Ipet")
        self.assertEqual(responses[0][0], "error")
        self.assertIn("expired before click", responses[0][1]["error"])

    def test_poll_runs_blocking_target_command_off_poller_and_serializes_work(self) -> None:
        module = _router_module()
        host = _FakeHost()
        runners: list[object] = []
        calls: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            command_path = root / "command.json"
            queue_dir = root / "command.queue"
            queue_dir.mkdir()
            for index in (1, 2):
                (queue_dir / f"{index:03d}.json").write_text(
                    json.dumps(
                        {
                            "protocol": "ipet.desktop-command.v1",
                            "nonce": f"async-{index}",
                            "type": "human_ops_click",
                            "deadline_ns": 9_999_999_999_999_999_999,
                            "payload": {
                                "target_app": "QQ",
                                "x": index,
                                "y": index,
                            },
                        }
                    ),
                    encoding="utf-8",
                )
            router = module.DesktopCommandRouter(
                host,
                root_dir=root,
                command_path=command_path,
                default_response_path=root / "response.json",
                heartbeat_path=root / "heartbeat.json",
                background_runner=lambda task: runners.append(task),
                focus_target_application=lambda payload: {
                    "focused": True,
                    "target_app": payload["target_app"],
                },
                execute_human_ops_click=lambda payload: calls.append(
                    f"click:{payload['x']}"
                )
                or {},
                write_response_func=lambda *_args: None,
            )
            host._desktop_command_mtime = None

            router.on_desktop_command_poll()

            self.assertEqual(calls, [])
            self.assertEqual(len(runners), 1)
            self.assertEqual(list(queue_dir.glob("*.json")), [])
            runners[0]()

        self.assertEqual(calls, ["click:1", "click:2"])

    def test_poll_keeps_qt_vision_capture_on_the_poller_thread(self) -> None:
        module = _router_module()
        host = _FakeHost()
        runners: list[object] = []
        captures: list[dict[str, object]] = []
        responses: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            command_path = root / "command.json"
            queue_dir = root / "command.queue"
            queue_dir.mkdir()
            (queue_dir / "001.json").write_text(
                json.dumps(
                    {
                        "protocol": "ipet.desktop-command.v1",
                        "nonce": "vision-ui-thread",
                        "type": "active_vision_capture",
                        "deadline_ns": 9_999_999_999_999_999_999,
                        "payload": {
                            "target_app": "QQ",
                            "accessibility_enabled": True,
                        },
                    }
                ),
                encoding="utf-8",
            )
            router = module.DesktopCommandRouter(
                host,
                root_dir=root,
                command_path=command_path,
                default_response_path=root / "response.json",
                heartbeat_path=root / "heartbeat.json",
                background_runner=lambda task: runners.append(task),
                capture_active_vision_frame_payload=lambda _host, payload, _config: captures.append(
                    dict(payload)
                )
                or {
                    "capture_backend": "macos_accessibility",
                    "accessibility": {"usable": True},
                    "active_observation": {"status": "success"},
                },
                write_response_func=lambda _command, status, _result=None: responses.append(
                    status
                ),
            )
            host._desktop_command_mtime = None

            router.on_desktop_command_poll()

        self.assertEqual(runners, [])
        self.assertEqual(len(captures), 1)
        self.assertEqual(responses, ["success"])

    def test_poll_rejects_queue_command_without_protocol_or_deadline(self) -> None:
        module = _router_module()
        host = _FakeHost()
        responses: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            command_path = root / "command.json"
            queue_dir = root / "command.queue"
            queue_dir.mkdir()
            (queue_dir / "001.json").write_text(
                json.dumps(
                    {
                        "nonce": "unversioned",
                        "type": "human_ops_click",
                        "payload": {"target_app": "Music"},
                    }
                ),
                encoding="utf-8",
            )
            router = module.DesktopCommandRouter(
                host,
                root_dir=root,
                command_path=command_path,
                default_response_path=root / "response.json",
                heartbeat_path=root / "heartbeat.json",
                write_response_func=lambda _command, _status, result=None: responses.append(
                    str((result or {}).get("error") or "")
                ),
            )
            host._desktop_command_mtime = None

            router.on_desktop_command_poll()

        self.assertEqual(
            responses,
            ["unsupported or missing desktop command protocol"],
        )

    def test_seen_nonce_window_rejects_nonconsecutive_replay(self) -> None:
        module = _router_module()
        host = _FakeHost()
        dispatched: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            command_path = root / "command.json"
            router = module.DesktopCommandRouter(
                host,
                root_dir=root,
                command_path=command_path,
                default_response_path=root / "response.json",
                heartbeat_path=root / "heartbeat.json",
                dispatch_func=lambda command: dispatched.append(command["nonce"]),
            )
            for nonce in ("A", "B", "A"):
                command_path.write_text(
                    json.dumps(
                        {
                            "nonce": nonce,
                            "type": "play_expression",
                            "payload": {"name": "smile"},
                        }
                    ),
                    encoding="utf-8",
                )
                router._process_desktop_command_path(
                    command_path,
                    remove_after=False,
                )

        self.assertEqual(dispatched, ["A", "B"])

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
