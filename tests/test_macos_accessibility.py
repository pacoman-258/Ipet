from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from body.active_vision import capture_active_vision_frame_payload
from body.macos_accessibility import (
    _AXRuntime,
    capture_macos_accessibility,
    perform_macos_accessibility_action,
    refresh_macos_accessibility_index,
    resolve_macos_ax_app,
    search_macos_accessibility_cache,
)
from body.macos_accessibility_cache import AXSnapshotCache


class _FakeAXRuntime:
    def __init__(self) -> None:
        self.nodes = {
            1: {
                "AXRole": "AXApplication",
                "AXTitle": "WeChat",
                "children": [2],
            },
            2: {
                "AXRole": "AXWindow",
                "AXTitle": "聊天",
                "AXPosition": {"x": 100, "y": 80},
                "AXSize": {"width": 900, "height": 700},
                "children": [3, 4, 5],
            },
            3: {
                "AXRole": "AXButton",
                "AXTitle": "发送",
                "AXIdentifier": "send-button",
                "AXEnabled": True,
                "AXPosition": {"x": 900, "y": 700},
                "AXSize": {"width": 70, "height": 34},
                "actions": ["AXPress"],
                "children": [],
            },
            4: {
                "AXRole": "AXTextArea",
                "AXDescription": "消息输入框",
                "AXIdentifier": "message-input",
                "AXEnabled": True,
                "AXFocused": False,
                "AXPosition": {"x": 360, "y": 590},
                "AXSize": {"width": 590, "height": 120},
                "actions": [],
                "children": [],
            },
            5: {
                "AXRole": "AXTextField",
                "AXSubrole": "AXSecureTextField",
                "AXDescription": "密码",
                "AXIdentifier": "password",
                "AXValue": "do-not-expose",
                "AXEnabled": True,
                "children": [],
            },
        }
        self.performed: list[tuple[int, str]] = []
        self.closed = False

    def trusted(self) -> bool:
        return True

    def focused_application(self) -> tuple[int, str]:
        return 777, "WeChat"

    def application(self, pid: int) -> int:
        return 1 if pid == 777 else 0

    def set_timeout(self, _element: int, _seconds: float) -> None:
        return None

    def retain(self, value: int) -> int:
        return value

    def release(self, _value: int) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def attribute(self, element: int, name: str):
        return self.nodes[element].get(name)

    def children(self, element: int, *, limit: int) -> tuple[list[int], bool]:
        children = list(self.nodes[element].get("children") or [])
        return children[:limit], len(children) > limit

    def actions(self, element: int) -> list[str]:
        return list(self.nodes[element].get("actions") or [])

    def perform(self, element: int, action: str) -> None:
        self.performed.append((element, action))

    def set_boolean_attribute(self, element: int, name: str, value: bool) -> None:
        self.nodes[element][name] = bool(value)


class _Window:
    def isVisible(self) -> bool:
        return True


class _LargeQQRuntime(_FakeAXRuntime):
    def __init__(self, *, fail_on_application: bool = False) -> None:
        super().__init__()
        self.fail_on_application = fail_on_application
        self.nodes = {
            1: {"AXRole": "AXApplication", "AXTitle": "QQ", "children": [2]},
            2: {
                "AXRole": "AXWindow",
                "AXTitle": "QQ",
                "AXPosition": {"x": 0, "y": 0},
                "AXSize": {"width": 1200, "height": 900},
                "children": list(range(3, 423)),
            },
        }
        for element_id in range(3, 423):
            title = "张三" if element_id == 333 else f"联系人{element_id:03d}"
            self.nodes[element_id] = {
                "AXRole": "AXRow",
                "AXTitle": title,
                "AXEnabled": True,
                "AXPosition": {"x": 20, "y": element_id},
                "AXSize": {"width": 280, "height": 30},
                "children": [],
            }
        self.nodes[421] = {
            "AXRole": "AXButton",
            "AXTitle": "发送",
            "AXIdentifier": "send-button",
            "AXEnabled": True,
            "AXPosition": {"x": 1080, "y": 820},
            "AXSize": {"width": 80, "height": 34},
            "actions": ["AXPress"],
            "children": [],
        }

    def focused_application(self) -> tuple[int, str]:
        return 888, "QQ"

    def application(self, pid: int) -> int:
        if self.fail_on_application:
            raise AssertionError("cache search must not rebuild the AX tree")
        return 1 if pid == 888 else 0


