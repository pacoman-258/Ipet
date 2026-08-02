from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from body import macos_accessibility as macos_ax
from body.active_vision import capture_active_vision_frame_payload
from body.macos_accessibility import (
    _AXRuntime,
    _capture_tree,
    _chat_header_matches_input_window,
    _host_process_tree_pids,
    _hit_test_point,
    _read_node,
    _target_app_pid,
    _valid_ax_ref,
    activate_macos_accessibility_application,
    capture_macos_accessibility,
    perform_macos_accessibility_action,
    refresh_macos_accessibility_index,
    resolve_macos_ax_app,
    search_macos_accessibility_cache,
    verify_macos_accessibility_chat_context,
    verify_macos_accessibility_input_value,
)
from body.macos_accessibility_cache import (
    AXSnapshotCache,
    _collection_branch_labels,
    _element_label,
)


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

    def set_string_attribute(self, element: int, name: str, value: str) -> None:
        self.nodes[element][name] = str(value)


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


class _MenuOnlyQQRuntime(_FakeAXRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.nodes = {
            1: {
                "AXRole": "AXApplication",
                "AXTitle": "QQ",
                "children": [2],
            },
            2: {
                "AXRole": "AXMenuBar",
                "children": [3],
            },
            3: {
                "AXRole": "AXMenuItem",
                "AXTitle": "QQ",
                "AXEnabled": True,
                "actions": ["AXPress"],
                "children": [],
            },
        }

    def focused_application(self) -> tuple[int, str]:
        return 888, "QQ"

    def application(self, pid: int) -> int:
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


class _QQWebConversationRuntime(_FakeAXRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.nodes = {
            1: {"AXRole": "AXApplication", "AXTitle": "QQ", "children": [2]},
            2: {
                "AXRole": "AXWindow",
                "AXTitle": "QQ",
                "AXPosition": {"x": 175, "y": 98},
                "AXSize": {"width": 960, "height": 648},
                "children": [3, 6],
            },
            3: {
                "AXRole": "AXGroup",
                "AXDescription": "会话列表",
                "AXEnabled": True,
                "AXPosition": {"x": 231, "y": 190},
                "AXSize": {"width": 250, "height": 552},
                "actions": ["AXShowMenu"],
                "children": [4],
            },
            4: {
                "AXRole": "AXGroup",
                "AXSubrole": "AXApplicationGroup",
                "AXEnabled": True,
                "AXPosition": {"x": 231, "y": 382},
                "AXSize": {"width": 250, "height": 64},
                "actions": ["AXShowMenu", "AXScrollToVisible"],
                "children": [5],
            },
            5: {
                "AXRole": "AXStaticText",
                "AXValue": "测试联系人",
                "AXEnabled": True,
                "AXPosition": {"x": 295, "y": 396},
                "AXSize": {"width": 70, "height": 16},
                "actions": ["AXShowMenu", "AXScrollToVisible"],
                "children": [],
            },
            6: {
                "AXRole": "AXToolbar",
                "AXDescription": "会话",
                "AXEnabled": True,
                "AXPosition": {"x": 481, "y": 592},
                "AXSize": {"width": 470, "height": 40},
                "actions": ["AXShowMenu"],
                "children": [],
            },
        }

    def focused_application(self) -> tuple[int, str]:
        return 888, "QQ"

    def application(self, pid: int) -> int:
        return 1 if pid == 888 else 0


class _ChatScopeRuntime(_FakeAXRuntime):
    def __init__(self, *, header: str, draft: str = "") -> None:
        super().__init__()
        self.nodes = {
            1: {"AXRole": "AXApplication", "AXTitle": "QQ", "children": [2]},
            2: {
                "AXRole": "AXWindow",
                "AXTitle": "QQ",
                "AXPosition": {"x": 100, "y": 80},
                "AXSize": {"width": 900, "height": 700},
                "children": [3, 4, 5],
            },
            3: {
                "AXRole": "AXStaticText",
                "AXValue": header,
                "AXPosition": {"x": 560, "y": 112},
                "AXSize": {"width": 120, "height": 24},
                "children": [],
            },
            4: {
                "AXRole": "AXTextArea",
                "AXDescription": "消息输入框",
                "AXIdentifier": "message-input",
                "AXValue": draft,
                "AXEnabled": True,
                "AXFocused": True,
                "AXPosition": {"x": 390, "y": 590},
                "AXSize": {"width": 590, "height": 120},
                "children": [],
            },
            5: {
                "AXRole": "AXRow",
                "AXTitle": "目标会话",
                "AXPosition": {"x": 110, "y": 220},
                "AXSize": {"width": 250, "height": 64},
                "children": [],
            },
        }

    def focused_application(self) -> tuple[int, str]:
        return 888, "QQ"

    def application(self, pid: int) -> int:
        return 1 if pid == 888 else 0


class MacOSAccessibilityTests(unittest.TestCase):
    def test_native_ax_gate_serializes_calls_and_prioritizes_foreground(self) -> None:
        first_entered = threading.Event()
        release_first = threading.Event()
        second_ready = threading.Event()
        order: list[str] = []

        def first_background() -> None:
            with macos_ax._ax_native_operation(foreground=False):
                order.append("background-1")
                first_entered.set()
                self.assertTrue(release_first.wait(timeout=2))

        def second_background() -> None:
            second_ready.set()
            with macos_ax._ax_native_operation(foreground=False):
                order.append("background-2")

        def foreground() -> None:
            with macos_ax._ax_native_operation(foreground=True):
                order.append("foreground")

        threads = [
            threading.Thread(target=first_background),
            threading.Thread(target=second_background),
            threading.Thread(target=foreground),
        ]
        threads[0].start()
        self.assertTrue(first_entered.wait(timeout=1))
        threads[1].start()
        self.assertTrue(second_ready.wait(timeout=1))
        threads[2].start()
        with macos_ax._AX_NATIVE_OPERATION_CONDITION:
            self.assertTrue(
                macos_ax._AX_NATIVE_OPERATION_CONDITION.wait_for(
                    lambda: macos_ax._AX_NATIVE_FOREGROUND_WAITERS == 1,
                    timeout=1,
                )
            )
        release_first.set()
        for thread in threads:
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())

        self.assertEqual(
            order,
            ["background-1", "foreground", "background-2"],
        )

    def setUp(self) -> None:
        self.cache = AXSnapshotCache()

    def test_application_children_merge_ax_windows_with_stable_deduplication(self) -> None:
        runtime = object.__new__(_AXRuntime)
        runtime._application_elements = {1}
        runtime.release = mock.Mock()
        runtime._element_identity_signature = lambda element: (
            "AXWindow",
            3 if element in {3, 30} else element,
        )
        runtime._attribute_elements = mock.Mock(
            side_effect=[
                ([2, 3], False),
                ([30, 4], False),
            ]
        )
        runtime._attribute_element = mock.Mock(side_effect=[0, 0])

        children, truncated = runtime.children(1, limit=8)

        self.assertEqual(children, [2, 3, 4])
        self.assertFalse(truncated)
        self.assertEqual(
            runtime._attribute_elements.call_args_list,
            [
                mock.call(1, "AXChildren", limit=8),
                mock.call(1, "AXWindows", limit=6),
            ],
        )
        self.assertEqual(
            runtime._attribute_element.call_args_list,
            [
                mock.call(1, "AXFocusedWindow"),
                mock.call(1, "AXMainWindow"),
            ],
        )
        runtime.release.assert_called_once_with(30)

    def test_application_children_keeps_distinct_windows_with_weak_signatures(self) -> None:
        runtime = object.__new__(_AXRuntime)
        runtime._application_elements = {1}
        runtime.release = mock.Mock()
        runtime._element_identity_signature = lambda _element: (
            "AXWindow",
            "",
            "",
            "",
            0,
            0,
            0,
            0,
        )
        runtime._attribute_elements = mock.Mock(
            side_effect=[
                ([3], False),
                ([4], False),
            ]
        )
        runtime._attribute_element = mock.Mock(side_effect=[0, 0])

        children, truncated = runtime.children(1, limit=8)

        self.assertEqual(children, [3, 4])
        self.assertFalse(truncated)
        runtime.release.assert_not_called()

    def test_application_children_recovers_direct_window_roots(self) -> None:
        runtime = object.__new__(_AXRuntime)
        runtime._application_elements = {1}
        runtime.release = mock.Mock()
        runtime._element_identity_signature = lambda element: (
            "AXWindow" if element in {3, 30} else "AXMenuBar",
            "main-window" if element in {3, 30} else str(element),
        )
        runtime._attribute_elements = mock.Mock(
            side_effect=[
                ([2], False),
                ([], False),
            ]
        )
        runtime._attribute_element = mock.Mock(side_effect=[3, 30])

        children, truncated = runtime.children(1, limit=8)

        self.assertEqual(children, [2, 3])
        self.assertFalse(truncated)
        self.assertEqual(
            runtime._attribute_element.call_args_list,
            [
                mock.call(1, "AXFocusedWindow"),
                mock.call(1, "AXMainWindow"),
            ],
        )
        runtime.release.assert_called_once_with(30)

    def test_capture_tree_borrows_and_retains_application_reference(self) -> None:
        runtime = _FakeAXRuntime()
        runtime.retain = mock.Mock(side_effect=lambda value: value)
        runtime.release = mock.Mock()

        elements, visited, truncated = _capture_tree(
            runtime,
            1,
            resolve_macos_ax_app("WeChat"),
            max_elements=20,
            max_depth=8,
            timeout_sec=1.0,
        )

        self.assertEqual(visited, 5)
        self.assertFalse(truncated)
        self.assertTrue(elements)
        root = next(item for item in elements if item["tree_path"] == [])
        window = next(item for item in elements if item["role"] == "AXWindow")
        self.assertTrue(root["children_complete"])
        self.assertEqual(root["children_count"], 1)
        self.assertEqual(root["enqueued_child_count"], 1)
        self.assertTrue(window["children_complete"])
        self.assertEqual(window["children_count"], 3)
        self.assertEqual(window["enqueued_child_count"], 3)
        runtime.retain.assert_called_once_with(1)
        self.assertEqual(
            sum(call.args == (1,) for call in runtime.release.call_args_list),
            1,
        )

    def test_capture_tree_deduplicates_cycles_without_false_truncation(self) -> None:
        runtime = _FakeAXRuntime()
        runtime.nodes = {
            1: {
                "AXRole": "AXApplication",
                "AXTitle": "Loop",
                "children": [2, 2],
            },
            2: {
                "AXRole": "AXMenu",
                "AXTitle": "Loop menu",
                "children": [1, 1],
            },
        }

        elements, visited, truncated = _capture_tree(
            runtime,
            1,
            resolve_macos_ax_app("WeChat"),
            max_elements=20_000,
            max_depth=96,
            timeout_sec=1.0,
        )

        self.assertEqual(visited, 2)
        self.assertEqual(len(elements), 2)
        self.assertFalse(truncated)

    def test_capture_tree_reads_window_content_before_menu_descendants(self) -> None:
        runtime = _FakeAXRuntime()
        runtime.nodes = {
            1: {
                "AXRole": "AXApplication",
                "children": [2, 3],
            },
            2: {
                "AXRole": "AXMenuBar",
                "children": [4],
            },
            3: {
                "AXRole": "AXWindow",
                "children": [5],
            },
            4: {
                "AXRole": "AXMenuItem",
                "AXTitle": "Deferred menu item",
                "children": [],
            },
            5: {
                "AXRole": "AXButton",
                "AXTitle": "Window control",
                "children": [],
            },
        }

        elements, _visited, truncated = _capture_tree(
            runtime,
            1,
            resolve_macos_ax_app("Music"),
            max_elements=20,
            max_depth=8,
            timeout_sec=1.0,
        )

        roles = [item["role"] for item in elements]
        self.assertFalse(truncated)
        self.assertLess(roles.index("AXButton"), roles.index("AXMenuItem"))

    def test_capture_tree_deduplicates_cycle_when_cfhash_is_zero(self) -> None:
        runtime = _FakeAXRuntime()
        runtime.nodes = {
            1: {"AXRole": "AXApplication", "children": [2]},
            2: {"AXRole": "AXGroup", "children": [1]},
        }
        runtime.element_identity = lambda _value: 0
        runtime.elements_equal = lambda left, right: left == right

        elements, visited, truncated = _capture_tree(
            runtime,
            1,
            resolve_macos_ax_app("WeChat"),
            max_elements=12,
            max_depth=12,
            timeout_sec=1.0,
        )

        self.assertEqual(visited, 2)
        self.assertEqual(len(elements), 2)
        self.assertFalse(truncated)

    def test_capture_tree_marks_depth_cutoff_with_unvisited_children(self) -> None:
        runtime = _FakeAXRuntime()
        runtime.nodes = {
            1: {
                "AXRole": "AXApplication",
                "AXTitle": "Deep",
                "children": [2],
            },
            2: {
                "AXRole": "AXGroup",
                "children": [3],
            },
            3: {
                "AXRole": "AXButton",
                "AXTitle": "Hidden below depth",
                "children": [],
            },
        }

        _elements, visited, truncated = _capture_tree(
            runtime,
            1,
            resolve_macos_ax_app("WeChat"),
            max_elements=20,
            max_depth=1,
            timeout_sec=1.0,
        )

        self.assertEqual(visited, 2)
        self.assertTrue(truncated)

    def test_chat_context_verification_binds_input_to_live_header_and_draft(self) -> None:
        profile = resolve_macos_ax_app("QQ")
        source_runtime = _ChatScopeRuntime(
            header="目标会话",
            draft="准备发送的草稿",
        )
        input_ref = _read_node(
            source_runtime,
            4,
            profile,
            path=(0, 1),
            ancestors=(
                {"role": "AXApplication", "label": "QQ"},
                {"role": "AXWindow", "label": "QQ"},
            ),
        )["ax_ref"]

        verified = verify_macos_accessibility_chat_context(
            "QQ",
            intended_chat="目标会话",
            input_ax_ref=input_ref,
            expected_text="准备发送的草稿",
            platform_name="darwin",
            runtime_factory=lambda: _ChatScopeRuntime(
                header="目标会话",
                draft="准备发送的草稿",
            ),
        )

        self.assertTrue(verified["chat_identity_verified"])
        self.assertTrue(verified["chat_draft_verified"])
        self.assertTrue(verified["frontmost_verified"])
        self.assertEqual(verified["target_pid"], 888)

    def test_chat_header_allows_web_area_shared_with_reviewed_input(self) -> None:
        elements = [
            {
                "role": "AXWindow",
                "tree_path": [0],
                "bounds": {"x": 100, "y": 80, "width": 900, "height": 700},
            },
            {
                "role": "AXWebArea",
                "tree_path": [0, 0],
                "bounds": {"x": 100, "y": 80, "width": 900, "height": 700},
            },
            {
                "role": "AXButton",
                "label": "目标会话",
                "tree_path": [0, 0, 0],
                "bounds": {"x": 410, "y": 112, "width": 120, "height": 24},
            },
            {
                "role": "AXTextArea",
                "tree_path": [0, 0, 1],
                "bounds": {"x": 390, "y": 590, "width": 590, "height": 120},
            },
        ]

        self.assertTrue(
            _chat_header_matches_input_window(
                elements,
                elements[-1],
                "目标会话",
            )
        )

    def test_chat_header_uses_top_title_row_before_closer_body_branch(self) -> None:
        elements = [
            {
                "role": "AXWindow",
                "tree_path": [0],
                "bounds": {"x": 100, "y": 80, "width": 900, "height": 700},
            },
            {
                "role": "AXGroup",
                "tree_path": [0, 0],
                "bounds": {"x": 390, "y": 170, "width": 590, "height": 540},
            },
            {
                "role": "AXButton",
                "label": "伪装会话",
                "tree_path": [0, 0, 1],
                "bounds": {"x": 560, "y": 180, "width": 120, "height": 24},
            },
            {
                "role": "AXStaticText",
                "label": "真实会话",
                "tree_path": [0, 1],
                "bounds": {"x": 560, "y": 112, "width": 120, "height": 24},
            },
            {
                "role": "AXTextArea",
                "tree_path": [0, 0, 2],
                "bounds": {"x": 390, "y": 590, "width": 590, "height": 120},
            },
        ]

        self.assertTrue(
            _chat_header_matches_input_window(
                elements,
                elements[-1],
                "真实会话",
            )
        )
        self.assertFalse(
            _chat_header_matches_input_window(
                elements,
                elements[-1],
                "伪装会话",
            )
        )

    def test_chat_header_uses_matching_editor_identity_below_app_toolbar(self) -> None:
        elements = [
            {
                "role": "AXWindow",
                "tree_path": [0],
                "bounds": {"x": 100, "y": 80, "width": 900, "height": 700},
            },
            {
                "role": "AXButton",
                "label": "应用工具",
                "tree_path": [0, 0],
                "bounds": {"x": 410, "y": 90, "width": 120, "height": 24},
            },
            {
                "role": "AXButton",
                "label": "目标会话",
                "tree_path": [0, 1],
                "bounds": {"x": 410, "y": 130, "width": 120, "height": 24},
            },
            {
                "role": "AXTextArea",
                "description": "目标会话",
                "tree_path": [0, 2],
                "bounds": {"x": 390, "y": 590, "width": 590, "height": 120},
            },
        ]

        self.assertTrue(
            _chat_header_matches_input_window(
                elements,
                elements[-1],
                "目标会话",
            )
        )

    def test_chat_header_prefers_deeper_chat_pane_over_global_toolbar(self) -> None:
        elements = [
            {
                "role": "AXWindow",
                "tree_path": [0],
                "bounds": {"x": 100, "y": 70, "width": 900, "height": 720},
            },
            {
                "role": "AXWebArea",
                "tree_path": [0, 0],
                "bounds": {"x": 100, "y": 70, "width": 900, "height": 720},
            },
            {
                "role": "AXGroup",
                "tree_path": [0, 0, 2],
                "bounds": {"x": 100, "y": 70, "width": 900, "height": 720},
            },
            {
                "role": "AXButton",
                "label": "全局工具栏名称",
                "tree_path": [0, 0, 2, 3],
                "bounds": {"x": 400, "y": 90, "width": 150, "height": 24},
            },
            {
                "role": "AXGroup",
                "tree_path": [0, 0, 2, 35],
                "bounds": {"x": 360, "y": 80, "width": 640, "height": 710},
            },
            {
                "role": "AXGroup",
                "tree_path": [0, 0, 2, 35, 132],
                "bounds": {"x": 360, "y": 100, "width": 640, "height": 50},
            },
            {
                "role": "AXButton",
                "label": "真实会话",
                "tree_path": [0, 0, 2, 35, 132, 0],
                "bounds": {"x": 400, "y": 112, "width": 150, "height": 24},
            },
            {
                "role": "AXGroup",
                "tree_path": [0, 0, 2, 35, 149],
                "bounds": {"x": 360, "y": 570, "width": 640, "height": 190},
            },
            {
                "role": "AXTextArea",
                "tree_path": [0, 0, 2, 35, 149, 0],
                "bounds": {"x": 390, "y": 590, "width": 590, "height": 120},
            },
        ]

        self.assertTrue(
            _chat_header_matches_input_window(
                elements,
                elements[-1],
                "真实会话",
            )
        )
        self.assertFalse(
            _chat_header_matches_input_window(
                elements,
                elements[-1],
                "全局工具栏名称",
            )
        )

    def test_chat_context_verification_rejects_stable_input_after_chat_switch(self) -> None:
        profile = resolve_macos_ax_app("QQ")
        source_runtime = _ChatScopeRuntime(header="目标会话")
        input_ref = _read_node(
            source_runtime,
            4,
            profile,
            path=(0, 1),
            ancestors=(
                {"role": "AXApplication", "label": "QQ"},
                {"role": "AXWindow", "label": "QQ"},
            ),
        )["ax_ref"]

        with self.assertRaisesRegex(RuntimeError, "intended chat"):
            verify_macos_accessibility_chat_context(
                {
                    "app": "QQ",
                    "bundle_id": "com.tencent.qq",
                    "pid": 888,
                },
                intended_chat="目标会话",
                input_ax_ref=input_ref,
                platform_name="darwin",
                runtime_factory=lambda: _ChatScopeRuntime(header="另一个会话"),
            )

    def test_chat_context_rejects_same_name_body_text_as_header(self) -> None:
        profile = resolve_macos_ax_app("QQ")
        source_runtime = _ChatScopeRuntime(header="真实会话")
        input_ref = _read_node(
            source_runtime,
            4,
            profile,
            path=(0, 1),
            ancestors=(
                {"role": "AXApplication", "label": "QQ"},
                {"role": "AXWindow", "label": "QQ"},
            ),
        )["ax_ref"]

        def runtime_factory():
            runtime = _ChatScopeRuntime(header="真实会话")
            runtime.nodes[2]["children"].append(6)
            runtime.nodes[6] = {
                "AXRole": "AXButton",
                "AXTitle": "伪装会话",
                "AXPosition": {"x": 560, "y": 180},
                "AXSize": {"width": 120, "height": 24},
                "actions": ["AXPress"],
                "children": [],
            }
            return runtime

        with self.assertRaisesRegex(RuntimeError, "intended chat"):
            verify_macos_accessibility_chat_context(
                "QQ",
                intended_chat="伪装会话",
                input_ax_ref=input_ref,
                platform_name="darwin",
                runtime_factory=runtime_factory,
            )

    def test_chat_context_requires_target_app_to_be_frontmost(self) -> None:
        profile = resolve_macos_ax_app("QQ")
        source_runtime = _ChatScopeRuntime(header="目标会话")
        input_ref = _read_node(
            source_runtime,
            4,
            profile,
            path=(0, 1),
            ancestors=(
                {"role": "AXApplication", "label": "QQ"},
                {"role": "AXWindow", "label": "QQ"},
            ),
        )["ax_ref"]

        def runtime_factory():
            runtime = _ChatScopeRuntime(header="目标会话")
            runtime.focused_application = lambda: (999, "其他应用")
            return runtime

        with self.assertRaisesRegex(RuntimeError, "not frontmost"):
            verify_macos_accessibility_chat_context(
                {
                    "app": "QQ",
                    "bundle_id": "com.tencent.qq",
                    "pid": 888,
                },
                intended_chat="目标会话",
                input_ax_ref=input_ref,
                platform_name="darwin",
                runtime_factory=runtime_factory,
            )

    def test_chat_send_verification_requires_reviewed_input_focus(self) -> None:
        profile = resolve_macos_ax_app("QQ")
        source_runtime = _ChatScopeRuntime(header="目标会话")
        input_ref = _read_node(
            source_runtime,
            4,
            profile,
            path=(0, 1),
            ancestors=(
                {"role": "AXApplication", "label": "QQ"},
                {"role": "AXWindow", "label": "QQ"},
            ),
        )["ax_ref"]

        def runtime_factory():
            runtime = _ChatScopeRuntime(header="目标会话")
            runtime.nodes[4]["AXFocused"] = False
            return runtime

        with self.assertRaisesRegex(RuntimeError, "not focused"):
            verify_macos_accessibility_chat_context(
                "QQ",
                intended_chat="目标会话",
                input_ax_ref=input_ref,
                require_input_focused=True,
                platform_name="darwin",
                runtime_factory=runtime_factory,
            )

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

    def test_host_process_tree_includes_nested_qt_helpers(self) -> None:
        result = _host_process_tree_pids(
            current_pid_provider=lambda: 777,
            runner=lambda *_args, **_kwargs: SimpleNamespace(
                returncode=0,
                stdout=(
                    "  777     1\n"
                    "  778   777\n"
                    "  779   778\n"
                    "  880     1\n"
                ),
            ),
        )

        self.assertEqual(result, {777, 778, 779})

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

    def test_chat_search_and_message_input_kinds_are_signed_into_ax_refs(self) -> None:
        runtime = _FakeAXRuntime()
        runtime.nodes[6] = {
            "AXRole": "AXTextField",
            "AXDescription": "搜索",
            "AXIdentifier": "global-search",
            "AXEnabled": True,
            "AXPosition": {"x": 200, "y": 100},
            "AXSize": {"width": 240, "height": 32},
            "actions": [],
            "children": [],
        }
        profile = resolve_macos_ax_app("QQ")

        search = _read_node(
            runtime,
            6,
            profile,
            path=(0, 0),
            ancestors=(),
        )
        message = _read_node(
            runtime,
            4,
            profile,
            path=(0, 1),
            ancestors=(),
        )

        self.assertEqual(search["ax_ref"]["input_kind"], "search_field")
        self.assertEqual(message["ax_ref"]["input_kind"], "chat_message")
        self.assertTrue(_valid_ax_ref(search["ax_ref"]))
        tampered = {**search["ax_ref"], "input_kind": "chat_message"}
        self.assertFalse(_valid_ax_ref(tampered))

    def test_unlabelled_compact_chat_header_field_keeps_search_signature(self) -> None:
        runtime = _FakeAXRuntime()
        runtime.nodes[6] = {
            "AXRole": "AXTextField",
            "AXEnabled": True,
            "AXPosition": {"x": 160, "y": 108},
            "AXSize": {"width": 220, "height": 28},
            "actions": [],
            "children": [],
        }
        runtime.nodes[7] = {
            "AXRole": "AXTextField",
            "AXEnabled": True,
            "AXPosition": {"x": 320, "y": 610},
            "AXSize": {"width": 420, "height": 42},
            "actions": [],
            "children": [],
        }
        profile = resolve_macos_ax_app("QQ")
        main_window = (
            {
                "role": "AXWindow",
                "bounds": {
                    "x": 100,
                    "y": 80,
                    "width": 900,
                    "height": 700,
                },
            },
        )

        search = _read_node(
            runtime,
            6,
            profile,
            path=(0, 0),
            ancestors=main_window,
        )
        message = _read_node(
            runtime,
            7,
            profile,
            path=(0, 1),
            ancestors=main_window,
        )

        self.assertEqual(search["ax_ref"]["input_kind"], "search_field")
        self.assertEqual(message["ax_ref"]["input_kind"], "chat_message")
        self.assertTrue(_valid_ax_ref(search["ax_ref"]))
        self.assertTrue(_valid_ax_ref(message["ax_ref"]))

    def test_unlabelled_field_in_small_login_window_is_not_a_search_bypass(self) -> None:
        runtime = _FakeAXRuntime()
        runtime.nodes[6] = {
            "AXRole": "AXTextField",
            "AXEnabled": True,
            "AXPosition": {"x": 650, "y": 350},
            "AXSize": {"width": 180, "height": 32},
            "actions": [],
            "children": [],
        }

        field = _read_node(
            runtime,
            6,
            resolve_macos_ax_app("WeChat"),
            path=(0, 0),
            ancestors=(
                {
                    "role": "AXWindow",
                    "bounds": {
                        "x": 595,
                        "y": 234,
                        "width": 280,
                        "height": 380,
                    },
                },
            ),
        )

        self.assertEqual(field["ax_ref"]["input_kind"], "chat_message")
        self.assertTrue(_valid_ax_ref(field["ax_ref"]))

    def test_capture_blocks_host_helper_before_constructing_ax_runtime(self) -> None:
        runtime_factory_calls: list[str] = []

        with mock.patch(
            "body.macos_accessibility._host_process_tree_pids",
            return_value={777, 778},
        ):
            result = capture_macos_accessibility(
                {
                    "app": "QtWebEngineProcess",
                    "bundle_id": "",
                    "pid": "778",
                },
                platform_name="darwin",
                runtime_factory=lambda: runtime_factory_calls.append("runtime")
                or _FakeAXRuntime(),
                cache=self.cache,
                current_pid_provider=lambda: 777,
            )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["pid"], 778)
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

    def test_frontmost_app_fallback_uses_process_metadata_without_ax(self) -> None:
        runtime = object.__new__(_AXRuntime)

        with mock.patch.dict("sys.modules", {"AppKit": None}), mock.patch.object(
            macos_ax._active_macos,
            "enumerate_active_vision_running_app_candidates",
            return_value=[
                {
                    "app": "Ipet",
                    "pid": "777",
                    "frontmost": True,
                }
            ],
        ):
            self.assertEqual(runtime.focused_application(), (777, "Ipet"))

    def test_resolved_profile_pid_does_not_probe_frontmost_ax(self) -> None:
        profile = {**resolve_macos_ax_app("WeChat"), "pid": 888}
        runtime = SimpleNamespace(
            focused_application=lambda: self.fail(
                "a resolved target PID must not probe the frontmost AX application"
            )
        )

        self.assertEqual(
            _target_app_pid(
                profile,
                runtime,
                runner=lambda *_args, **_kwargs: self.fail(
                    "a resolved target PID must not fall back to process search"
                ),
            ),
            888,
        )

    def test_background_app_pid_prefers_bundle_gui_process_over_pgrep(self) -> None:
        application = SimpleNamespace(
            processIdentifier=lambda: 888,
            isTerminated=lambda: False,
            activationPolicy=lambda: 0,
            isActive=lambda: False,
        )
        appkit = SimpleNamespace(
            NSRunningApplication=SimpleNamespace(
                runningApplicationsWithBundleIdentifier_=lambda _bundle_id: [
                    application
                ]
            )
        )
        runtime = SimpleNamespace(
            focused_application=lambda: (999, "ChatGPT")
        )

        with mock.patch.dict("sys.modules", {"AppKit": appkit}):
            pid = _target_app_pid(
                resolve_macos_ax_app("WeChat"),
                runtime,
                runner=lambda *_args, **_kwargs: self.fail(
                    "pgrep must remain the last fallback"
                ),
            )

        self.assertEqual(pid, 888)

    def test_ax_application_can_be_activated_without_pyobjc(self) -> None:
        class ActivationRuntime:
            def __init__(self) -> None:
                self.set_calls: list[tuple[int, str, bool]] = []
                self.released: list[int] = []
                self.closed = False

            def trusted(self) -> bool:
                return True

            def focused_application(self) -> tuple[int, str]:
                return 999, "ChatGPT"

            def application(self, pid: int) -> int:
                return 1 if pid == 888 else 0

            def set_timeout(self, _element: int, _timeout: float) -> None:
                return None

            def set_boolean_attribute(
                self,
                element: int,
                name: str,
                value: bool,
            ) -> None:
                self.set_calls.append((element, name, value))

            def attribute(self, _element: int, name: str) -> object:
                return name == "AXFrontmost"

            def release(self, element: int) -> None:
                self.released.append(element)

            def close(self) -> None:
                self.closed = True

        runtime = ActivationRuntime()
        activated = activate_macos_accessibility_application(
            {"app": "QQ", "bundle_id": "com.tencent.qq", "pid": 888},
            platform_name="darwin",
            runtime_factory=lambda: runtime,
            current_pid_provider=lambda: 777,
        )

        self.assertTrue(activated)
        self.assertEqual(runtime.set_calls, [(1, "AXFrontmost", True)])
        self.assertEqual(runtime.released, [1])
        self.assertTrue(runtime.closed)

    def test_ax_activation_unminimizes_and_raises_application_windows(self) -> None:
        class WindowActivationRuntime:
            def __init__(self) -> None:
                self.set_calls: list[tuple[int, str, bool]] = []
                self.performed: list[tuple[int, str]] = []
                self.released: list[int] = []

            def trusted(self) -> bool:
                return True

            def focused_application(self) -> tuple[int, str]:
                return 999, "ChatGPT"

            def application(self, pid: int) -> int:
                return 1 if pid == 888 else 0

            def set_timeout(self, _element: int, _timeout: float) -> None:
                return None

            def set_boolean_attribute(
                self,
                element: int,
                name: str,
                value: bool,
            ) -> None:
                self.set_calls.append((element, name, value))

            def attribute(self, element: int, name: str) -> object:
                values = {
                    (1, "AXFrontmost"): True,
                    (2, "AXRole"): "AXWindow",
                    (2, "AXMinimized"): True,
                    (3, "AXRole"): "AXMenuBar",
                }
                return values.get((element, name))

            def children(self, _element: int, *, limit: int) -> tuple[list[int], bool]:
                return [2, 3][:limit], limit < 2

            def actions(self, element: int) -> list[str]:
                return ["AXRaise"] if element == 2 else []

            def perform(self, element: int, action: str) -> None:
                self.performed.append((element, action))

            def release(self, element: int) -> None:
                self.released.append(element)

            def close(self) -> None:
                return None

        runtime = WindowActivationRuntime()
        activated = activate_macos_accessibility_application(
            {"app": "QQ", "bundle_id": "com.tencent.qq", "pid": 888},
            platform_name="darwin",
            runtime_factory=lambda: runtime,
            current_pid_provider=lambda: 777,
        )

        self.assertTrue(activated)
        self.assertIn((1, "AXFrontmost", True), runtime.set_calls)
        self.assertIn((2, "AXMinimized", False), runtime.set_calls)
        self.assertEqual(runtime.performed, [(2, "AXRaise")])
        self.assertEqual(runtime.released, [2, 3, 1])

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

    def test_capture_batches_attributes_without_requesting_secure_values(self) -> None:
        class BatchRuntime(_FakeAXRuntime):
            def __init__(self) -> None:
                super().__init__()
                self.attribute_batches: list[tuple[int, tuple[str, ...]]] = []

            def attributes(
                self,
                element: int,
                names: tuple[str, ...],
            ) -> dict[str, object]:
                self.attribute_batches.append((element, names))
                return {
                    name: self.nodes[element].get(name)
                    for name in names
                }

        runtime = BatchRuntime()
        result = capture_macos_accessibility(
            "微信",
            platform_name="darwin",
            runtime_factory=lambda: runtime,
            cache=self.cache,
        )

        self.assertEqual(result["status"], "success")
        self.assertTrue(
            all(
                "AXValue" not in names
                for element, names in runtime.attribute_batches
                if element == 5
            )
        )
        self.assertEqual(
            len(
                [
                    names
                    for element, names in runtime.attribute_batches
                    if element == 3
                ]
            ),
            2,
        )

    def test_finder_file_name_text_field_is_not_treated_as_an_editor(self) -> None:
        runtime = _FakeAXRuntime()
        runtime.nodes[6] = {
            "AXRole": "AXTextField",
            "AXValue": "Downloads",
            "AXURL": "file:///Users/test/Downloads",
            "AXEnabled": True,
            "AXPosition": {"x": 320, "y": 180},
            "AXSize": {"width": 180, "height": 24},
            "actions": ["AXOpen", "AXConfirm", "AXShowMenu"],
            "children": [],
        }

        node = _read_node(
            runtime,
            6,
            {
                "app_id": "finder",
                "display_name": "访达",
                "bundle_id": "com.apple.finder",
            },
            path=(0, 0, 0, 0, 0),
            ancestors=(
                {"role": "AXBrowser", "label": ""},
                {"role": "AXScrollArea", "label": ""},
                {"role": "AXList", "label": ""},
                {"role": "AXGroup", "label": ""},
            ),
        )

        self.assertEqual(node["label"], "Downloads")
        self.assertEqual(node["url"], "file:///Users/test/Downloads")
        self.assertNotIn("focus", node["supports"])
        self.assertNotIn("type_text", node["supports"])
        self.assertIn("open", node["supports"])
        self.assertIn("show_menu", node["supports"])
        self.assertEqual(node["ax_ref"]["url"], "file:///Users/test/Downloads")

        self.cache.put(
            "finder",
            pid=777,
            elements=[
                {"role": "AXBrowser", "tree_path": [0]},
                {"role": "AXScrollArea", "tree_path": [0, 0]},
                {
                    "role": "AXList",
                    "tree_path": [0, 0, 0],
                    "bounds": {"x": 280, "y": 120, "width": 700, "height": 600},
                },
                {
                    "role": "AXGroup",
                    "tree_path": [0, 0, 0, 0],
                    "bounds": {"x": 300, "y": 160, "width": 220, "height": 32},
                },
                node,
                {
                    "role": "AXOutline",
                    "tree_path": [0, 1],
                    "label": "边栏",
                    "bounds": {"x": 80, "y": 120, "width": 180, "height": 600},
                },
                {
                    "role": "AXRow",
                    "tree_path": [0, 1, 0],
                    "label": "个人收藏",
                    "bounds": {"x": 80, "y": 140, "width": 180, "height": 24},
                },
                {
                    "role": "AXRow",
                    "tree_path": [0, 1, 1],
                    "label": "iCloud",
                    "bounds": {"x": 80, "y": 164, "width": 180, "height": 24},
                },
            ],
            visited_count=8,
            capture_truncated=False,
        )
        result = self.cache.search(
            "finder",
            pid=777,
            query="打开 Downloads 文件夹",
            limit=8,
        )
        target = next(
            item
            for item in result["elements"]
            if item.get("label") == "Downloads"
            and item.get("context_relation") == "collection_item"
        )
        self.assertTrue(result["sufficient"])
        self.assertEqual(result["collection_label"], "当前文件列表")
        self.assertEqual(target["role"], "AXTextField")
        self.assertIn("open", target["supports"])
        self.assertEqual(target["activation"], "open")
        self.assertEqual(target["ax_ref"]["activation"], "open")

        selection = self.cache.search(
            "finder",
            pid=777,
            query="选择 Downloads 文件夹",
            limit=8,
        )
        selection_target = next(
            item
            for item in selection["elements"]
            if item.get("label") == "Downloads"
            and item.get("context_relation") == "collection_item"
        )
        self.assertEqual(selection_target["role"], "AXGroup")
        self.assertIn("hit_test", selection_target["supports"])
        self.assertNotIn("open", selection_target["supports"])

        menu = self.cache.search(
            "finder",
            pid=777,
            query="打开 Downloads 文件夹的上下文菜单",
            limit=8,
        )
        menu_target = next(
            item
            for item in menu["elements"]
            if item.get("label") == "Downloads"
            and item.get("context_relation") == "collection_item"
        )
        self.assertTrue(menu["sufficient"])
        self.assertEqual(menu["actionable_match_count"], 1)
        self.assertEqual(menu_target["role"], "AXTextField")
        self.assertEqual(menu_target["activation"], "show_menu")
        self.assertEqual(menu_target["ax_ref"]["activation"], "show_menu")

        action_runtime = _FakeAXRuntime()
        action_runtime.nodes = {
            1: {"AXRole": "AXApplication", "AXTitle": "Finder", "children": [2]},
            2: {"AXRole": "AXBrowser", "children": [3]},
            3: {"AXRole": "AXScrollArea", "children": [4]},
            4: {"AXRole": "AXList", "children": [5]},
            5: {
                "AXRole": "AXGroup",
                "AXPosition": {"x": 300, "y": 160},
                "AXSize": {"width": 220, "height": 32},
                "children": [6],
            },
            6: dict(runtime.nodes[6]),
        }
        action_runtime.focused_application = lambda: (777, "Finder")
        action = perform_macos_accessibility_action(
            "Finder",
            target["ax_ref"],
            platform_name="darwin",
            runtime_factory=lambda: action_runtime,
            cache=self.cache,
        )
        self.assertEqual(action_runtime.performed, [(6, "AXOpen")])
        self.assertEqual(action["ax_action"], "AXOpen")

    def test_nonsettable_text_label_is_not_exposed_as_a_typing_target(self) -> None:
        class NonEditableTextRuntime(_FakeAXRuntime):
            def attribute_settable(self, _element: int, _name: str) -> bool:
                return False

        runtime = NonEditableTextRuntime()
        runtime.nodes[6] = {
            "AXRole": "AXTextField",
            "AXDescription": "Seventh Heaven",
            "AXEnabled": True,
            "AXPosition": {"x": 0, "y": 0},
            "AXSize": {"width": 123, "height": 16},
            "actions": [],
            "children": [],
        }

        node = _read_node(
            runtime,
            6,
            resolve_macos_ax_app("音乐"),
            path=(1, 0, 3, 0, 1, 0),
            ancestors=(),
        )

        self.assertEqual(node["label"], "Seventh Heaven")
        self.assertNotIn("focus", node["supports"])
        self.assertNotIn("type_text", node["supports"])
        self.assertNotIn("ax_ref", node)

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

    def test_capture_enables_electron_manual_accessibility_before_reading_tree(self) -> None:
        class ElectronRuntime(_FakeAXRuntime):
            def __init__(self) -> None:
                super().__init__()
                self.manual_accessibility_calls: list[int] = []
                self.nodes = {
                    1: {
                        "AXRole": "AXApplication",
                        "AXTitle": "QQ",
                        "children": [2],
                    },
                    2: {
                        "AXRole": "AXMenuBar",
                        "AXTitle": "QQ menu",
                        "children": [],
                    },
                }

            def focused_application(self) -> tuple[int, str]:
                return 777, "QQ"

            def enable_manual_accessibility(self, application: int) -> bool:
                self.manual_accessibility_calls.append(application)
                self.nodes[1]["children"] = [2, 3]
                self.nodes[3] = {
                    "AXRole": "AXWindow",
                    "AXTitle": "QQ",
                    "AXPosition": {"x": 100, "y": 80},
                    "AXSize": {"width": 900, "height": 700},
                    "children": [4],
                }
                self.nodes[4] = {
                    "AXRole": "AXTextArea",
                    "AXDescription": "目标会话",
                    "AXEnabled": True,
                    "AXPosition": {"x": 350, "y": 600},
                    "AXSize": {"width": 580, "height": 100},
                    "actions": ["AXPress"],
                    "children": [],
                }
                return True

        runtime = ElectronRuntime()

        result = capture_macos_accessibility(
            "QQ",
            platform_name="darwin",
            runtime_factory=lambda: runtime,
            cache=self.cache,
            query="当前会话输入框 AXTextArea",
        )

        self.assertEqual(runtime.manual_accessibility_calls, [1])
        self.assertTrue(result["manual_accessibility_enabled"])
        self.assertGreaterEqual(result["total_element_count"], 4)
        self.assertEqual(result["search"]["role_match_count"], 1)

    def test_action_re_resolves_approved_reference_and_uses_ax_press(self) -> None:
        captured = capture_macos_accessibility(
            "WeChat",
            platform_name="darwin",
            runtime_factory=_FakeAXRuntime,
            cache=self.cache,
        )
        send = next(item for item in captured["elements"] if item.get("identifier") == "send-button")

        class DirectPathRuntime(_FakeAXRuntime):
            def attribute(self, element: int, name: str):
                if element in {4, 5}:
                    raise AssertionError(
                        "reviewed path resolution must not scan unrelated siblings"
                    )
                return super().attribute(element, name)

        runtime = DirectPathRuntime()

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

    def test_action_replaces_editable_ax_value_and_verifies_exact_text(self) -> None:
        captured = capture_macos_accessibility(
            "WeChat",
            platform_name="darwin",
            runtime_factory=_FakeAXRuntime,
            cache=self.cache,
            query="消息输入框 AXTextArea",
        )
        editor = next(
            item
            for item in captured["elements"]
            if item.get("identifier") == "message-input"
        )
        runtime = _FakeAXRuntime()

        result = perform_macos_accessibility_action(
            "WeChat",
            editor["ax_ref"],
            operation="set_value",
            value="safe draft",
            platform_name="darwin",
            runtime_factory=lambda: runtime,
            cache=self.cache,
        )

        self.assertEqual(runtime.nodes[4]["AXValue"], "safe draft")
        self.assertEqual(result["ax_action"], "AXValueSet")
        self.assertTrue(result["input_value_set"])
        self.assertNotIn("safe draft", result.values())

    def test_explicit_context_menu_query_uses_native_ax_show_menu(self) -> None:
        class MenuRuntime(_FakeAXRuntime):
            def __init__(self) -> None:
                super().__init__()
                self.nodes[3]["actions"] = ["AXPress", "AXShowMenu"]

        captured = capture_macos_accessibility(
            "WeChat",
            platform_name="darwin",
            runtime_factory=MenuRuntime,
            cache=self.cache,
            query="显示发送按钮的上下文菜单",
        )
        target = next(
            item
            for item in captured["elements"]
            if item.get("label") == "发送"
        )

        self.assertTrue(captured["search"]["sufficient"])
        self.assertEqual(target["activation"], "show_menu")
        self.assertEqual(target["activation_effect"], "context_menu")
        self.assertEqual(target["ax_ref"]["activation"], "show_menu")

        action_runtime = MenuRuntime()
        action = perform_macos_accessibility_action(
            "WeChat",
            target["ax_ref"],
            platform_name="darwin",
            runtime_factory=lambda: action_runtime,
            cache=self.cache,
        )

        self.assertEqual(action["ax_action"], "AXShowMenu")
        self.assertEqual(action_runtime.performed, [(3, "AXShowMenu")])

    def test_context_menu_action_refreshes_then_executes_a_menu_command(self) -> None:
        class MenuChainRuntime(_FakeAXRuntime):
            def __init__(self) -> None:
                super().__init__()
                self.nodes[3]["actions"] = ["AXPress", "AXShowMenu"]
                self.nodes[6] = {
                    "AXRole": "AXMenu",
                    "AXTitle": "上下文菜单",
                    "children": [7],
                }
                self.nodes[7] = {
                    "AXRole": "AXMenuItem",
                    "AXTitle": "显示简介",
                    "AXEnabled": True,
                    "actions": ["AXPress"],
                    "children": [],
                }

            def perform(self, element: int, action: str) -> None:
                super().perform(element, action)
                if element == 3 and action == "AXShowMenu":
                    self.nodes[2]["children"] = [3, 4, 5, 6]

        runtime = MenuChainRuntime()
        initial = capture_macos_accessibility(
            "WeChat",
            platform_name="darwin",
            runtime_factory=lambda: runtime,
            cache=self.cache,
            query="显示发送按钮的上下文菜单",
        )
        menu_ref = next(
            item["ax_ref"]
            for item in initial["elements"]
            if item.get("label") == "发送"
        )
        perform_macos_accessibility_action(
            "WeChat",
            menu_ref,
            platform_name="darwin",
            runtime_factory=lambda: runtime,
            cache=self.cache,
        )

        self.assertTrue(
            search_macos_accessibility_cache(
                "WeChat",
                "显示简介",
                pid=777,
                cache=self.cache,
            )["stale"]
        )
        refreshed = capture_macos_accessibility(
            "WeChat",
            platform_name="darwin",
            runtime_factory=lambda: runtime,
            cache=self.cache,
            query="点击显示简介菜单项 AXMenuItem",
        )
        command_ref = next(
            item["ax_ref"]
            for item in refreshed["elements"]
            if item.get("label") == "显示简介"
        )
        perform_macos_accessibility_action(
            "WeChat",
            command_ref,
            platform_name="darwin",
            runtime_factory=lambda: runtime,
            cache=self.cache,
        )

        self.assertEqual(
            runtime.performed,
            [(3, "AXShowMenu"), (7, "AXPress")],
        )

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

    def test_focus_action_uses_verified_geometry_when_electron_focus_is_delayed(self) -> None:
        captured = capture_macos_accessibility(
            "WeChat",
            platform_name="darwin",
            runtime_factory=_FakeAXRuntime,
            cache=self.cache,
        )
        input_element = next(item for item in captured["elements"] if item.get("identifier") == "message-input")

        class _DelayedFocusRuntime(_FakeAXRuntime):
            def set_boolean_attribute(self, element: int, name: str, value: bool) -> None:
                if name != "AXFocused":
                    super().set_boolean_attribute(element, name, value)

        runtime = _DelayedFocusRuntime()
        clicks: list[tuple[int, int]] = []

        def click_and_focus(x: int, y: int) -> None:
            clicks.append((x, y))
            runtime.nodes[4]["AXFocused"] = True

        result = perform_macos_accessibility_action(
            "微信",
            input_element["ax_ref"],
            operation="focus",
            platform_name="darwin",
            runtime_factory=lambda: runtime,
            cache=self.cache,
            geometry_clicker=click_and_focus,
            sleeper=lambda _seconds: None,
        )

        self.assertEqual(clicks, [(655, 650)])
        self.assertEqual(result["ax_action"], "AXGeometryFocus")
        self.assertEqual(result["method"], "macos_accessibility+core_graphics")
        self.assertEqual((result["x"], result["y"]), clicks[0])

    def test_focus_action_accepts_application_focused_element_identity(self) -> None:
        captured = capture_macos_accessibility(
            "WeChat",
            platform_name="darwin",
            runtime_factory=_FakeAXRuntime,
            cache=self.cache,
        )
        input_element = next(item for item in captured["elements"] if item.get("identifier") == "message-input")

        class _ApplicationFocusRuntime(_FakeAXRuntime):
            def set_boolean_attribute(self, _element: int, _name: str, _value: bool) -> None:
                return None

            def focused_ui_element(self, _application: int) -> int:
                return 4

            def elements_equal(self, left: int, right: int) -> bool:
                return left == right

        result = perform_macos_accessibility_action(
            "微信",
            input_element["ax_ref"],
            operation="focus",
            platform_name="darwin",
            runtime_factory=_ApplicationFocusRuntime,
            cache=self.cache,
            sleeper=lambda _seconds: None,
        )

        self.assertEqual(result["ax_action"], "AXFocused")

    def test_input_value_verification_re_resolves_and_checks_exact_text(self) -> None:
        captured = capture_macos_accessibility(
            "WeChat",
            platform_name="darwin",
            runtime_factory=_FakeAXRuntime,
            cache=self.cache,
        )
        input_element = next(item for item in captured["elements"] if item.get("identifier") == "message-input")
        runtime = _FakeAXRuntime()
        runtime.nodes[4]["AXFocused"] = True
        runtime.nodes[4]["AXValue"] = "IPET_TEST"

        result = verify_macos_accessibility_input_value(
            "微信",
            input_ax_ref=input_element["ax_ref"],
            expected_text="IPET_TEST",
            platform_name="darwin",
            runtime_factory=lambda: runtime,
            sleeper=lambda _seconds: None,
        )

        self.assertTrue(result["input_value_verified"])
        self.assertTrue(result["input_focus_verified"])
        self.assertTrue(result["postcondition_verified"])
        self.assertNotIn("value", result)

    def test_input_value_verification_fails_closed_on_mismatch(self) -> None:
        captured = capture_macos_accessibility(
            "WeChat",
            platform_name="darwin",
            runtime_factory=_FakeAXRuntime,
            cache=self.cache,
        )
        input_element = next(item for item in captured["elements"] if item.get("identifier") == "message-input")
        runtime = _FakeAXRuntime()
        runtime.nodes[4]["AXFocused"] = True
        runtime.nodes[4]["AXValue"] = "different"

        with self.assertRaisesRegex(RuntimeError, "does not match"):
            verify_macos_accessibility_input_value(
                "微信",
                input_ax_ref=input_element["ax_ref"],
                expected_text="IPET_TEST",
                platform_name="darwin",
                runtime_factory=lambda: runtime,
                sleeper=lambda _seconds: None,
            )

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
        sleeps = []

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
                "settle_ms": 0,
            },
            {},
            platform_name="darwin",
            accessibility_provider=accessibility_provider,
            screen_provider=lambda: self.fail("screen capture should not run when AX is usable"),
            sleeper=lambda seconds: sleeps.append(seconds),
        )

        self.assertEqual(frame["capture_backend"], "macos_accessibility")
        self.assertNotIn("data_url", frame)
        self.assertEqual(frame["active_observation"]["verify_result"]["status"], "observed")
        self.assertEqual(frame["accessibility"]["app"]["app_id"], "wechat")
        self.assertEqual(provider_calls[0][0], "WeChat")
        self.assertEqual(provider_calls[0][1]["query"], "发送")
        self.assertEqual(provider_calls[0][1]["cache_mode"], "prefer_cache")
        self.assertEqual(provider_calls[0][1]["result_limit"], 4)
        self.assertEqual(provider_calls[0][1]["timeout_sec"], 4.0)
        self.assertEqual(sleeps, [0.0])

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

    def test_visual_fallback_hides_host_and_settles_after_ax_attempt(self) -> None:
        window = mock.Mock()
        window.isVisible.return_value = True
        sleeps = []
        capture_visibility = []

        frame = capture_active_vision_frame_payload(
            window,
            {
                "mode": "desktop_survey",
                "target_app": "WeChat",
                "settle_ms": 1,
            },
            {},
            platform_name="darwin",
            accessibility_provider=lambda *_args, **_kwargs: {
                "status": "success",
                "supported": True,
                "usable": False,
                "reason": "insufficient hierarchy",
            },
            screen_provider=lambda: object(),
            frame_encoder=lambda _screen, _config: (
                capture_visibility.append(window.hide.called)
                or {
                    "capture_backend": "test_screen",
                    "mime_type": "image/png",
                    "data_url": "data:image/png;base64,AA==",
                    "frame_hash": "sha256:test",
                }
            ),
            desktop_targets_provider=lambda **_kwargs: [],
            dock_items_provider=lambda **_kwargs: [],
            running_apps_provider=lambda **_kwargs: [],
            sleeper=lambda seconds: sleeps.append(seconds),
        )

        self.assertEqual(frame["capture_backend"], "test_screen")
        self.assertEqual(sleeps, [0.001, 0.001])
        self.assertEqual(capture_visibility, [True])
        window.hide.assert_called_once_with()
        window.show.assert_called_once_with()
        window.raise_.assert_not_called()

    def test_deferred_visual_fallback_never_captures_before_focus_verification(self) -> None:
        frame = capture_active_vision_frame_payload(
            _Window(),
            {
                "mode": "desktop_survey",
                "target_app": "WeChat",
                "defer_visual_fallback": True,
                "settle_ms": 0,
            },
            {},
            platform_name="darwin",
            accessibility_provider=lambda *_args, **_kwargs: {
                "status": "permission_required",
                "supported": True,
                "usable": False,
                "reason": "Accessibility permission is unavailable",
                "app": {
                    "app_id": "wechat",
                    "name": "微信",
                    "bundle_id": "com.tencent.xinWeChat",
                },
            },
            screen_provider=lambda: self.fail(
                "pixels must wait for verified target focus"
            ),
            sleeper=lambda _seconds: None,
        )

        self.assertEqual(frame["capture_backend"], "macos_accessibility")
        self.assertFalse(frame["accessibility"]["usable"])
        self.assertEqual(frame["active_observation"]["status"], "partial")
        self.assertIn(
            "accessibility_unavailable",
            frame["active_observation"]["unknowns"][0],
        )

    def test_verified_application_capture_skips_redundant_target_discovery(self) -> None:
        discovery_calls = []

        def frame_encoder(_screen, _config):
            return {
                "capture_backend": "test_screen",
                "mime_type": "image/png",
                "data_url": "data:image/png;base64,AA==",
                "frame_hash": "sha256:test",
            }

        def discovered(source):
            discovery_calls.append(source)
            return []

        frame = capture_active_vision_frame_payload(
            _Window(),
            {
                "mode": "desktop_survey",
                "target_app": "WeChat",
                "accessibility_enabled": False,
                "target_surface_verified": True,
                "settle_ms": 0,
            },
            {},
            platform_name="darwin",
            screen_provider=lambda: object(),
            frame_encoder=frame_encoder,
            desktop_targets_provider=lambda **_kwargs: discovered("windows"),
            dock_items_provider=lambda **_kwargs: discovered("dock"),
            running_apps_provider=lambda **_kwargs: discovered("running_apps"),
            sleeper=lambda _seconds: None,
        )

        self.assertEqual(discovery_calls, [])
        self.assertEqual(frame["capture_backend"], "test_screen")
        trace = frame["active_observation"]
        self.assertTrue(trace["target_surface_verified"])
        self.assertEqual(trace["desktop_targets"], [])
        self.assertEqual(trace["target_candidates"], [])
        self.assertEqual(trace["discovery_errors"], [])

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

    def test_current_ui_query_refreshes_even_when_cache_was_not_invalidated(self) -> None:
        cache = AXSnapshotCache(ttl_sec=60)
        first = capture_macos_accessibility(
            "QQ",
            platform_name="darwin",
            runtime_factory=_LargeQQRuntime,
            cache=cache,
            query="找到张三联系人",
        )
        changed_runtime = _LargeQQRuntime()
        changed_runtime.nodes[333]["AXTitle"] = "李四"

        second = capture_macos_accessibility(
            "QQ",
            platform_name="darwin",
            runtime_factory=lambda: changed_runtime,
            cache=cache,
            query="读取当前可见的李四联系人",
            cache_mode="prefer_cache",
        )

        self.assertFalse(second["search"]["cache_hit"])
        self.assertNotEqual(first["snapshot_id"], second["snapshot_id"])
        self.assertTrue(any(item.get("label") == "李四" for item in second["elements"]))

    def test_collection_listing_query_refreshes_even_with_fresh_cache(self) -> None:
        cache = AXSnapshotCache(ttl_sec=60)
        first = capture_macos_accessibility(
            "QQ",
            platform_name="darwin",
            runtime_factory=_LargeQQRuntime,
            cache=cache,
            query="联系人",
        )
        changed_runtime = _LargeQQRuntime()
        changed_runtime.nodes[333]["AXTitle"] = "李四"

        second = capture_macos_accessibility(
            "QQ",
            platform_name="darwin",
            runtime_factory=lambda: changed_runtime,
            cache=cache,
            query="列出所有联系人",
            cache_mode="prefer_cache",
        )

        self.assertFalse(second["search"]["cache_hit"])
        self.assertNotEqual(first["snapshot_id"], second["snapshot_id"])

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
        expired = search_macos_accessibility_cache("QQ", "发送", pid=100, cache=cache)
        self.assertEqual(expired["status"], "hit")
        self.assertTrue(expired["stale"])
        self.assertEqual(expired["stale_reason"], "snapshot_expired")
        self.assertFalse(expired["sufficient"])
        self.assertEqual(
            expired["insufficiency_reason"],
            "stale_snapshot_requires_refresh",
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

    def test_requested_ax_role_constrains_but_does_not_pollute_semantic_search(self) -> None:
        cache = AXSnapshotCache()
        cache.put(
            "finder",
            pid=777,
            elements=[
                {
                    "role": "AXButton",
                    "label": "搜索",
                    "supports": ["press"],
                    "bounds": {"x": 10, "y": 10, "width": 20, "height": 20},
                    "ax_ref": {"fingerprint": "search"},
                },
                {
                    "role": "AXButton",
                    "label": "共享",
                    "supports": ["press"],
                    "bounds": {"x": 40, "y": 10, "width": 20, "height": 20},
                    "ax_ref": {"fingerprint": "share"},
                },
                {
                    "role": "AXTextField",
                    "label": "搜索",
                    "supports": ["focus", "type_text"],
                    "bounds": {"x": 70, "y": 10, "width": 160, "height": 20},
                    "ax_ref": {"fingerprint": "search-field"},
                },
            ],
            visited_count=3,
            capture_truncated=False,
        )

        semantic = cache.search("finder", pid=777, query="搜索 AXButton", limit=10)
        role_only = cache.search("finder", pid=777, query="AXButton", limit=10)
        role_listing = cache.search(
            "finder",
            pid=777,
            query="AXButton 可见按钮名称",
            limit=10,
        )

        self.assertEqual([item["label"] for item in semantic["elements"]], ["搜索"])
        self.assertEqual(
            {item["label"] for item in role_only["elements"]},
            {"搜索", "共享"},
        )
        self.assertTrue(role_listing["sufficient"])
        self.assertEqual(role_listing["role_match_count"], 2)
        self.assertEqual(
            {item["label"] for item in role_listing["elements"]},
            {"搜索", "共享"},
        )

    def test_explicit_input_role_does_not_inherit_collection_context_ambiguity(self) -> None:
        cache = AXSnapshotCache()
        cache.put(
            "music",
            pid=777,
            elements=[
                {
                    "role": "AXWindow",
                    "tree_path": [0],
                    "label": "音乐",
                    "bounds": {"x": 100, "y": 80, "width": 900, "height": 700},
                },
                {
                    "role": "AXGrid",
                    "tree_path": [0, 0],
                    "label": "专辑",
                    "supports": ["show_menu"],
                    "bounds": {"x": 260, "y": 150, "width": 700, "height": 560},
                    "ax_ref": {"fingerprint": "album-grid"},
                },
                {
                    "role": "AXToolbar",
                    "tree_path": [0, 1],
                    "label": "专辑工具栏",
                    "supports": ["show_menu"],
                    "bounds": {"x": 260, "y": 100, "width": 700, "height": 48},
                    "ax_ref": {"fingerprint": "album-toolbar"},
                },
                {
                    "role": "AXTextField",
                    "tree_path": [0, 1, 0],
                    "identifier": "filterField",
                    "label": "在专辑中查找",
                    "supports": ["show_menu", "focus", "type_text"],
                    "bounds": {"x": 760, "y": 110, "width": 180, "height": 24},
                    "ax_ref": {"fingerprint": "album-filter"},
                },
            ],
            visited_count=4,
            capture_truncated=False,
        )

        result = cache.search(
            "music",
            pid=777,
            query="聚焦 identifier 为 filterField 的 AXTextField 专辑筛选输入框并准备输入",
            limit=10,
        )

        self.assertTrue(result["sufficient"])
        self.assertEqual(result["actionable_match_count"], 1)
        executable = [
            item
            for item in result["elements"]
            if isinstance(item.get("ax_ref"), dict)
        ]
        self.assertEqual([item["role"] for item in executable], ["AXTextField"])

    def test_explicit_role_context_never_exposes_other_role_references(self) -> None:
        cache = AXSnapshotCache()
        cache.put(
            "qq",
            pid=777,
            elements=[
                {
                    "role": "AXWindow",
                    "tree_path": [0],
                    "label": "QQ",
                    "bounds": {"x": 100, "y": 80, "width": 900, "height": 700},
                },
                {
                    "role": "AXGroup",
                    "tree_path": [0, 0],
                    "label": "搜索工具栏",
                    "supports": ["press"],
                    "bounds": {"x": 120, "y": 100, "width": 300, "height": 48},
                    "ax_ref": {"fingerprint": "search-toolbar"},
                },
                {
                    "role": "AXTextField",
                    "tree_path": [0, 0, 0],
                    "label": "搜索",
                    "supports": ["focus", "type_text"],
                    "bounds": {"x": 140, "y": 112, "width": 240, "height": 24},
                    "ax_ref": {"fingerprint": "search-field"},
                },
                {
                    "role": "AXTextArea",
                    "tree_path": [0, 1],
                    "label": "消息输入框",
                    "supports": ["focus", "type_text"],
                    "bounds": {"x": 400, "y": 600, "width": 500, "height": 100},
                    "ax_ref": {"fingerprint": "message-field"},
                },
            ],
            visited_count=4,
            capture_truncated=False,
        )

        result = cache.search(
            "qq",
            pid=777,
            query="AXTextField",
            limit=10,
        )

        executable = [
            item
            for item in result["elements"]
            if isinstance(item.get("ax_ref"), dict)
        ]
        self.assertEqual([item["role"] for item in executable], ["AXTextField"])
        self.assertEqual(result["actionable_match_count"], 1)

    def test_generic_account_and_button_role_listing_uses_requested_roles(self) -> None:
        cache = AXSnapshotCache()
        cache.put(
            "wechat",
            pid=54206,
            elements=[
                {
                    "role": "AXStaticText",
                    "label": "当前登录用户李某",
                    "value": "当前登录用户李某",
                },
                {
                    "role": "AXButton",
                    "label": "进入微信",
                    "supports": ["press"],
                    "bounds": {"x": 650, "y": 510, "width": 168, "height": 36},
                    "ax_ref": {"fingerprint": "enter-wechat"},
                },
                {
                    "role": "AXButton",
                    "label": "切换账号",
                    "supports": ["press"],
                    "bounds": {"x": 650, "y": 567, "width": 64, "height": 25},
                    "ax_ref": {"fingerprint": "switch-account"},
                },
            ],
            visited_count=3,
            capture_truncated=False,
        )

        result = cache.search(
            "wechat",
            pid=54206,
            query="页面类型 账号提示 主要按钮 AXButton AXStaticText",
            limit=10,
        )

        self.assertTrue(result["sufficient"])
        self.assertEqual(result["role_match_count"], 3)
        self.assertEqual(
            {item["label"] for item in result["elements"]},
            {"当前登录用户李某", "进入微信", "切换账号"},
        )

    def test_model_role_listing_ignores_app_name_and_generic_availability_words(self) -> None:
        cache = AXSnapshotCache()
        cache.put(
            "wechat",
            app_name="微信",
            bundle_id="com.tencent.xinWeChat",
            aliases=("WeChat", "Weixin"),
            pid=54206,
            elements=[
                {
                    "role": "AXWindow",
                    "label": "微信",
                    "bounds": {"x": 500, "y": 300, "width": 420, "height": 360},
                },
                {
                    "role": "AXStaticText",
                    "label": "当前登录用户李某",
                    "value": "当前登录用户李某",
                },
                *[
                    {
                        "role": "AXButton",
                        "label": label,
                        "supports": ["press"],
                        "bounds": {
                            "x": 650,
                            "y": 500 + index * 40,
                            "width": 168,
                            "height": 36,
                        },
                        "ax_ref": {"fingerprint": f"button-{index}"},
                    }
                    for index, label in enumerate(
                        (
                            "进入微信",
                            "网络代理设置",
                            "切换账号",
                            "仅传输文件",
                        )
                    )
                ],
            ],
            visited_count=6,
            capture_truncated=False,
        )

        result = cache.search(
            "wechat",
            pid=54206,
            query="微信当前页面 页面名称 账号提示 AXButton 可用按钮",
            limit=10,
        )

        self.assertTrue(result["sufficient"])
        self.assertEqual(result["requested_roles"], ["axbutton"])
        self.assertEqual(
            set(result["effective_requested_roles"]),
            {"axbutton", "axstatictext", "axwindow"},
        )
        self.assertEqual(result["role_adaptation"], "descriptive_context")
        self.assertEqual(result["current_view_title"], "微信")
        self.assertEqual(result["role_match_count"], 6)
        self.assertEqual(
            {item["label"] for item in result["elements"]},
            {
                "微信",
                "当前登录用户李某",
                "进入微信",
                "网络代理设置",
                "切换账号",
                "仅传输文件",
            },
        )

    def test_finder_desktop_adapts_generic_collection_roles_to_ax_images(self) -> None:
        cache = AXSnapshotCache()
        cache.put(
            "finder",
            pid=643,
            elements=[
                {
                    "role": "AXApplication",
                    "tree_path": [],
                    "label": "访达",
                },
                {
                    "role": "AXScrollArea",
                    "tree_path": [1],
                    "label": "桌面",
                    "focused": True,
                    "bounds": {"x": 0, "y": 0, "width": 1470, "height": 956},
                },
                {
                    "role": "AXGroup",
                    "tree_path": [1, 0],
                    "label": "桌面",
                    "bounds": {"x": 0, "y": 0, "width": 1470, "height": 956},
                },
                {
                    "role": "AXImage",
                    "tree_path": [1, 0, 0],
                    "label": "books的替身",
                    "url": "file:///books",
                    "supports": ["open", "show_menu"],
                    "bounds": {"x": 1372, "y": 41, "width": 64, "height": 64},
                    "ax_ref": {"fingerprint": "books"},
                },
                {
                    "role": "AXImage",
                    "tree_path": [1, 0, 1],
                    "label": "lyj的替身",
                    "url": "file:///lyj",
                    "supports": ["open", "show_menu"],
                    "bounds": {"x": 1372, "y": 153, "width": 64, "height": 64},
                    "ax_ref": {"fingerprint": "lyj"},
                },
            ],
            visited_count=5,
            capture_truncated=False,
        )

        result = cache.search(
            "finder",
            pid=643,
            query="AXWindow AXList AXOutline AXRow visible items",
            limit=10,
        )

        self.assertTrue(result["sufficient"])
        self.assertEqual(result["role_adaptation"], "finder_desktop_ax_shape")
        self.assertEqual(
            result["effective_requested_roles"],
            ["axscrollarea", "aximage"],
        )
        self.assertEqual(result["collection_label"], "桌面")
        self.assertTrue(result["collection_complete"])
        self.assertEqual(
            [
                item["label"]
                for item in result["elements"]
                if item.get("context_relation") == "collection_item"
            ],
            ["books的替身", "lyj的替身"],
        )

    def test_unlabelled_editors_use_role_focus_and_file_semantics(self) -> None:
        cache = AXSnapshotCache()
        cache.put(
            "qq",
            pid=777,
            elements=[
                {
                    "role": "AXTextArea",
                    "focused": True,
                    "supports": ["focus", "type_text"],
                    "bounds": {"x": 200, "y": 500, "width": 650, "height": 110},
                    "ax_ref": {"fingerprint": "chat-input"},
                },
                {
                    "role": "AXTextField",
                    "supports": ["focus", "type_text"],
                    "bounds": {"x": 20, "y": 40, "width": 162, "height": 20},
                    "ax_ref": {"fingerprint": "search-input"},
                },
                {
                    "role": "AXTextField",
                    "url": "file:///tmp/report.txt",
                    "supports": ["open"],
                    "bounds": {"x": 20, "y": 100, "width": 180, "height": 20},
                    "ax_ref": {"fingerprint": "file-name"},
                },
            ],
            visited_count=3,
            capture_truncated=False,
        )

        chat_input = cache.search(
            "qq",
            pid=777,
            query="定位消息输入框 AXTextArea AXTextField",
            limit=10,
        )
        search_input = cache.search(
            "qq",
            pid=777,
            query="定位搜索框 AXTextField",
            limit=10,
        )

        self.assertEqual(
            [item["ax_ref"]["fingerprint"] for item in chat_input["elements"]],
            ["chat-input"],
        )
        self.assertEqual(
            search_input["elements"][0]["ax_ref"]["fingerprint"],
            "search-input",
        )
        self.assertTrue(
            all(
                (
                    isinstance(item.get("ax_ref"), dict)
                    and item["ax_ref"]["fingerprint"] == "search-input"
                )
                or (
                    item.get("context_relation") == "current_state"
                    and "ax_ref" not in item
                )
                for item in search_input["elements"]
            )
        )

    def test_composite_row_inherits_descendant_label_and_suppresses_closed_menu(self) -> None:
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
        self.assertEqual(target["ax_ref"]["descendant_label"], "专辑")
        self.assertFalse(
            any(
                item.get("role") == "AXMenuItem" and item.get("label") == "专辑"
                for item in captured["elements"]
            )
        )

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

        changed_runtime = _CompositeMusicRuntime()
        changed_runtime.nodes[6]["AXValue"] = "歌曲"
        with self.assertRaisesRegex(
            RuntimeError,
            "no longer contains the reviewed descendant label",
        ):
            perform_macos_accessibility_action(
                "Music",
                target["ax_ref"],
                platform_name="darwin",
                runtime_factory=lambda: changed_runtime,
                cache=self.cache,
            )

    def test_semantic_action_retries_transient_post_focus_ax_tree(self) -> None:
        captured = capture_macos_accessibility(
            "Music",
            platform_name="darwin",
            runtime_factory=_CompositeMusicRuntime,
            cache=self.cache,
            query="专辑 AXRow 语义目标",
        )
        target = next(
            item
            for item in captured["elements"]
            if item.get("role") == "AXRow" and item.get("label") == "专辑"
        )
        runtime = _CompositeMusicRuntime()
        resolved_node = {
            "role": "AXRow",
            "subrole": "AXOutlineRow",
            "enabled": True,
            "actions": [],
            "bounds": {"x": 90, "y": 260, "width": 210, "height": 28},
        }
        sleeps: list[float] = []
        with mock.patch(
            "body.macos_accessibility._resolve_element",
            side_effect=[
                RuntimeError(
                    "The approved AX target is stale or no longer present "
                    "(visited=1, role_candidates=0)."
                ),
                (4, resolved_node),
            ],
        ) as resolver:
            action = perform_macos_accessibility_action(
                "Music",
                target["ax_ref"],
                platform_name="darwin",
                runtime_factory=lambda: runtime,
                cache=self.cache,
                sleeper=sleeps.append,
            )

        self.assertEqual(resolver.call_count, 2)
        self.assertEqual(sleeps, [0.12])
        self.assertEqual(action["ax_action"], "AXSelected")
        self.assertTrue(runtime.nodes[4]["AXSelected"])

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

    def test_music_grid_query_returns_all_logical_items_before_sidebar_noise(self) -> None:
        cache = AXSnapshotCache()
        albums = [
            "ONE OF US, Afterglow",
            "Seventh Heaven, Kalafina",
            "Eutopia / EMOTION / stars we chase - EP, Various Artists",
            "跡暖空, MyGO!!!!!",
            "Anfang, Roselia",
            "FIRE BIRD - Single, Roselia",
            "Ex-Otogibanashi - EP, ryo",
            "花の塔 - Single, Sayuri",
            "レイメイ - Single, Sayuri",
            *[f"测试专辑 {index:02d}" for index in range(10, 21)],
        ]
        elements = [
            {"role": "AXApplication", "tree_path": []},
            {"role": "AXOutline", "tree_path": [0], "label": "边栏"},
            {
                "role": "AXRow",
                "tree_path": [0, 0],
                "label": "专辑",
                "supports": ["select"],
                "ax_ref": {"fingerprint": "sidebar-albums"},
                "bounds": {"x": 76, "y": 356, "width": 200, "height": 32},
            },
            {
                "role": "AXScrollArea",
                "tree_path": [1],
                "bounds": {"x": 276, "y": 132, "width": 514, "height": 466},
            },
            {
                "role": "AXGrid",
                "tree_path": [1, 0],
                "label": "专辑",
                "bounds": {"x": 276, "y": 132, "width": 514, "height": 1336},
            },
        ]
        for index, label in enumerate(albums):
            elements.append(
                {
                    "role": "AXGroup",
                    "tree_path": [1, 0, index],
                    "label": label,
                    "description": label,
                    "supports": ["press", "show_menu"],
                    "ax_ref": {"fingerprint": f"album-{index}"},
                    "bounds": {
                        "x": 284 + (index % 3) * 158,
                        "y": 138 + (index // 3) * 212,
                        "width": 158,
                        "height": 212,
                    },
                }
            )
        cache.put(
            "music",
            pid=777,
            elements=elements,
            visited_count=len(elements),
            capture_truncated=False,
        )

        result = cache.search(
            "music",
            pid=777,
            query="请读取专辑网格中当前可见的所有专辑名称及其对应条目",
            limit=10,
        )

        self.assertEqual(result["collection_label"], "专辑")
        self.assertEqual(result["collection_item_total_count"], len(albums))
        self.assertEqual(result["collection_item_returned_count"], len(albums))
        self.assertTrue(result["collection_complete"])

        self.assertTrue(result["sufficient"])
        self.assertFalse(result["visual_fallback_required"])
        self.assertEqual(
            [item.get("label") for item in result["elements"]],
            ["专辑", *albums],
        )
        model_completeness_query = cache.search(
            "music",
            pid=777,
            query="专辑 AXList AXOutline collection_labels_complete",
            limit=16,
        )
        self.assertTrue(
            model_completeness_query["exhaustive_collection_query"]
        )
        self.assertEqual(
            model_completeness_query["collection_item_returned_count"],
            len(albums),
        )
        self.assertTrue(model_completeness_query["collection_complete"])
        self.assertTrue(
            model_completeness_query["collection_labels_complete"]
        )
        model_role_query = cache.search(
            "music",
            pid=777,
            query="专辑 AXCollection AXRow",
            limit=16,
        )
        self.assertFalse(model_role_query["exhaustive_collection_query"])
        self.assertEqual(
            model_role_query["collection_item_returned_count"],
            len(albums),
        )
        self.assertTrue(model_role_query["collection_complete"])
        self.assertTrue(model_role_query["collection_labels_complete"])
        virtual_collection = cache.search(
            "music",
            pid=777,
            query=(
                "当前窗口标题 AXWindow AXTitle "
                "专辑名称 AXCollection AXItem"
            ),
            limit=64,
        )
        self.assertTrue(virtual_collection["sufficient"])
        self.assertNotIn(
            "axtitle",
            virtual_collection["requested_roles"],
        )
        self.assertEqual(
            virtual_collection["role_adaptation"],
            "virtual_axcollection",
        )
        self.assertIn("axgrid", virtual_collection["effective_requested_roles"])
        self.assertEqual(
            virtual_collection["current_view_title"],
            "专辑",
        )
        self.assertEqual(virtual_collection["collection_label"], "专辑")
        self.assertEqual(
            virtual_collection["collection_item_total_count"],
            len(albums),
        )
        self.assertEqual(
            virtual_collection["collection_item_returned_count"],
            len(albums),
        )
        self.assertTrue(virtual_collection["collection_complete"])
        self.assertEqual(
            [item.get("label") for item in virtual_collection["elements"]],
            ["专辑", *albums],
        )
        album_item = next(
            item
            for item in result["elements"]
            if item.get("label") == albums[0]
        )
        self.assertNotIn("ax_ref", album_item)
        self.assertNotIn("supports", album_item)

        open_details = cache.search(
            "music",
            pid=777,
            query="打开 ONE OF US, Afterglow 专辑详情",
            limit=10,
        )
        self.assertFalse(open_details["sufficient"])
        self.assertEqual(
            open_details["insufficiency_reason"],
            "action_intent_mismatch",
        )
        self.assertEqual(open_details["actionable_match_count"], 0)
        self.assertLessEqual(open_details["returned_count"], 2)
        self.assertFalse(open_details["visual_fallback_required"])

        album_menu = cache.search(
            "music",
            pid=777,
            query="显示 ONE OF US, Afterglow 专辑的上下文菜单",
            limit=10,
        )
        menu_target = next(
            item
            for item in album_menu["elements"]
            if item.get("label") == albums[0]
        )
        self.assertTrue(album_menu["sufficient"])
        self.assertEqual(album_menu["actionable_match_count"], 1)
        self.assertEqual(menu_target["activation"], "show_menu")
        self.assertEqual(menu_target["activation_effect"], "context_menu")

        play_album = cache.search(
            "music",
            pid=777,
            query="播放 ONE OF US, Afterglow 专辑",
            limit=10,
        )
        self.assertTrue(play_album["sufficient"])
        playable = next(
            item
            for item in play_album["elements"]
            if item.get("label") == albums[0]
        )
        self.assertEqual(
            playable["activation_effect"],
            "media_playback",
        )
        self.assertIn("press", playable["supports"])

        for collection_noun in ("播放列表", "播放清单", "播放队列"):
            with self.subTest(collection_noun=collection_noun):
                view_playlist = cache.search(
                    "music",
                    pid=777,
                    query=f"查看{collection_noun}里的 ONE OF US, Afterglow 专辑",
                    limit=10,
                )
                self.assertTrue(view_playlist["sufficient"])
                viewed = next(
                    item
                    for item in view_playlist["elements"]
                    if item.get("label") == albums[0]
                )
                self.assertNotIn("ax_ref", viewed)
                self.assertNotIn("supports", viewed)

        for action in ("选择", "打开"):
            for collection_noun in ("播放列表", "播放清单", "播放队列"):
                query = (
                    f"{action}{collection_noun}中的 "
                    "ONE OF US, Afterglow 专辑"
                )
                with self.subTest(query=query):
                    guarded = cache.search(
                        "music",
                        pid=777,
                        query=query,
                        limit=10,
                    )
                    self.assertFalse(guarded["sufficient"])
                    self.assertEqual(
                        guarded["insufficiency_reason"],
                        "action_intent_mismatch",
                    )
                    self.assertTrue(
                        all("ax_ref" not in item for item in guarded["elements"])
                    )

    def test_declared_complete_collection_survives_unrelated_tree_timeout(self) -> None:
        cache = AXSnapshotCache()
        elements = [
            {"role": "AXApplication", "tree_path": []},
            {
                "role": "AXGrid",
                "tree_path": [0],
                "label": "专辑",
                "description": "3 items",
            },
            *[
                {
                    "role": "AXGroup",
                    "tree_path": [0, index],
                    "label": f"专辑 {index}",
                    "supports": ["press"],
                    "ax_ref": {"fingerprint": f"album-{index}"},
                }
                for index in range(3)
            ],
        ]
        cache.put(
            "music",
            pid=777,
            elements=elements,
            visited_count=len(elements),
            capture_truncated=True,
        )

        result = cache.search(
            "music",
            pid=777,
            query="请读取专辑网格中当前可见的所有专辑名称及其对应条目",
        )

        self.assertFalse(result["stale"])
        self.assertEqual(
            result["snapshot_stale_reason"],
            "capture_truncated",
        )
        self.assertTrue(result["sufficient"])
        self.assertTrue(result["complete_truncated_collection_read"])
        self.assertFalse(result["visual_fallback_required"])
        self.assertEqual(result["collection_declared_item_count"], 3)
        self.assertTrue(result["collection_complete"])
        self.assertTrue(
            all("ax_ref" not in item for item in result["elements"])
        )

    def test_captured_direct_collection_survives_unrelated_tree_timeout(self) -> None:
        cache = AXSnapshotCache()
        elements = [
            {"role": "AXApplication", "tree_path": []},
            {
                "role": "AXGrid",
                "tree_path": [0],
                "label": "专辑",
                "children_count": 3,
                "children_complete": True,
                "enqueued_child_count": 3,
            },
            *[
                {
                    "role": "AXGroup",
                    "tree_path": [0, index],
                    "label": f"Album {index}",
                    "supports": ["press"],
                    "ax_ref": {"fingerprint": f"album-{index}"},
                    "bounds": {
                        "x": 100 + index * 80,
                        "y": 100,
                        "width": 70,
                        "height": 70,
                    },
                }
                for index in range(3)
            ],
        ]
        cache.put(
            "music",
            pid=777,
            elements=elements,
            visited_count=len(elements),
            capture_truncated=True,
        )

        result = cache.search(
            "music",
            pid=777,
            query="请读取专辑网格中当前可见的所有专辑名称及其对应条目",
        )

        self.assertTrue(result["collection_branch_capture_complete"])
        self.assertTrue(result["complete_truncated_collection_read"])
        self.assertTrue(result["sufficient"])
        self.assertFalse(result["visual_fallback_required"])
        model_role_query = cache.search(
            "music",
            pid=777,
            query="专辑 AXCollection AXRow",
            limit=16,
        )
        self.assertFalse(model_role_query["exhaustive_collection_query"])
        self.assertTrue(model_role_query["collection_complete"])
        self.assertTrue(
            model_role_query["complete_truncated_collection_read"]
        )
        self.assertFalse(model_role_query["stale"])
        self.assertTrue(model_role_query["sufficient"])
        self.assertFalse(model_role_query["visual_fallback_required"])

    def test_complete_window_query_survives_unrelated_menu_timeout(self) -> None:
        cache = AXSnapshotCache()
        cache.put(
            "music",
            pid=777,
            elements=[
                {
                    "role": "AXApplication",
                    "tree_path": [],
                    "children_complete": True,
                    "enqueued_child_count": 1,
                },
                {
                    "role": "AXWindow",
                    "tree_path": [0],
                    "children_complete": True,
                    "enqueued_child_count": 1,
                },
                {
                    "role": "AXTextField",
                    "tree_path": [0, 0],
                    "label": "搜索",
                    "supports": ["focus", "type_text"],
                    "bounds": {
                        "x": 100,
                        "y": 100,
                        "width": 180,
                        "height": 28,
                    },
                    "ax_ref": {"fingerprint": "search-field"},
                    "children_complete": True,
                    "enqueued_child_count": 0,
                },
            ],
            visited_count=3,
            capture_truncated=True,
        )

        result = cache.search(
            "music",
            pid=777,
            query="搜索输入框 AXTextField",
        )

        self.assertTrue(result["window_branch_capture_complete"])
        self.assertTrue(result["complete_truncated_window_query"])
        self.assertFalse(result["stale"])
        self.assertTrue(result["sufficient"])
        self.assertEqual(result["actionable_match_count"], 1)
        self.assertTrue(
            any("ax_ref" in item for item in result["elements"])
        )

    def test_reviewed_non_chat_target_survives_unrelated_window_timeout(self) -> None:
        cache = AXSnapshotCache()
        cache.put(
            "music",
            pid=777,
            elements=[
                {
                    "role": "AXApplication",
                    "tree_path": [],
                    "children_complete": True,
                    "enqueued_child_count": 1,
                },
                {
                    "role": "AXWindow",
                    "tree_path": [0],
                    "children_complete": True,
                    "enqueued_child_count": 2,
                },
                {
                    "role": "AXTextField",
                    "tree_path": [0, 0],
                    "label": "搜索",
                    "supports": ["focus", "type_text"],
                    "bounds": {
                        "x": 100,
                        "y": 100,
                        "width": 180,
                        "height": 28,
                    },
                    "ax_ref": {"fingerprint": "search-field"},
                    "children_complete": True,
                    "enqueued_child_count": 0,
                },
            ],
            visited_count=3,
            capture_truncated=True,
        )

        result = cache.search(
            "music",
            pid=777,
            query="搜索输入框 AXTextField",
        )

        self.assertFalse(result["window_branch_capture_complete"])
        self.assertTrue(result["reviewed_target_path_complete"])
        self.assertTrue(result["complete_truncated_window_query"])
        self.assertFalse(result["stale"])
        self.assertTrue(result["sufficient"])

    def test_exhaustive_collection_keeps_64_long_labels_within_payload_budget(self) -> None:
        cache = AXSnapshotCache()
        albums = [
            f"{index:02d}-" + ("很长的专辑名称" * 14)
            for index in range(64)
        ]
        elements = [
            {"role": "AXApplication", "tree_path": []},
            {
                "role": "AXGrid",
                "tree_path": [0],
                "label": "专辑",
                "bounds": {"x": 276, "y": 132, "width": 514, "height": 466},
            },
        ]
        for index, label in enumerate(albums):
            elements.append(
                {
                    "role": "AXGroup",
                    "tree_path": [0, index],
                    "label": label,
                    "supports": ["press"],
                    "ax_ref": {"fingerprint": f"long-album-{index}"},
                    "bounds": {
                        "x": 284 + (index % 3) * 158,
                        "y": 138 + (index // 3) * 212,
                        "width": 158,
                        "height": 212,
                    },
                }
            )
        cache.put(
            "music",
            pid=777,
            elements=elements,
            visited_count=len(elements),
            capture_truncated=False,
        )

        result = cache.search(
            "music",
            pid=777,
            query="列出专辑网格中当前可见的所有专辑名称",
            limit=10,
        )

        self.assertEqual(result["collection_item_total_count"], 64)
        self.assertEqual(result["collection_item_returned_count"], 64)
        self.assertTrue(result["collection_complete"])
        self.assertTrue(result["sufficient"])
        self.assertLessEqual(result["returned_element_chars"], 14_000)
        self.assertEqual(
            [item.get("label") for item in result["elements"][1:]],
            albums,
        )
        self.assertTrue(result["collection_labels_complete"])
        self.assertEqual(result["collection_labels_truncated_count"], 0)

        oversized_albums = [
            f"{index:02d}-" + ("超" * 240)
            for index in range(64)
        ]
        for element, label in zip(elements[2:], oversized_albums):
            element["label"] = label
        cache.put(
            "music",
            pid=777,
            elements=elements,
            visited_count=len(elements),
            capture_truncated=False,
        )

        oversized = cache.search(
            "music",
            pid=777,
            query="列出专辑网格中当前可见的所有专辑名称",
            limit=10,
        )

        self.assertTrue(oversized["collection_complete"])
        self.assertFalse(oversized["collection_labels_complete"])
        self.assertGreater(oversized["collection_labels_truncated_count"], 0)
        self.assertFalse(oversized["sufficient"])
        self.assertEqual(
            oversized["insufficiency_reason"],
            "collection_labels_truncated",
        )

    def test_music_get_info_is_track_info_not_album_details(self) -> None:
        self.cache.put(
            "music",
            pid=777,
            elements=[
                {
                    "role": "AXMenu",
                    "tree_path": [0],
                    "label": "上下文菜单",
                },
                {
                    "role": "AXMenuItem",
                    "tree_path": [0, 0],
                    "label": "显示简介",
                    "supports": ["press"],
                    "ax_ref": {"fingerprint": "get-info"},
                },
            ],
            visited_count=2,
            capture_truncated=False,
        )

        track_info = self.cache.search(
            "music",
            pid=777,
            query="点击显示简介菜单项 AXMenuItem",
            limit=4,
        )
        target = next(
            item
            for item in track_info["elements"]
            if item.get("label") == "显示简介"
        )
        self.assertTrue(track_info["sufficient"])
        self.assertEqual(target["activation_effect"], "track_info_dialog")

        album_details = self.cache.search(
            "music",
            pid=777,
            query="点击显示简介打开专辑详情 AXMenuItem",
            limit=4,
        )
        self.assertFalse(album_details["sufficient"])
        self.assertEqual(
            album_details["insufficiency_reason"],
            "action_intent_mismatch",
        )
        self.assertTrue(
            all("ax_ref" not in item for item in album_details["elements"])
        )

    def test_qq_show_menu_only_row_becomes_verified_geometry_click(self) -> None:
        runtime = _QQWebConversationRuntime()
        captured = capture_macos_accessibility(
            "QQ",
            platform_name="darwin",
            runtime_factory=lambda: runtime,
            cache=self.cache,
            query="请定位会话列表中与测试联系人对应的可按下会话入口",
        )

        target = next(
            item
            for item in captured["elements"]
            if item.get("label") == "测试联系人"
            and item.get("context_relation") == "collection_item"
        )
        self.assertTrue(captured["search"]["sufficient"])
        self.assertEqual(target["label_source"], "descendant")
        self.assertIn("hit_test", target["supports"])
        self.assertEqual(target["ax_ref"]["activation"], "hit_test")
        self.assertEqual(
            target["ax_ref"]["descendant_label"],
            "测试联系人",
        )
        self.assertEqual(target["ax_ref"]["semantic_kind"], "chat_thread")

        typo = self.cache.search(
            "qq",
            pid=888,
            query="打开测试联系入会话",
            limit=10,
        )
        self.assertFalse(typo["sufficient"])
        self.assertEqual(
            typo["insufficiency_reason"],
            "near_match_requires_confirmation",
        )
        self.assertEqual(typo["near_match_labels"], ["测试联系人"])
        self.assertFalse(typo["visual_fallback_required"])

        natural_query = self.cache.search(
            "qq",
            pid=888,
            query="打开测试联系人会话",
            limit=10,
        )
        self.assertTrue(natural_query["sufficient"])
        self.assertEqual(natural_query["collection_label"], "会话列表")
        self.assertEqual(natural_query["actionable_match_count"], 1)
        self.assertLessEqual(natural_query["returned_count"], 6)
        self.assertIn(
            "hit_test",
            next(
                item
                for item in natural_query["elements"]
                if item.get("label") == "测试联系人"
            )["supports"],
        )

        clicks: list[tuple[int, int]] = []
        action_runtime = _QQWebConversationRuntime()
        action = perform_macos_accessibility_action(
            "QQ",
            target["ax_ref"],
            platform_name="darwin",
            runtime_factory=lambda: action_runtime,
            cache=self.cache,
            geometry_clicker=lambda x, y: clicks.append((x, y)),
        )

        self.assertEqual(clicks, [(356, 414)])
        self.assertEqual(action["ax_action"], "AXGeometryHitTest")
        self.assertEqual(
            action["method"],
            "macos_accessibility+core_graphics",
        )
        self.assertEqual(action_runtime.performed, [])

        already_active_runtime = _QQWebConversationRuntime()
        already_active_runtime.nodes[2]["children"] = [3, 6, 7, 8]
        already_active_runtime.nodes[7] = {
            "AXRole": "AXButton",
            "AXTitle": "测试联系人",
            "AXEnabled": True,
            "AXPosition": {"x": 560, "y": 120},
            "AXSize": {"width": 120, "height": 32},
            "actions": ["AXShowMenu"],
            "children": [],
        }
        already_active_runtime.nodes[8] = {
            "AXRole": "AXTextArea",
            "AXDescription": "消息输入框",
            "AXEnabled": True,
            "AXPosition": {"x": 520, "y": 610},
            "AXSize": {"width": 560, "height": 110},
            "actions": [],
            "children": [],
        }
        duplicate_clicks: list[tuple[int, int]] = []
        already_active = perform_macos_accessibility_action(
            "QQ",
            target["ax_ref"],
            platform_name="darwin",
            runtime_factory=lambda: already_active_runtime,
            cache=self.cache,
            geometry_clicker=lambda x, y: duplicate_clicks.append((x, y)),
        )

        self.assertEqual(duplicate_clicks, [])
        self.assertTrue(already_active["already_satisfied"])
        self.assertTrue(already_active["postcondition_verified"])
        self.assertFalse(already_active["clicked"])
        self.assertFalse(already_active["ax_action_performed"])
        self.assertEqual(already_active["ax_action"], "AXNoOpAlreadyActive")

        changed_runtime = _QQWebConversationRuntime()
        changed_runtime.nodes[5]["AXValue"] = "另一个联系人"
        changed_clicks: list[tuple[int, int]] = []
        with self.assertRaisesRegex(
            RuntimeError,
            "no longer contains the reviewed descendant label",
        ):
            perform_macos_accessibility_action(
                "QQ",
                target["ax_ref"],
                platform_name="darwin",
                runtime_factory=lambda: changed_runtime,
                cache=self.cache,
                geometry_clicker=lambda x, y: changed_clicks.append((x, y)),
            )
        self.assertEqual(changed_clicks, [])

    def test_qq_chat_row_prefers_verified_geometry_when_axpress_is_exposed(self) -> None:
        runtime = _QQWebConversationRuntime()
        runtime.nodes[4]["actions"] = [
            "AXPress",
            "AXShowMenu",
            "AXScrollToVisible",
        ]

        captured = capture_macos_accessibility(
            "QQ",
            platform_name="darwin",
            runtime_factory=lambda: runtime,
            cache=self.cache,
            query="打开测试联系人会话",
        )
        target = next(
            item
            for item in captured["elements"]
            if item.get("label") == "测试联系人"
            and item.get("context_relation") == "collection_item"
        )

        self.assertTrue(captured["search"]["sufficient"])
        self.assertEqual(target["activation"], "hit_test")
        self.assertEqual(
            target["ax_ref"]["semantic_kind"],
            "chat_thread",
        )
        self.assertIn("press", target["supports"])
        self.assertIn("hit_test", target["supports"])

    def test_near_match_scans_collection_items_beyond_result_limit(self) -> None:
        elements = [
            {
                "role": "AXGroup",
                "tree_path": [0],
                "label": "会话列表",
                "bounds": {"x": 20, "y": 80, "width": 280, "height": 700},
            }
        ]
        for index in range(30):
            label = "暮色星河" if index == 29 else f"普通联系人{index:02d}"
            elements.append(
                {
                    "role": "AXGroup",
                    "tree_path": [0, index],
                    "label": label,
                    "supports": ["hit_test"],
                    "bounds": {
                        "x": 20,
                        "y": 80 + index * 20,
                        "width": 280,
                        "height": 20,
                    },
                    "ax_ref": {"fingerprint": f"conversation-{index}"},
                }
            )
        self.cache.put(
            "qq",
            pid=888,
            elements=elements,
            visited_count=len(elements),
            capture_truncated=False,
        )

        result = self.cache.search(
            "qq",
            pid=888,
            query="打开暮色星何会话",
            limit=10,
        )

        self.assertNotIn(
            "暮色星河",
            [item.get("label") for item in result["elements"]],
        )
        self.assertEqual(result["near_match_labels"], ["暮色星河"])
        self.assertEqual(
            result["insufficiency_reason"],
            "near_match_requires_confirmation",
        )
        self.assertFalse(result["visual_fallback_required"])

    def test_action_query_prefers_named_collection_over_repeated_content_group(self) -> None:
        elements: list[dict[str, object]] = [
            {
                "role": "AXGroup",
                "tree_path": [0],
                "label": "会话列表",
                "bounds": {"x": 20, "y": 80, "width": 280, "height": 620},
            }
        ]
        for index in range(9):
            label = "目标联系人" if index == 3 else f"会话{index}"
            elements.append(
                {
                    "role": "AXGroup",
                    "tree_path": [0, index],
                    "label": label,
                    "supports": ["press"],
                    "bounds": {
                        "x": 20,
                        "y": 80 + index * 64,
                        "width": 280,
                        "height": 64,
                    },
                    "ax_ref": {"fingerprint": f"conversation-{index}"},
                }
            )
        elements.append(
            {
                "role": "AXGroup",
                "tree_path": [1],
                "label": "目标联系人",
                "supports": ["press"],
                "bounds": {"x": 320, "y": 80, "width": 700, "height": 500},
                "ax_ref": {"fingerprint": "message-pane"},
            }
        )
        for index in range(20):
            elements.append(
                {
                    "role": "AXGroup",
                    "tree_path": [1, index],
                    "label": f"消息{index}",
                    "supports": ["press"],
                    "bounds": {
                        "x": 340,
                        "y": 100 + index * 20,
                        "width": 640,
                        "height": 20,
                    },
                    "ax_ref": {"fingerprint": f"message-{index}"},
                }
            )
        self.cache.put(
            "qq",
            pid=888,
            elements=elements,
            visited_count=len(elements),
            capture_truncated=False,
        )

        result = self.cache.search(
            "qq",
            pid=888,
            query="打开目标联系人的会话",
            limit=10,
        )

        self.assertTrue(result["sufficient"])
        self.assertEqual(result["collection_label"], "会话列表")
        target = next(
            item
            for item in result["elements"]
            if item.get("context_relation") == "collection_item"
        )
        self.assertEqual(target["tree_path"], [0, 3])
        self.assertTrue(
            all(
                item.get("tree_path", [None])[0] == 0
                for item in result["elements"]
            )
        )

    def test_natural_select_synonym_uses_one_collection_item(self) -> None:
        self.cache.put(
            "finder",
            pid=700,
            elements=[
                {
                    "role": "AXList",
                    "tree_path": [0],
                    "label": "当前文件列表",
                    "bounds": {"x": 200, "y": 100, "width": 500, "height": 500},
                },
                {
                    "role": "AXGroup",
                    "tree_path": [0, 0],
                    "label": "目标文件",
                    "supports": ["hit_test"],
                    "bounds": {"x": 210, "y": 110, "width": 480, "height": 24},
                    "ax_ref": {"fingerprint": "target-file"},
                },
                {
                    "role": "AXGroup",
                    "tree_path": [0, 1],
                    "label": "其他文件",
                    "supports": ["hit_test"],
                    "bounds": {"x": 210, "y": 134, "width": 480, "height": 24},
                    "ax_ref": {"fingerprint": "other-file"},
                },
            ],
            visited_count=3,
            capture_truncated=False,
        )

        result = self.cache.search(
            "finder",
            pid=700,
            query="选中文件 目标文件",
            limit=10,
        )

        self.assertTrue(result["sufficient"])
        self.assertEqual(result["actionable_match_count"], 1)
        target = next(
            item
            for item in result["elements"]
            if item.get("context_relation") == "collection_item"
        )
        self.assertEqual(target["label"], "目标文件")

    def test_exact_collection_action_does_not_require_returning_every_item(self) -> None:
        self.cache.put(
            "finder",
            pid=700,
            elements=[
                {
                    "role": "AXList",
                    "tree_path": [0],
                    "label": "当前文件列表",
                    "bounds": {
                        "x": 200,
                        "y": 100,
                        "width": 500,
                        "height": 500,
                    },
                },
                {
                    "role": "AXGroup",
                    "tree_path": [0, 0],
                    "label": "目标文件",
                    "supports": ["hit_test"],
                    "bounds": {
                        "x": 210,
                        "y": 110,
                        "width": 480,
                        "height": 24,
                    },
                    "ax_ref": {"fingerprint": "target-file"},
                },
                {
                    "role": "AXGroup",
                    "tree_path": [0, 1],
                    "label": "其他文件",
                    "supports": ["hit_test"],
                    "bounds": {
                        "x": 210,
                        "y": 134,
                        "width": 480,
                        "height": 24,
                    },
                    "ax_ref": {"fingerprint": "other-file"},
                },
            ],
            visited_count=3,
            capture_truncated=False,
        )

        result = self.cache.search(
            "finder",
            pid=700,
            query="选择当前文件列表中的目标文件",
            limit=10,
        )

        self.assertFalse(result["exhaustive_collection_query"])
        self.assertFalse(result["collection_complete"])
        self.assertTrue(result["sufficient"])
        self.assertEqual(result["actionable_match_count"], 1)

    def test_finder_collection_does_not_inherit_first_file_or_sidebar_state(self) -> None:
        self.cache.put(
            "finder",
            pid=700,
            elements=[
                {
                    "role": "AXList",
                    "tree_path": [0],
                    "supports": ["select"],
                    "bounds": {"x": 200, "y": 100, "width": 500, "height": 500},
                    "ax_ref": {"fingerprint": "file-list"},
                },
                {
                    "role": "AXGroup",
                    "tree_path": [0, 0],
                    "label": "目标文件",
                    "supports": ["hit_test"],
                    "bounds": {"x": 210, "y": 110, "width": 480, "height": 24},
                    "ax_ref": {"fingerprint": "target-file"},
                },
                {
                    "role": "AXOutline",
                    "tree_path": [1],
                    "label": "边栏",
                    "bounds": {"x": 20, "y": 100, "width": 160, "height": 500},
                },
                {
                    "role": "AXRow",
                    "tree_path": [1, 0],
                    "label": "下载",
                    "selected": True,
                    "supports": ["select"],
                    "bounds": {"x": 30, "y": 130, "width": 140, "height": 24},
                    "ax_ref": {"fingerprint": "sidebar-downloads"},
                },
            ],
            visited_count=4,
            capture_truncated=False,
        )

        result = self.cache.search(
            "finder",
            pid=700,
            query="选中当前文件列表中的目标文件",
            limit=10,
        )

        self.assertTrue(result["sufficient"])
        self.assertEqual(result["collection_label"], "当前文件列表")
        anchor = next(
            item
            for item in result["elements"]
            if item.get("context_relation") == "collection_anchor"
        )
        self.assertNotIn("ax_ref", anchor)
        self.assertFalse(
            any(
                item.get("label") == "下载"
                or item.get("context_relation") == "current_state"
                for item in result["elements"]
            )
        )

    def test_finder_view_mode_label_is_the_current_file_collection(self) -> None:
        self.cache.put(
            "finder",
            pid=700,
            elements=[
                {
                    "role": "AXList",
                    "tree_path": [0],
                    "label": "图标视图",
                    "bounds": {
                        "x": 200,
                        "y": 100,
                        "width": 500,
                        "height": 500,
                    },
                },
                {
                    "role": "AXGroup",
                    "tree_path": [0, 0],
                    "label": "example.txt",
                    "bounds": {
                        "x": 210,
                        "y": 110,
                        "width": 80,
                        "height": 80,
                    },
                },
            ],
            visited_count=2,
            capture_truncated=False,
        )

        result = self.cache.search(
            "finder",
            pid=700,
            query="请读取当前访达窗口中的所有文件和文件夹名称",
        )

        self.assertEqual(result["collection_label"], "当前文件列表")
        self.assertTrue(result["collection_complete"])
        self.assertTrue(result["sufficient"])

    def test_finder_geometry_selection_binds_descendant_file_url(self) -> None:
        cache = AXSnapshotCache()
        cache.put(
            "finder",
            pid=700,
            elements=[
                {
                    "role": "AXList",
                    "tree_path": [0],
                    "label": "当前文件列表",
                    "bounds": {"x": 200, "y": 100, "width": 500, "height": 500},
                },
                {
                    "role": "AXGroup",
                    "tree_path": [0, 0],
                    "bounds": {"x": 210, "y": 110, "width": 480, "height": 24},
                },
                {
                    "role": "AXTextField",
                    "tree_path": [0, 0, 0],
                    "label": "same.txt",
                    "url": "file:///A/same.txt",
                    "bounds": {"x": 220, "y": 112, "width": 120, "height": 20},
                },
            ],
            visited_count=3,
            capture_truncated=False,
        )

        result = cache.search(
            "finder",
            pid=700,
            query="选择当前文件列表中的 same.txt",
            limit=10,
        )
        target = next(
            item
            for item in result["elements"]
            if item.get("context_relation") == "collection_item"
        )
        self.assertEqual(
            target["ax_ref"]["descendant_url"],
            "file:///A/same.txt",
        )

        runtime = _FakeAXRuntime()
        runtime.nodes = {
            2: {
                "AXRole": "AXGroup",
                "AXEnabled": True,
                "children": [3],
            },
            3: {
                "AXRole": "AXTextField",
                "AXTitle": "same.txt",
                "AXURL": "file:///B/same.txt",
                "children": [],
            },
        }
        with self.assertRaisesRegex(RuntimeError, "file URL"):
            _hit_test_point(
                runtime,
                2,
                {
                    "role": "AXGroup",
                    "enabled": True,
                    "bounds": {
                        "x": 210,
                        "y": 110,
                        "width": 480,
                        "height": 24,
                    },
                },
                target["ax_ref"],
            )

    def test_finder_disabled_icon_group_selects_enabled_image_without_opening(self) -> None:
        cache = AXSnapshotCache()
        cache.put(
            "finder",
            pid=700,
            elements=[
                {
                    "role": "AXList",
                    "tree_path": [0],
                    "label": "图标视图",
                    "bounds": {
                        "x": 200,
                        "y": 100,
                        "width": 500,
                        "height": 500,
                    },
                },
                {
                    "role": "AXGroup",
                    "tree_path": [0, 0],
                    "enabled": False,
                    "bounds": {
                        "x": 210,
                        "y": 110,
                        "width": 64,
                        "height": 64,
                    },
                },
                {
                    "role": "AXImage",
                    "tree_path": [0, 0, 0],
                    "label": "example.txt",
                    "supports": ["open", "show_menu"],
                    "bounds": {
                        "x": 210,
                        "y": 110,
                        "width": 64,
                        "height": 64,
                    },
                    "ax_ref": {
                        "app_id": "finder",
                        "role": "AXImage",
                        "path": [0, 0, 0],
                        "bounds": {
                            "x": 210,
                            "y": 110,
                            "width": 64,
                            "height": 64,
                        },
                        "fingerprint": "copied",
                    },
                },
            ],
            visited_count=3,
            capture_truncated=False,
        )

        result = cache.search(
            "finder",
            pid=700,
            query="选择当前文件列表中的 example.txt",
        )
        target = next(
            item
            for item in result["elements"]
            if item.get("context_relation") == "collection_item"
        )

        self.assertTrue(result["sufficient"])
        self.assertEqual(target["role"], "AXImage")
        self.assertEqual(target["activation"], "hit_test")
        self.assertEqual(target["supports"], ["hit_test"])
        self.assertNotIn("open", target["supports"])
        self.assertEqual(target["ax_ref"]["activation"], "hit_test")

    def test_hit_test_accepts_reviewed_file_url_on_target_itself(self) -> None:
        runtime = _FakeAXRuntime()
        runtime.nodes = {
            2: {
                "AXRole": "AXImage",
                "AXTitle": "example.txt",
                "AXURL": "file:///A/example.txt",
                "AXEnabled": True,
                "AXPosition": {"x": 210, "y": 110},
                "AXSize": {"width": 64, "height": 64},
                "children": [],
            },
        }

        point = _hit_test_point(
            runtime,
            2,
            {
                "role": "AXImage",
                "label": "example.txt",
                "url": "file:///A/example.txt",
                "enabled": True,
                "bounds": {
                    "x": 210,
                    "y": 110,
                    "width": 64,
                    "height": 64,
                },
            },
            {
                "descendant_label": "example.txt",
                "descendant_url": "file:///A/example.txt",
            },
        )

        self.assertEqual(point, (242, 142))

    def test_ambiguous_action_query_exposes_no_executable_reference(self) -> None:
        self.cache.put(
            "qq",
            pid=888,
            elements=[
                {
                    "role": "AXGroup",
                    "tree_path": [0],
                    "label": "暮色星河",
                    "supports": ["show_menu"],
                    "bounds": {"x": 20, "y": 80, "width": 280, "height": 64},
                    "ax_ref": {"fingerprint": "conversation"},
                },
                {
                    "role": "AXButton",
                    "tree_path": [1],
                    "label": "暮色星河",
                    "supports": ["press", "show_menu"],
                    "bounds": {"x": 320, "y": 80, "width": 120, "height": 32},
                    "ax_ref": {"fingerprint": "header"},
                },
            ],
            visited_count=2,
            capture_truncated=False,
        )

        result = self.cache.search(
            "qq",
            pid=888,
            query="打开暮色星河的上下文菜单",
            limit=10,
        )

        self.assertFalse(result["sufficient"])
        self.assertEqual(
            result["insufficiency_reason"],
            "ambiguous_actionable_matches",
        )
        self.assertGreater(result["actionable_match_count"], 1)
        self.assertFalse(result["visual_fallback_required"])
        self.assertTrue(result["elements"])
        self.assertTrue(
            all("ax_ref" not in item for item in result["elements"])
        )

    def test_ax_action_names_are_not_roles_and_exact_target_beats_shared_suffix(self) -> None:
        self.cache.put(
            "finder",
            pid=700,
            elements=[
                {
                    "role": "AXImage",
                    "tree_path": [0],
                    "label": "books的替身",
                    "supports": ["open", "show_menu"],
                    "bounds": {"x": 400, "y": 80, "width": 64, "height": 64},
                    "ax_ref": {"fingerprint": "books-alias"},
                },
                {
                    "role": "AXImage",
                    "tree_path": [1],
                    "label": "lyj的替身",
                    "supports": ["open", "show_menu"],
                    "bounds": {"x": 400, "y": 160, "width": 64, "height": 64},
                    "ax_ref": {"fingerprint": "lyj-alias"},
                },
            ],
            visited_count=2,
            capture_truncated=False,
        )

        result = self.cache.search(
            "finder",
            pid=700,
            query="打开 books的替身 AXImage AXOpen",
            limit=10,
        )

        self.assertEqual(result["requested_roles"], ["aximage"])
        self.assertTrue(result["sufficient"])
        self.assertEqual(result["actionable_match_count"], 1)
        executable = [
            item
            for item in result["elements"]
            if isinstance(item.get("ax_ref"), dict)
        ]
        self.assertEqual(
            [item.get("label") for item in executable],
            ["books的替身"],
        )

    def test_context_only_elements_do_not_inherit_requested_menu_action(self) -> None:
        self.cache.put(
            "qq",
            pid=888,
            elements=[
                {
                    "role": "AXGroup",
                    "tree_path": [0],
                    "label": "会话列表",
                    "supports": ["show_menu"],
                    "bounds": {"x": 20, "y": 80, "width": 280, "height": 620},
                    "ax_ref": {"fingerprint": "conversation-list"},
                },
                {
                    "role": "AXGroup",
                    "tree_path": [0, 0],
                    "label": "目标联系人",
                    "supports": ["show_menu"],
                    "bounds": {"x": 20, "y": 80, "width": 280, "height": 64},
                    "ax_ref": {"fingerprint": "conversation"},
                },
                {
                    "role": "AXTextArea",
                    "tree_path": [1],
                    "focused": True,
                    "supports": ["focus", "type_text", "show_menu"],
                    "bounds": {"x": 320, "y": 600, "width": 700, "height": 120},
                    "ax_ref": {"fingerprint": "chat-input"},
                },
            ],
            visited_count=3,
            capture_truncated=False,
        )

        result = self.cache.search(
            "qq",
            pid=888,
            query="打开会话列表中目标联系人的上下文菜单",
            limit=10,
        )

        self.assertTrue(result["sufficient"])
        target = next(
            item
            for item in result["elements"]
            if item.get("context_relation") == "collection_item"
        )
        anchor = next(
            item
            for item in result["elements"]
            if item.get("context_relation") == "collection_anchor"
        )
        current = next(
            item
            for item in result["elements"]
            if item.get("context_relation") == "current_state"
        )
        self.assertEqual(target["activation"], "show_menu")
        self.assertNotIn("activation", anchor)
        self.assertNotIn("activation", current)

    def test_active_chat_identity_verification_requires_visual_confirmation(self) -> None:
        self.cache.put(
            "qq",
            pid=888,
            elements=[
                {
                    "role": "AXGroup",
                    "tree_path": [0],
                    "label": "会话列表",
                    "bounds": {"x": 20, "y": 80, "width": 280, "height": 620},
                },
                {
                    "role": "AXGroup",
                    "tree_path": [0, 0],
                    "label": "目标联系人",
                    "supports": ["hit_test"],
                    "bounds": {"x": 20, "y": 80, "width": 280, "height": 64},
                    "ax_ref": {"fingerprint": "conversation"},
                },
                {
                    "role": "AXTextArea",
                    "tree_path": [1],
                    "focused": True,
                    "value": "测试草稿",
                    "supports": ["focus", "type_text"],
                    "bounds": {"x": 320, "y": 600, "width": 700, "height": 120},
                    "ax_ref": {"fingerprint": "chat-input"},
                },
            ],
            visited_count=3,
            capture_truncated=False,
        )

        result = self.cache.search(
            "qq",
            pid=888,
            query=(
                "当前是否在目标联系人/会话里，"
                "聊天记录底部是否出现测试草稿，输入框是否可用"
            ),
            limit=10,
        )

        self.assertFalse(result["sufficient"])
        self.assertEqual(
            result["insufficiency_reason"],
            "active_chat_identity_unverified",
        )
        self.assertTrue(result["visual_fallback_required"])
        self.assertTrue(result["elements"])
        self.assertTrue(
            all("ax_ref" not in item for item in result["elements"])
        )

    def test_active_chat_identity_uses_same_window_header_and_editor(self) -> None:
        self.cache.put(
            "qq",
            pid=888,
            elements=[
                {
                    "role": "AXWindow",
                    "tree_path": [0],
                    "label": "QQ",
                    "bounds": {
                        "x": 100,
                        "y": 80,
                        "width": 900,
                        "height": 700,
                    },
                },
                {
                    "role": "AXGroup",
                    "tree_path": [0, 0],
                    "bounds": {
                        "x": 320,
                        "y": 100,
                        "width": 660,
                        "height": 620,
                    },
                },
                {
                    "role": "AXButton",
                    "tree_path": [0, 0, 0],
                    "label": "目标联系人",
                    "bounds": {
                        "x": 360,
                        "y": 112,
                        "width": 120,
                        "height": 24,
                    },
                },
                {
                    "role": "AXStaticText",
                    "tree_path": [0, 0, 1],
                    "label": "最后一条可见消息",
                    "bounds": {
                        "x": 420,
                        "y": 510,
                        "width": 180,
                        "height": 20,
                    },
                },
                {
                    "role": "AXTextArea",
                    "tree_path": [0, 0, 2],
                    "description": "目标联系人",
                    "focused": True,
                    "supports": ["focus", "type_text"],
                    "bounds": {
                        "x": 360,
                        "y": 600,
                        "width": 580,
                        "height": 100,
                    },
                    "ax_ref": {
                        "app_id": "qq",
                        "role": "AXTextArea",
                        "path": [0, 0, 2],
                        "fingerprint": "chat-input",
                    },
                },
            ],
            visited_count=5,
            capture_truncated=False,
        )

        result = self.cache.search(
            "qq",
            pid=888,
            query=(
                "当前会话名称 AXWindow AXRow，"
                "聊天输入框 AXTextArea type_text，最后一条可见消息"
            ),
            limit=10,
        )

        self.assertTrue(result["sufficient"])
        self.assertTrue(result["active_chat_identity_verified"])
        self.assertEqual(result["active_chat_label"], "目标联系人")
        self.assertTrue(result["active_chat_input_focused"])
        evidence = [
            item
            for item in result["elements"]
            if item.get("context_relation") == "verification_evidence"
        ]
        self.assertEqual(
            {item.get("role") for item in evidence},
            {"AXButton", "AXStaticText", "AXTextArea"},
        )
        self.assertTrue(all("ax_ref" not in item for item in evidence))

    def test_collection_branch_label_index_reads_large_tree_linearly(self) -> None:
        root_count = 500
        elements: list[dict[str, object]] = []
        root_indices: list[int] = []
        for index in range(root_count):
            root_indices.append(len(elements))
            elements.append(
                {
                    "role": "AXGroup",
                    "tree_path": [0, index],
                }
            )
            elements.append(
                {
                    "role": "AXStaticText",
                    "tree_path": [0, index, 0],
                    "label": f"项目{index:03d}",
                }
            )

        with mock.patch(
            "body.macos_accessibility_cache._element_label",
            wraps=_element_label,
        ) as label_reader:
            labels = _collection_branch_labels(
                elements,
                root_indices,
                container_path=(0,),
            )

        self.assertEqual(len(labels), root_count)
        self.assertEqual(labels[root_indices[-1]], "项目499")
        self.assertLessEqual(
            label_reader.call_count,
            len(elements) + len(root_indices) + 5,
        )

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

    def test_manual_refresh_reports_partial_when_only_some_apps_update(self) -> None:
        cache = AXSnapshotCache()

        def running_apps_provider(**_kwargs):
            return (
                [
                    {"app": "Safari", "bundle_id": "com.apple.Safari", "pid": "777"},
                    {"app": "Notes", "bundle_id": "com.apple.Notes", "pid": "778"},
                ],
                [],
            )

        class PartialRuntime(_FakeAXRuntime):
            def application(self, pid: int) -> int:
                return 1 if pid == 777 else 0

        result = refresh_macos_accessibility_index(
            platform_name="darwin",
            runner=lambda *_args, **_kwargs: None,
            runtime_factory=PartialRuntime,
            cache=cache,
            running_apps_provider=running_apps_provider,
        )

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["updated_count"], 1)
        self.assertEqual(result["skipped_count"], 1)

    def test_all_app_refresh_excludes_host_process_tree_before_any_ax_call(self) -> None:
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

        with mock.patch(
            "body.macos_accessibility._host_process_tree_pids",
            side_effect=({777}, {777, 779}),
        ) as process_tree:
            result = refresh_macos_accessibility_index(
                platform_name="darwin",
                runner=lambda *_args, **_kwargs: None,
                runtime_factory=RecordingRuntime,
                cache=cache,
                running_apps_provider=lambda **_kwargs: (
                    [
                        {"app": "Ipet", "bundle_id": "com.example.Ipet", "pid": "777"},
                        {
                            "app": "QtWebEngineProcess",
                            "bundle_id": "",
                            "pid": "779",
                        },
                        {"app": "Notes", "bundle_id": "com.apple.Notes", "pid": "778"},
                    ],
                    [],
                ),
                current_pid_provider=lambda: 777,
            )

        self.assertEqual(process_tree.call_count, 2)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["discovered_count"], 1)
        self.assertEqual(result["updated_count"], 1)
        self.assertEqual(result["self_excluded_count"], 2)
        self.assertEqual(
            {
                (item["name"], item["pid"])
                for item in result["self_excluded_apps"]
            },
            {("Ipet", 777), ("QtWebEngineProcess", 779)},
        )
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

    def test_all_app_refresh_preserves_snapshots_when_discovery_fails(self) -> None:
        cache = AXSnapshotCache()
        for app_id in ("qq", "wechat", "music", "finder"):
            cache.put(
                app_id,
                app_name=app_id,
                pid=700,
                elements=[
                    {
                        "role": "AXButton",
                        "label": f"{app_id} cached control",
                    }
                ],
                visited_count=1,
                capture_truncated=False,
            )

        result = refresh_macos_accessibility_index(
            platform_name="darwin",
            runner=lambda *_args, **_kwargs: None,
            runtime_factory=_FakeAXRuntime,
            cache=cache,
            running_apps_provider=lambda **_kwargs: (
                [],
                ["transient application discovery failure"],
            ),
        )

        self.assertEqual(result["status"], "error")
        self.assertFalse(result["discovery_authoritative"])
        self.assertEqual(result["skipped_count"], 4)
        self.assertTrue(
            all(item["status"] == "unverified" for item in result["updates"])
        )
        for app_id in ("qq", "wechat", "music", "finder"):
            stored = cache.search(
                app_id,
                pid=700,
                query=f"{app_id} cached control",
            )
            self.assertFalse(stored["stale"])
            self.assertEqual(stored["stale_reason"], "")

    def test_background_refresh_does_not_replace_windowed_tree_with_menu_only_tree(self) -> None:
        cache = AXSnapshotCache()
        initial = capture_macos_accessibility(
            "QQ",
            platform_name="darwin",
            runtime_factory=_LargeQQRuntime,
            cache=cache,
            query="发送",
        )

        result = refresh_macos_accessibility_index(
            platform_name="darwin",
            runner=lambda *_args, **_kwargs: None,
            runtime_factory=_MenuOnlyQQRuntime,
            cache=cache,
            running_apps_provider=lambda **_kwargs: (
                [
                    {
                        "app": "QQ",
                        "bundle_id": "com.tencent.qq",
                        "pid": "888",
                    }
                ],
                [],
            ),
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["updated_count"], 0)
        self.assertTrue(result["updates"][0]["snapshot_preserved"])
        stored = cache.search("qq", pid=888, query="发送")
        self.assertEqual(stored["snapshot_id"], initial["snapshot_id"])
        self.assertGreater(stored["index"]["roles"].get("AXWindow", 0), 0)

    def test_background_refresh_does_not_replace_main_tree_with_auxiliary_window(self) -> None:
        class AuxiliaryWindowRuntime(_FakeAXRuntime):
            def __init__(self) -> None:
                super().__init__()
                self.nodes = {
                    1: {
                        "AXRole": "AXApplication",
                        "AXTitle": "QQ",
                        "children": [2, 3],
                    },
                    2: {
                        "AXRole": "AXWindow",
                        "AXTitle": "语音通话",
                        "children": [4],
                    },
                    3: {
                        "AXRole": "AXMenuBar",
                        "children": [5],
                    },
                    4: {
                        "AXRole": "AXButton",
                        "AXTitle": "关闭麦克风",
                        "children": [],
                    },
                    5: {
                        "AXRole": "AXMenuItem",
                        "AXTitle": "窗口",
                        "children": [],
                    },
                }

            def focused_application(self) -> tuple[int, str]:
                return 888, "QQ"

            def application(self, pid: int) -> int:
                return 1 if pid == 888 else 0

        cache = AXSnapshotCache()
        initial = capture_macos_accessibility(
            "QQ",
            platform_name="darwin",
            runtime_factory=_LargeQQRuntime,
            cache=cache,
            query="发送",
        )

        result = refresh_macos_accessibility_index(
            platform_name="darwin",
            runner=lambda *_args, **_kwargs: None,
            runtime_factory=AuxiliaryWindowRuntime,
            cache=cache,
            running_apps_provider=lambda **_kwargs: (
                [
                    {
                        "app": "QQ",
                        "bundle_id": "com.tencent.qq",
                        "pid": "888",
                    }
                ],
                [],
            ),
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["updated_count"], 0)
        self.assertTrue(result["updates"][0]["snapshot_preserved"])
        stored = cache.search("qq", pid=888, query="发送")
        self.assertEqual(stored["snapshot_id"], initial["snapshot_id"])
        self.assertGreater(stored["total_element_count"], 400)

    def test_background_refresh_preserves_main_tree_against_larger_rich_auxiliary_window(self) -> None:
        class RichAuxiliaryWindowRuntime(_FakeAXRuntime):
            def __init__(self) -> None:
                super().__init__()
                self.nodes = {
                    1: {
                        "AXRole": "AXApplication",
                        "AXTitle": "QQ",
                        "children": [2],
                    },
                    2: {
                        "AXRole": "AXWindow",
                        "AXTitle": "语音通话",
                        "AXPosition": {"x": 200, "y": 120},
                        "AXSize": {"width": 720, "height": 520},
                        "children": list(range(3, 523)),
                    },
                }
                for element_id in range(3, 523):
                    self.nodes[element_id] = {
                        "AXRole": "AXRow",
                        "AXTitle": f"通话成员 {element_id}",
                        "children": [],
                    }

            def focused_application(self) -> tuple[int, str]:
                return 888, "QQ"

            def application(self, pid: int) -> int:
                return 1 if pid == 888 else 0

        cache = AXSnapshotCache()
        initial = capture_macos_accessibility(
            "QQ",
            platform_name="darwin",
            runtime_factory=_LargeQQRuntime,
            cache=cache,
            query="发送",
        )

        result = refresh_macos_accessibility_index(
            platform_name="darwin",
            runner=lambda *_args, **_kwargs: None,
            runtime_factory=RichAuxiliaryWindowRuntime,
            cache=cache,
            running_apps_provider=lambda **_kwargs: (
                [
                    {
                        "app": "QQ",
                        "bundle_id": "com.tencent.qq",
                        "pid": "888",
                    }
                ],
                [],
            ),
        )

        self.assertEqual(result["updated_count"], 0)
        self.assertTrue(result["updates"][0]["snapshot_preserved"])
        stored = cache.search("qq", pid=888, query="发送")
        self.assertEqual(stored["snapshot_id"], initial["snapshot_id"])
        self.assertGreater(stored["total_element_count"], 400)

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
        self.assertEqual(result["status"], "error")
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