class _CompositeMusicRuntime(_FakeAXRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.nodes = {
            1: {"AXRole": "AXApplication", "AXTitle": "Music", "children": [2, 8]},
            2: {
                "AXRole": "AXWindow",
                "AXTitle": "音乐",
                "AXPosition": {"x": 80, "y": 40},
                "AXSize": {"width": 1100, "height": 760},
                "children": [3],
            },
            3: {"AXRole": "AXOutline", "children": [4]},
            4: {
                "AXRole": "AXRow",
                "AXSubrole": "AXOutlineRow",
                "AXEnabled": True,
                "AXPosition": {"x": 90, "y": 260},
                "AXSize": {"width": 210, "height": 28},
                "children": [5],
            },
            5: {
                "AXRole": "AXCell",
                "AXEnabled": True,
                "AXFocused": True,
                "AXSelected": True,
                "AXPosition": {"x": 90, "y": 260},
                "AXSize": {"width": 210, "height": 28},
                "children": [6],
            },
            6: {
                "AXRole": "AXStaticText",
                "AXValue": "专辑",
                "AXPosition": {"x": 126, "y": 264},
                "AXSize": {"width": 42, "height": 20},
                "children": [],
            },
            8: {"AXRole": "AXMenu", "AXTitle": "控制", "children": [9]},
            9: {"AXRole": "AXMenuItem", "AXTitle": "随机播放", "children": [10]},
            10: {"AXRole": "AXMenu", "children": [11]},
            11: {
                "AXRole": "AXMenuItem",
                "AXTitle": "专辑",
                "AXEnabled": True,
                "actions": ["AXPress"],
                "children": [],
            },
        }

    def focused_application(self) -> tuple[int, str]:
        return 777, "Music"


class MacOSAccessibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cache = AXSnapshotCache()

    def test_known_aliases_remain_stable_and_other_apps_get_dynamic_profiles(self) -> None:
        self.assertEqual(resolve_macos_ax_app("QQ")["app_id"], "qq")
        self.assertEqual(resolve_macos_ax_app("微信")["app_id"], "wechat")
        self.assertEqual(resolve_macos_ax_app("Music")["app_id"], "music")
        self.assertEqual(resolve_macos_ax_app("访达")["app_id"], "finder")
        notes = resolve_macos_ax_app("Notes")
        self.assertTrue(notes["dynamic"])
        self.assertEqual(notes["display_name"], "Notes")
        self.assertEqual(notes["surface"], "native_app_gui")
        safari = resolve_macos_ax_app(
            {"app": "Safari", "bundle_id": "com.apple.Safari", "pid": "902"}
        )
        self.assertTrue(safari["dynamic"])
        self.assertEqual(safari["bundle_id"], "com.apple.Safari")
        self.assertEqual(safari["pid"], 902)

    def test_capture_blocks_self_pid_before_constructing_ax_runtime(self) -> None:
        runtime_factory_calls: list[str] = []

        result = capture_macos_accessibility(
            {
                "app": "Ipet",
                "bundle_id": "com.example.Ipet",
                "pid": "777",
            },
            platform_name="darwin",
            runtime_factory=lambda: runtime_factory_calls.append("runtime") or _FakeAXRuntime(),
            cache=self.cache,
            current_pid_provider=lambda: 777,
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["pid"], 777)
        self.assertIn("own Accessibility process", result["reason"])
        self.assertEqual(runtime_factory_calls, [])

    def test_capture_blocks_self_pid_after_dynamic_app_resolution(self) -> None:
        runtime_factory_calls: list[str] = []

        result = capture_macos_accessibility(
            "Ipet",
            platform_name="darwin",
            runner=lambda *_args, **_kwargs: None,
            runtime_factory=lambda: runtime_factory_calls.append("runtime") or _FakeAXRuntime(),
            cache=self.cache,
            running_apps_provider=lambda **_kwargs: (
                [{"app": "Ipet", "bundle_id": "com.example.Ipet", "pid": "777"}],
                [],
            ),
            current_pid_provider=lambda: 777,
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["pid"], 777)
        self.assertEqual(runtime_factory_calls, [])

    def test_cache_search_blocks_current_process_snapshot(self) -> None:
        result = search_macos_accessibility_cache(
            {"app": "Ipet", "bundle_id": "com.example.Ipet", "pid": "777"},
            "button",
            pid=777,
            cache=self.cache,
            current_pid_provider=lambda: 777,
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["pid"], 777)
        self.assertIn("own Accessibility process", result["reason"])

    def test_runtime_application_has_a_final_self_pid_guard(self) -> None:
        runtime = object.__new__(_AXRuntime)
        runtime.ax = SimpleNamespace(
            AXUIElementCreateApplication=lambda _pid: self.fail(
                "native AX application creation must not receive the host PID"
            )
        )

        with mock.patch("body.macos_accessibility.os.getpid", return_value=777):
            with self.assertRaisesRegex(RuntimeError, "own Accessibility process"):
                runtime.application(777)

    def test_frontmost_app_resolution_uses_workspace_metadata_without_ax(self) -> None:
        application = SimpleNamespace(
            processIdentifier=lambda: 777,
            localizedName=lambda: "Ipet",
        )
        workspace = SimpleNamespace(frontmostApplication=lambda: application)
        appkit = SimpleNamespace(
            NSWorkspace=SimpleNamespace(sharedWorkspace=lambda: workspace)
        )
        runtime = object.__new__(_AXRuntime)

        with mock.patch.dict("sys.modules", {"AppKit": appkit}):
            self.assertEqual(runtime.focused_application(), (777, "Ipet"))

    def test_capture_returns_bounded_semantic_elements_and_redacts_secure_value(self) -> None:
        runtime = _FakeAXRuntime()

        result = capture_macos_accessibility(
            "微信",
            platform_name="darwin",
            runtime_factory=lambda: runtime,
            cache=self.cache,
        )

        self.assertEqual(result["status"], "success")
        self.assertTrue(result["usable"])
        self.assertEqual(result["app"]["surface"], "wechat_gui")
        send = next(item for item in result["elements"] if item.get("identifier") == "send-button")
        self.assertIn("press", send["supports"])
        self.assertEqual(send["ax_ref"]["app_id"], "wechat")
        self.assertTrue(send["ax_ref"]["fingerprint"])
        secure = next(item for item in result["elements"] if item.get("identifier") == "password")
        self.assertNotIn("do-not-expose", str(secure))
        self.assertIn("未使用截图", result["text"])
        self.assertTrue(runtime.closed)

    def test_container_only_tree_is_not_usable_and_allows_screenshot_fallback(self) -> None:
        runtime = _FakeAXRuntime()
        runtime.nodes = {
            1: {"AXRole": "AXApplication", "AXTitle": "WeChat", "children": [2]},
            2: {"AXRole": "AXWindow", "AXTitle": "WeChat", "children": []},
        }

        result = capture_macos_accessibility(
            "WeChat",
            platform_name="darwin",
            runtime_factory=lambda: runtime,
            cache=self.cache,
        )

        self.assertEqual(result["status"], "empty")
        self.assertFalse(result["usable"])

    def test_action_re_resolves_approved_reference_and_uses_ax_press(self) -> None:
        captured = capture_macos_accessibility(
            "WeChat",
            platform_name="darwin",
            runtime_factory=_FakeAXRuntime,
            cache=self.cache,
        )
        send = next(item for item in captured["elements"] if item.get("identifier") == "send-button")
        runtime = _FakeAXRuntime()

        result = perform_macos_accessibility_action(
            "WeChat",
            send["ax_ref"],
            platform_name="darwin",
            runtime_factory=lambda: runtime,
            cache=self.cache,
        )

        self.assertEqual(runtime.performed, [(3, "AXPress")])
        self.assertTrue(result["ax_target_verified"])
        self.assertTrue(result["ax_action_performed"])
        self.assertEqual(result["method"], "macos_accessibility")

    def test_semantic_action_blocks_self_pid_before_constructing_ax_runtime(self) -> None:
        captured = capture_macos_accessibility(
            "WeChat",
            platform_name="darwin",
            runtime_factory=_FakeAXRuntime,
            cache=self.cache,
        )
        send = next(item for item in captured["elements"] if item.get("identifier") == "send-button")
        runtime_factory_calls: list[str] = []

        with self.assertRaisesRegex(RuntimeError, "own Accessibility process"):
            perform_macos_accessibility_action(
                {
                    "app": "Ipet",
                    "bundle_id": "com.example.Ipet",
                    "pid": "777",
                },
                send["ax_ref"],
                platform_name="darwin",
                runtime_factory=lambda: runtime_factory_calls.append("runtime") or _FakeAXRuntime(),
                cache=self.cache,
                current_pid_provider=lambda: 777,
            )

        self.assertEqual(runtime_factory_calls, [])

    def test_focus_action_requires_and_verifies_editable_element(self) -> None:
        captured = capture_macos_accessibility(
            "WeChat",
            platform_name="darwin",
            runtime_factory=_FakeAXRuntime,
            cache=self.cache,
        )
        input_element = next(item for item in captured["elements"] if item.get("identifier") == "message-input")
        runtime = _FakeAXRuntime()

        result = perform_macos_accessibility_action(
            "微信",
            input_element["ax_ref"],
            operation="focus",
            platform_name="darwin",
            runtime_factory=lambda: runtime,
            cache=self.cache,
        )

        self.assertEqual(result["ax_action"], "AXFocused")
        self.assertTrue(runtime.nodes[4]["AXFocused"])

    def test_reordered_duplicate_targets_fail_closed_instead_of_trusting_old_path(self) -> None:
        capture_runtime = _FakeAXRuntime()
        capture_runtime.nodes[3].pop("AXIdentifier")
        capture_runtime.nodes[6] = {
            **capture_runtime.nodes[3],
            "AXPosition": {"x": 700, "y": 700},
        }
        capture_runtime.nodes[2]["children"] = [3, 4, 5, 6]
        captured = capture_macos_accessibility(
            "WeChat",
            platform_name="darwin",
            runtime_factory=lambda: capture_runtime,
            cache=self.cache,
        )
        send = next(
            item
            for item in captured["elements"]
            if item.get("title") == "发送" and item.get("bounds", {}).get("x") == 900
        )
        action_runtime = _FakeAXRuntime()
        action_runtime.nodes[3].pop("AXIdentifier")
        action_runtime.nodes[6] = {
            **action_runtime.nodes[3],
            "AXPosition": {"x": 700, "y": 700},
        }
        action_runtime.nodes[2]["children"] = [6, 4, 5, 3]

        with self.assertRaisesRegex(RuntimeError, "ambiguous"):
            perform_macos_accessibility_action(
                "WeChat",
                send["ax_ref"],
                platform_name="darwin",
                runtime_factory=lambda: action_runtime,
                cache=self.cache,
            )

    def test_active_vision_uses_structured_ax_frame_without_screen_capture(self) -> None:
        accessibility = capture_macos_accessibility(
            "WeChat",
            platform_name="darwin",
            runtime_factory=_FakeAXRuntime,
            cache=self.cache,
        )
        provider_calls = []

        def accessibility_provider(target_app, **kwargs):
            provider_calls.append((target_app, kwargs))
            return accessibility

        frame = capture_active_vision_frame_payload(
            _Window(),
            {
                "mode": "desktop_survey",
                "target_app": "WeChat",
                "target_hint": "发送按钮",
                "accessibility_query": "发送",
                "accessibility_cache_mode": "prefer_cache",
                "accessibility_result_limit": 4,
            },
            {},
            platform_name="darwin",
            accessibility_provider=accessibility_provider,
            screen_provider=lambda: self.fail("screen capture should not run when AX is usable"),
        )

        self.assertEqual(frame["capture_backend"], "macos_accessibility")
        self.assertNotIn("data_url", frame)
        self.assertEqual(frame["active_observation"]["verify_result"]["status"], "observed")
        self.assertEqual(frame["accessibility"]["app"]["app_id"], "wechat")
        self.assertEqual(provider_calls[0][0], "WeChat")
        self.assertEqual(provider_calls[0][1]["query"], "发送")
        self.assertEqual(provider_calls[0][1]["cache_mode"], "prefer_cache")
        self.assertEqual(provider_calls[0][1]["result_limit"], 4)

    def test_active_vision_respects_disabled_product_accessibility_permission(self) -> None:
        calls = []

        def frame_encoder(_screen, _config):
            return {
                "capture_backend": "test_screen",
                "mime_type": "image/png",
                "data_url": "data:image/png;base64,AA==",
                "frame_hash": "sha256:test",
            }

        frame = capture_active_vision_frame_payload(
            _Window(),
            {
                "mode": "desktop_survey",
                "target_app": "WeChat",
                "accessibility_enabled": False,
            },
            {},
            platform_name="darwin",
            accessibility_provider=lambda *_args, **_kwargs: calls.append("ax"),
            screen_provider=lambda: object(),
            frame_encoder=frame_encoder,
            desktop_targets_provider=lambda **_kwargs: [],
            dock_items_provider=lambda **_kwargs: [],
            running_apps_provider=lambda **_kwargs: [],
            sleeper=lambda _seconds: None,
        )

        self.assertEqual(calls, [])
        self.assertEqual(frame["capture_backend"], "test_screen")

    def test_large_qq_tree_is_cached_and_only_top_search_results_leave_body(self) -> None:
        cache = AXSnapshotCache()
        first = capture_macos_accessibility(
            "QQ",
            platform_name="darwin",
            runtime_factory=_LargeQQRuntime,
            cache=cache,
            query="找到张三联系人",
            result_limit=8,
        )

        self.assertEqual(first["status"], "success")
        self.assertGreater(first["total_element_count"], 400)
        self.assertEqual(len(first["elements"]), 1)
        self.assertTrue(any(item.get("label") == "张三" for item in first["elements"]))
        self.assertFalse(first["search"]["cache_hit"])
        self.assertLess(len(json.dumps(first, ensure_ascii=False)), 20_000)

        second = capture_macos_accessibility(
            "QQ",
            platform_name="darwin",
            runtime_factory=lambda: _LargeQQRuntime(fail_on_application=True),
            cache=cache,
            query="发送按钮",
            cache_mode="prefer_cache",
            result_limit=5,
        )

        self.assertTrue(second["search"]["cache_hit"])
        self.assertEqual(len(second["elements"]), 1)
        self.assertTrue(any(item.get("identifier") == "send-button" for item in second["elements"]))
        self.assertEqual(first["snapshot_id"], second["snapshot_id"])

    def test_ax_store_marks_process_changes_and_optional_ttl_expiry(self) -> None:
        now = [10.0]
        cache = AXSnapshotCache(ttl_sec=5, clock=lambda: now[0])
        cache.put(
            "qq",
            pid=100,
            elements=[{"role": "AXButton", "label": "发送", "supports": ["press"]}],
            visited_count=1,
            capture_truncated=False,
        )

        self.assertEqual(
            search_macos_accessibility_cache("QQ", "发送", pid=100, cache=cache)["status"],
            "hit",
        )
        changed = search_macos_accessibility_cache("QQ", "发送", pid=101, cache=cache)
        self.assertEqual(changed["status"], "hit")
        self.assertTrue(changed["stale"])
        self.assertEqual(changed["stale_reason"], "process_changed")
        cache.put(
            "qq",
            pid=100,
            elements=[{"role": "AXButton", "label": "发送", "supports": ["press"]}],
            visited_count=1,
            capture_truncated=False,
        )
        now[0] += 6
        self.assertEqual(
            search_macos_accessibility_cache("QQ", "发送", pid=100, cache=cache)["reason"],
            "snapshot_expired",
        )

    def test_ax_query_searches_each_app_cache_with_model_supplied_terms(self) -> None:
        cache = AXSnapshotCache()
        cases = (
            ("qq", "QQ", 501, "张三", "张三 AXRow", "AXRow"),
            ("wechat", "微信", 502, "文件传输助手", "文件传输助手 AXRow", "AXRow"),
            ("music", "音乐", 503, "播放", "播放 AXButton", "AXButton"),
            ("finder", "访达", 504, "Downloads", "Downloads AXRow", "AXRow"),
        )
        for app_id, _target_app, pid, label, _query, role in cases:
            cache.put(
                app_id,
                pid=pid,
                elements=[
                    {
                        "role": role,
                        "label": label,
                        "identifier": f"{app_id}-target",
                        "supports": ["press"],
                    }
                ],
                visited_count=1,
                capture_truncated=False,
            )

        snapshot_ids: set[str] = set()
        for _app_id, target_app, pid, label, query, _role in cases:
            with self.subTest(target_app=target_app):
                result = search_macos_accessibility_cache(
                    target_app,
                    query,
                    pid=pid,
                    cache=cache,
                )

                self.assertEqual(result["status"], "hit")
                self.assertEqual(result["query"], query)
                self.assertEqual(result["matched_count"], 1)
                self.assertEqual([item["label"] for item in result["elements"]], [label])
                snapshot_ids.add(result["snapshot_id"])

        self.assertEqual(len(snapshot_ids), len(cases))

    def test_composite_row_inherits_descendant_label_and_outranks_closed_menu(self) -> None:
        runtime = _CompositeMusicRuntime()
        captured = capture_macos_accessibility(
            "Music",
            platform_name="darwin",
            runtime_factory=lambda: runtime,
            cache=self.cache,
            query="请打开专辑列表",
        )

        self.assertTrue(captured["search"]["sufficient"])
        self.assertGreaterEqual(captured["search"]["actionable_match_count"], 1)
        target = captured["elements"][0]
        self.assertEqual(target["role"], "AXRow")
        self.assertEqual(target["label"], "专辑")
        self.assertEqual(target["label_source"], "descendant")
        self.assertTrue(target["ax_ref"]["fingerprint"])
        menu = next(
            item
            for item in captured["elements"]
            if item.get("role") == "AXMenuItem" and item.get("label") == "专辑"
        )
        self.assertEqual(menu["semantic_path"], "控制 > 随机播放 > 专辑")

        action_runtime = _CompositeMusicRuntime()
        action = perform_macos_accessibility_action(
            "Music",
            target["ax_ref"],
            platform_name="darwin",
            runtime_factory=lambda: action_runtime,
            cache=self.cache,
        )

        self.assertTrue(action["ax_action_performed"])
        self.assertEqual(action["ax_action"], "AXSelected")
        self.assertTrue(action_runtime.nodes[4]["AXSelected"])

    def test_collection_query_returns_bounded_hierarchy_context(self) -> None:
        cache = AXSnapshotCache()
        cache.put(
            "music",
            pid=777,
            elements=[
                {"role": "AXList", "tree_path": [0]},
                {"role": "AXStaticText", "tree_path": [0, 0], "label": "专辑"},
                {
                    "role": "AXRow",
                    "tree_path": [0, 1],
                    "label": "Today Is A Beautiful Day",
                    "supports": ["select"],
                    "ax_ref": {"fingerprint": "album-1"},
                },
                {
                    "role": "AXRow",
                    "tree_path": [0, 2],
                    "label": "迷跡波",
                    "supports": ["select"],
                    "ax_ref": {"fingerprint": "album-2"},
                },
            ],
            visited_count=4,
            capture_truncated=False,
        )

        result = cache.search("music", pid=777, query="读取专辑列表中可见的名称", limit=6)

        labels = [item.get("label") for item in result["elements"]]
        self.assertIn("Today Is A Beautiful Day", labels)
        self.assertIn("迷跡波", labels)
        self.assertGreaterEqual(result["contextual_count"], 2)
        self.assertTrue(result["sufficient"])

    def test_legacy_snapshot_rebuilds_safe_composite_row_reference(self) -> None:
        cache = AXSnapshotCache()
        cache.put(
            "music",
            pid=777,
            elements=[
                {"role": "AXOutline", "tree_path": [0], "label": "边栏"},
                {
                    "role": "AXRow",
                    "subrole": "AXOutlineRow",
                    "tree_path": [0, 0],
                    "supports": ["select"],
                    "bounds": {"x": 90, "y": 260, "width": 210, "height": 28},
                },
                {
                    "role": "AXStaticText",
                    "tree_path": [0, 0, 0],
                    "label": "专辑",
                    "value": "专辑",
                    "bounds": {"x": 126, "y": 264, "width": 42, "height": 20},
                },
            ],
            visited_count=3,
            capture_truncated=False,
        )

        result = cache.search("music", pid=777, query="打开专辑", limit=4)
        target = result["elements"][0]

        self.assertEqual(target["role"], "AXRow")
        self.assertEqual(target["label"], "专辑")
        self.assertEqual(target["semantic_path"], "边栏 > 专辑")
        self.assertEqual(target["ax_ref_source"], "snapshot_geometry")
        self.assertTrue(target["ax_ref"]["fingerprint"])
        self.assertEqual(result["actionable_match_count"], 1)
        self.assertTrue(result["sufficient"])

    def test_explicit_ax_role_query_is_insufficient_when_only_same_named_menu_matches(self) -> None:
        cache = AXSnapshotCache()
        cache.put(
            "qq",
            pid=888,
            elements=[
                {
                    "role": "AXMenuItem",
                    "tree_path": [1, 0],
                    "label": "搜索",
                    "title": "搜索",
                    "supports": ["press"],
                    "ax_ref": {"fingerprint": "closed-menu"},
                }
            ],
            visited_count=1,
            capture_truncated=False,
        )

        result = cache.search("qq", pid=888, query="搜索 AXTextField", limit=4)

        self.assertEqual(result["requested_roles"], ["axtextfield"])
        self.assertEqual(result["role_match_count"], 0)
        self.assertFalse(result["sufficient"])
        self.assertEqual(result["insufficiency_reason"], "requested_role_not_found")
        generic = cache.search("qq", pid=888, query="搜索", limit=4)
        self.assertFalse(generic["sufficient"])
        self.assertEqual(generic["insufficiency_reason"], "only_hidden_menu_matches")
        explicit_menu = cache.search("qq", pid=888, query="搜索 AXMenuItem", limit=4)
        self.assertTrue(explicit_menu["sufficient"])

    def test_persistent_store_splits_full_tree_and_survives_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "ax_trees"
            cache = AXSnapshotCache(storage_dir=root, chunk_size=50)
            elements = [
                {
                    "role": "AXRow",
                    "label": f"项目 {index}",
                    "identifier": f"row-{index}",
                    "supports": ["select"],
                }
                for index in range(121)
            ]
            snapshot = cache.put(
                "finder",
                pid=700,
                elements=elements,
                visited_count=121,
                capture_truncated=False,
            )

            snapshot_dir = root / "finder" / snapshot.snapshot_id
            self.assertEqual(len(list(snapshot_dir.glob("elements-*.jsonl"))), 3)
            metadata = json.loads((snapshot_dir / "meta.json").read_text(encoding="utf-8"))
            self.assertTrue(metadata["full_tree"])
            reloaded = AXSnapshotCache(storage_dir=root, chunk_size=50)
            status = reloaded.status()
            self.assertEqual(reloaded._entries, {})
            self.assertEqual(status["app_count"], 1)
            self.assertEqual(status["element_count"], 121)
            self.assertGreater(status["size_bytes"], 0)
            reloaded.invalidate("finder", reason="metadata_only_test")
            self.assertEqual(reloaded._entries, {})
            self.assertTrue(reloaded.status()["apps"][0]["stale"])
            result = reloaded.search("finder", pid=700, query="项目 119", limit=3)

            self.assertEqual(result["status"], "hit")
            self.assertTrue(result["persistent"])
            self.assertEqual(result["source"], "disk")
            self.assertEqual(result["stale_reason"], "metadata_only_test")
            self.assertEqual(result["elements"][0]["identifier"], "row-119")
            self.assertGreaterEqual(result["matched_count"], 1)

    def test_persistent_capture_never_writes_secure_text_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "ax_trees"
            result = capture_macos_accessibility(
                "WeChat",
                platform_name="darwin",
                runtime_factory=_FakeAXRuntime,
                cache=AXSnapshotCache(storage_dir=root),
            )

            self.assertEqual(result["status"], "success")
            persisted = "\n".join(
                path.read_text(encoding="utf-8")
                for path in root.rglob("*")
                if path.is_file()
            )
            self.assertNotIn("do-not-expose", persisted)
            self.assertIn('"protected":true', persisted)

    def test_persistent_cache_redacts_protected_values_at_its_own_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "ax_trees"
            cache = AXSnapshotCache(storage_dir=root)
            cache.put(
                "wechat",
                pid=777,
                elements=[
                    {
                        "role": "AXTextField",
                        "subrole": "AXSecureTextField",
                        "protected": True,
                        "value": "cache-boundary-secret",
                        "ax_ref": {
                            "app_id": "wechat",
                            "role": "AXTextField",
                            "value": "cache-boundary-secret",
                        },
                    }
                ],
                visited_count=1,
                capture_truncated=False,
            )

            persisted = "\n".join(
                path.read_text(encoding="utf-8")
                for path in root.rglob("*")
                if path.is_file()
            )
            self.assertNotIn("cache-boundary-secret", persisted)
            self.assertNotIn(
                "cache-boundary-secret",
                str(cache.search("wechat", pid=777, query="", limit=1)),
            )

    def test_full_capture_persists_roles_outside_old_semantic_allowlist(self) -> None:
        runtime = _FakeAXRuntime()
        runtime.nodes[6] = {"AXRole": "AXGroup", "children": []}
        runtime.nodes[2]["children"].append(6)

        captured = capture_macos_accessibility(
            "WeChat",
            platform_name="darwin",
            runtime_factory=lambda: runtime,
            cache=self.cache,
        )

        self.assertEqual(captured["total_element_count"], 6)
        self.assertTrue(any(item.get("role") == "AXGroup" for item in captured["elements"]))
        snapshot = self.cache._entries["wechat"]
        self.assertEqual(snapshot.elements[0]["tree_path"], [])
        self.assertEqual(snapshot.elements[-1]["tree_path"], [0, 3])

    def test_manual_refresh_updates_running_supported_app_persistent_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = AXSnapshotCache(storage_dir=Path(tmp) / "ax_trees")

            result = refresh_macos_accessibility_index(
                target_apps=["微信"],
                platform_name="darwin",
                runtime_factory=_FakeAXRuntime,
                cache=cache,
            )

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["updated_count"], 1)
            self.assertEqual(result["updates"][0]["app_id"], "wechat")
            self.assertTrue(next(item for item in result["apps"] if item["app_id"] == "wechat")["stored"])
            self.assertTrue((Path(result["storage_dir"]) / "wechat" / "current.json").exists())

    def test_generic_running_app_can_capture_search_and_execute_ax_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = AXSnapshotCache(storage_dir=Path(tmp) / "ax_trees")

            def running_apps_provider(**_kwargs):
                return (
                    [
                        {
                            "app": "Safari",
                            "bundle_id": "com.apple.Safari",
                            "pid": "777",
                            "source": "running_app",
                        }
                    ],
                    [],
                )

            captured = capture_macos_accessibility(
                "Safari",
                platform_name="darwin",
                runner=lambda *_args, **_kwargs: None,
                runtime_factory=_FakeAXRuntime,
                cache=cache,
                query="发送",
                running_apps_provider=running_apps_provider,
            )

            self.assertEqual(captured["status"], "success")
            self.assertEqual(captured["app"]["name"], "Safari")
            self.assertEqual(captured["app"]["bundle_id"], "com.apple.Safari")
            self.assertEqual(captured["app"]["surface"], "native_app_gui")
            searched = search_macos_accessibility_cache(
                "Safari",
                "发送",
                pid=777,
                cache=cache,
            )
            self.assertEqual(searched["status"], "hit")
            send = next(item for item in captured["elements"] if item.get("identifier") == "send-button")
            runtime = _FakeAXRuntime()
            action = perform_macos_accessibility_action(
                "Safari",
                send["ax_ref"],
                platform_name="darwin",
                runner=lambda *_args, **_kwargs: None,
                runtime_factory=lambda: runtime,
                cache=cache,
                running_apps_provider=running_apps_provider,
            )

            self.assertTrue(action["ax_action_performed"])
            self.assertEqual(runtime.performed, [(3, "AXPress")])

    def test_manual_refresh_discovers_all_running_gui_apps_dynamically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = AXSnapshotCache(storage_dir=Path(tmp) / "ax_trees")
            provider_calls: list[dict] = []

            def running_apps_provider(**kwargs):
                provider_calls.append(kwargs)
                return (
                    [
                        {"app": "Safari", "bundle_id": "com.apple.Safari", "pid": "777"},
                        {"app": "Notes", "bundle_id": "com.apple.Notes", "pid": "778"},
                    ],
                    [],
                )

            class MultiAppRuntime(_FakeAXRuntime):
                def application(self, pid: int) -> int:
                    return 1 if pid in {777, 778} else 0

            result = refresh_macos_accessibility_index(
                platform_name="darwin",
                runner=lambda *_args, **_kwargs: None,
                runtime_factory=MultiAppRuntime,
                cache=cache,
                running_apps_provider=running_apps_provider,
            )

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["discovered_count"], 2)
            self.assertEqual(result["updated_count"], 2)
            self.assertEqual({item["name"] for item in result["apps"]}, {"Safari", "Notes"})
            self.assertEqual(provider_calls[0]["limit"], 128)
            self.assertTrue(provider_calls[0]["include_hidden"])

    def test_all_app_refresh_excludes_host_pid_before_any_ax_call(self) -> None:
        cache = AXSnapshotCache()
        application_calls: list[int] = []
        host_profile = resolve_macos_ax_app(
            {"app": "Ipet", "bundle_id": "com.example.Ipet", "pid": "777"}
        )
        cache.put(
            host_profile["app_id"],
            app_name="Ipet",
            bundle_id="com.example.Ipet",
            pid=777,
            elements=[{"role": "AXButton", "label": "old host snapshot"}],
            visited_count=1,
            capture_truncated=False,
        )

        class RecordingRuntime(_FakeAXRuntime):
            def application(self, pid: int) -> int:
                application_calls.append(pid)
                return 1 if pid == 778 else 0

        result = refresh_macos_accessibility_index(
            platform_name="darwin",
            runner=lambda *_args, **_kwargs: None,
            runtime_factory=RecordingRuntime,
            cache=cache,
            running_apps_provider=lambda **_kwargs: (
                [
                    {"app": "Ipet", "bundle_id": "com.example.Ipet", "pid": "777"},
                    {"app": "Notes", "bundle_id": "com.apple.Notes", "pid": "778"},
                ],
                [],
            ),
            current_pid_provider=lambda: 777,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["discovered_count"], 1)
        self.assertEqual(result["updated_count"], 1)
        self.assertEqual(result["self_excluded_count"], 1)
        self.assertEqual(result["self_excluded_apps"][0]["name"], "Ipet")
        self.assertEqual(result["self_excluded_apps"][0]["pid"], 777)
        self.assertEqual(application_calls, [778])
        host_snapshot = cache.search(
            host_profile["app_id"],
            pid=777,
            query="old host snapshot",
        )
        self.assertTrue(host_snapshot["stale"])
        self.assertEqual(host_snapshot["stale_reason"], "self_process_excluded")

    def test_all_app_refresh_keeps_nonrunning_snapshot_and_marks_it_stale(self) -> None:
        cache = AXSnapshotCache()
        cache.put(
            "app-closed",
            app_name="Closed App",
            bundle_id="example.closed",
            aliases=("Closed App",),
            pid=901,
            elements=[{"role": "AXButton", "label": "旧按钮"}],
            visited_count=1,
            capture_truncated=False,
        )

        result = refresh_macos_accessibility_index(
            platform_name="darwin",
            runner=lambda *_args, **_kwargs: None,
            runtime_factory=_FakeAXRuntime,
            cache=cache,
            running_apps_provider=lambda **_kwargs: ([], []),
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["discovered_count"], 0)
        self.assertEqual(result["skipped_count"], 1)
        self.assertEqual(result["updates"][0]["status"], "unavailable")
        stored = cache.search("app-closed", pid=901, query="旧按钮")
        self.assertEqual(stored["status"], "hit")
        self.assertTrue(stored["stale"])
        self.assertEqual(stored["stale_reason"], "refresh_not_running")

    def test_failed_manual_refresh_keeps_last_snapshot_but_marks_it_stale(self) -> None:
        cache = AXSnapshotCache()
        cache.put(
            "wechat",
            pid=777,
            elements=[{"role": "AXButton", "label": "发送"}],
            visited_count=1,
            capture_truncated=False,
        )

        result = refresh_macos_accessibility_index(
            target_apps=["微信"],
            platform_name="darwin",
            runner=lambda *_args, **_kwargs: None,
            runtime_factory=lambda: _LargeQQRuntime(fail_on_application=True),
            cache=cache,
        )

        self.assertEqual(result["updated_count"], 0)
        self.assertEqual(result["updates"][0]["status"], "unavailable")
        stored = cache.search("wechat", pid=777, query="发送")
        self.assertEqual(stored["status"], "hit")
        self.assertTrue(stored["stale"])
        self.assertEqual(stored["stale_reason"], "refresh_unavailable")

    def test_successful_semantic_action_marks_persistent_snapshot_stale(self) -> None:
        cache = AXSnapshotCache()
        captured = capture_macos_accessibility(
            "WeChat",
            platform_name="darwin",
            runtime_factory=_FakeAXRuntime,
            cache=cache,
            query="发送",
        )
        send = next(item for item in captured["elements"] if item.get("identifier") == "send-button")

        perform_macos_accessibility_action(
            "WeChat",
            send["ax_ref"],
            platform_name="darwin",
            runtime_factory=_FakeAXRuntime,
            cache=cache,
        )

        stored = search_macos_accessibility_cache("WeChat", "发送", pid=777, cache=cache)
        self.assertEqual(stored["status"], "hit")
        self.assertTrue(stored["stale"])
        self.assertEqual(stored["stale_reason"], "semantic_action_performed")


if __name__ == "__main__":
    unittest.main()
