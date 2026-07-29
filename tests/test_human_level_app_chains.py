from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend import computer_use_context as context_helpers
from body.macos_accessibility import (
    _query_requires_live_state,
    _read_node,
    resolve_macos_ax_app,
    verify_macos_accessibility_chat_context,
)
from body.macos_accessibility_cache import (
    AX_COLLECTION_READ_MAX_ITEMS,
    AXSnapshotCache,
)
from brain.decisions import BrainDecision
from human_ops.approval_flow import observation_confirms_expected_draft
from human_ops.approvals import ReviewableProposal
from human_ops.proposals import with_inherited_enter_expected_text


def _ref(
    app_id: str,
    role: str,
    path: list[int],
    label: str,
    *,
    activation: str = "",
) -> dict[str, object]:
    result: dict[str, object] = {
        "app_id": app_id,
        "role": role,
        "path": list(path),
        "title": label,
        "fingerprint": f"{app_id}:{role}:{'.'.join(map(str, path))}:{label}",
    }
    if activation:
        result["activation"] = activation
    return result


def _ax_context(
    app_id: str,
    app_name: str,
    elements: list[dict[str, object]],
    *,
    query: str,
    sufficient: bool = True,
    stale: bool = False,
) -> dict[str, object]:
    return context_helpers._infer_computer_use_context(
        "AX 读取完成",
        {
            "accessibility": {
                "usable": True,
                "app": {
                    "app_id": app_id,
                    "name": app_name,
                    "surface": f"{app_id}_gui",
                },
                "elements": elements,
                "search": {
                    "query": query,
                    "sufficient": sufficient,
                    "stale": stale,
                    "actionable_match_count": 1,
                    "visible_match_count": 1,
                },
            }
        },
        query,
    )


def _windowed_collection(
    app_id: str,
    *,
    collection_label: str,
    labels_by_window: list[list[str]],
    focused_window: int | None = None,
) -> list[dict[str, object]]:
    elements: list[dict[str, object]] = []
    for window_index, labels in enumerate(labels_by_window):
        origin_x = 80 + window_index * 720
        elements.extend(
            [
                {
                    "role": "AXWindow",
                    "tree_path": [window_index],
                    "label": f"{app_id}-window-{window_index}",
                    "focused": window_index == focused_window,
                    "bounds": {
                        "x": origin_x,
                        "y": 60,
                        "width": 640,
                        "height": 720,
                    },
                },
                {
                    "role": "AXList",
                    "tree_path": [window_index, 0],
                    "label": collection_label,
                    "bounds": {
                        "x": origin_x + 10,
                        "y": 110,
                        "width": 260,
                        "height": 560,
                    },
                },
            ]
        )
        for row_index, label in enumerate(labels):
            path = [window_index, 0, row_index]
            elements.append(
                {
                    "role": "AXRow",
                    "tree_path": path,
                    "label": label,
                    "bounds": {
                        "x": origin_x + 20,
                        "y": 130 + row_index * 48,
                        "width": 230,
                        "height": 42,
                    },
                    "supports": ["hit_test"],
                    "ax_ref": _ref(app_id, "AXRow", path, label),
                }
            )
    return elements


def _collection(
    app_id: str,
    *,
    label: str,
    item_count: int,
    logical_total: int | None = None,
    long_labels: bool = False,
) -> list[dict[str, object]]:
    collection: dict[str, object] = {
        "role": "AXGrid" if app_id == "music" else "AXList",
        "tree_path": [0, 0],
        "label": label,
        "bounds": {"x": 100, "y": 100, "width": 800, "height": 600},
    }
    if logical_total is not None:
        collection["value"] = f"{logical_total} items"
        collection["description"] = f"{logical_total} total items"
    elements: list[dict[str, object]] = [
        {
            "role": "AXWindow",
            "tree_path": [0],
            "label": app_id,
            "bounds": {"x": 40, "y": 40, "width": 1000, "height": 760},
        },
        collection,
    ]
    for index in range(item_count):
        item_label = (
            f"Item {index:03d} " + ("很长的名称" * 45)
            if long_labels
            else f"Item {index:03d}"
        )
        elements.append(
            {
                "role": "AXRow",
                "tree_path": [0, 0, index],
                "label": item_label,
                "bounds": {
                    "x": 120,
                    "y": 120 + index * 18,
                    "width": 300,
                    "height": 18,
                },
                "supports": ["select"],
            }
        )
    return elements


class _ChatRuntime:
    def __init__(self, app_name: str, *, draft: str, header: str = "Alice") -> None:
        self.app_name = app_name
        self.nodes = {
            1: {
                "AXRole": "AXApplication",
                "AXTitle": app_name,
                "children": [2],
            },
            2: {
                "AXRole": "AXWindow",
                "AXTitle": app_name,
                "AXPosition": {"x": 100, "y": 80},
                "AXSize": {"width": 900, "height": 700},
                "children": [3, 4],
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
        }

    def trusted(self) -> bool:
        return True

    def focused_application(self) -> tuple[int, str]:
        return 888, self.app_name

    def application(self, pid: int) -> int:
        return 1 if pid == 888 else 0

    def set_timeout(self, _element: int, _seconds: float) -> None:
        return None

    def retain(self, value: int) -> int:
        return value

    def release(self, _value: int) -> None:
        return None

    def close(self) -> None:
        return None

    def attribute(self, element: int, name: str):
        return self.nodes[element].get(name)

    def attributes(
        self,
        element: int,
        names: tuple[str, ...],
    ) -> dict[str, object]:
        return {name: self.nodes[element].get(name) for name in names}

    def children(self, element: int, *, limit: int) -> tuple[list[int], bool]:
        children = list(self.nodes[element].get("children") or [])
        return children[:limit], len(children) > limit

    def actions(self, _element: int) -> list[str]:
        return []


class LoginAndFallbackChainTests(unittest.TestCase):
    def test_qq_ax_login_surface_does_not_expose_chat_actions(self) -> None:
        context = _ax_context(
            "qq",
            "QQ",
            [
                {
                    "role": "AXButton",
                    "label": "账密登录",
                    "supports": ["press"],
                    "ax_ref": _ref("qq", "AXButton", [0, 0], "账密登录"),
                }
            ],
            query="Alice 会话 消息输入框",
            sufficient=False,
        )

        self.assertFalse(
            {"chat_thread", "chat_input", "search_field"}
            & {item["kind"] for item in context["affordances"]}
        )

    def test_wechat_ax_entry_surface_does_not_expose_chat_actions(self) -> None:
        context = _ax_context(
            "wechat",
            "微信",
            [
                {
                    "role": "AXButton",
                    "label": "进入微信",
                    "supports": ["press"],
                    "ax_ref": _ref(
                        "wechat",
                        "AXButton",
                        [0, 0],
                        "进入微信",
                    ),
                }
            ],
            query="Alice 会话 消息输入框",
            sufficient=False,
        )

        self.assertFalse(
            {"chat_thread", "chat_input", "search_field"}
            & {item["kind"] for item in context["affordances"]}
        )

    def test_qq_visual_login_negatives_cannot_become_chat_affordances(self) -> None:
        context = context_helpers._infer_computer_use_context(
            (
                "QQ 登录窗口中只有自动登录、账密登录和网络连接按钮，"
                "未显示会话列表、消息输入框或发送按钮。"
            ),
            {},
            "打开 QQ，进入 Alice 会话，定位消息输入框并发送消息",
        )

        self.assertFalse(
            any(
                item.get("kind")
                in {"chat_thread", "chat_input", "input", "search_field"}
                or item.get("label") == "发送"
                for item in context["affordances"]
            )
        )

    def test_wechat_visual_entry_negatives_cannot_become_chat_affordances(self) -> None:
        context = context_helpers._infer_computer_use_context(
            (
                "微信进入页面只有进入微信、切换账号和仅传输文件按钮，"
                "未显示聊天输入框或发送按钮。"
            ),
            {},
            "进入 Alice 会话并发送消息",
        )

        self.assertFalse(
            any(
                item.get("kind")
                in {"chat_thread", "chat_input", "input", "search_field"}
                or item.get("label") == "发送"
                for item in context["affordances"]
            )
        )

    def test_negative_search_evidence_cannot_create_qq_search_field(self) -> None:
        context = context_helpers._infer_computer_use_context(
            "QQ 登录页面未显示搜索框、联系人列表或会话列表。",
            {},
            "搜索 Alice 联系人",
        )

        self.assertNotIn(
            "search_field",
            {item["kind"] for item in context["affordances"]},
        )

    def test_cross_app_ax_reference_is_not_exposed_to_brain(self) -> None:
        context = _ax_context(
            "wechat",
            "微信",
            [
                {
                    "role": "AXTextArea",
                    "label": "消息输入框",
                    "supports": ["focus", "type_text"],
                    "ax_ref": _ref(
                        "qq",
                        "AXTextArea",
                        [0, 4],
                        "消息输入框",
                    ),
                }
            ],
            query="消息输入框",
        )

        self.assertEqual(context["affordances"], [])

    def test_stale_ax_search_withholds_all_executable_references(self) -> None:
        context = _ax_context(
            "qq",
            "QQ",
            [
                {
                    "role": "AXTextArea",
                    "label": "消息输入框",
                    "supports": ["focus", "type_text"],
                    "ax_ref": _ref(
                        "qq",
                        "AXTextArea",
                        [0, 4],
                        "消息输入框",
                    ),
                }
            ],
            query="消息输入框",
            stale=True,
        )

        self.assertEqual(context["affordances"], [])
        self.assertTrue(context["ax_search"]["executable_refs_withheld"])

    def test_insufficient_ax_search_withholds_all_executable_references(self) -> None:
        context = _ax_context(
            "wechat",
            "微信",
            [
                {
                    "role": "AXTextArea",
                    "label": "消息输入框",
                    "supports": ["focus", "type_text"],
                    "ax_ref": _ref(
                        "wechat",
                        "AXTextArea",
                        [0, 4],
                        "消息输入框",
                    ),
                }
            ],
            query="消息输入框",
            sufficient=False,
        )

        self.assertEqual(context["affordances"], [])
        self.assertTrue(context["ax_search"]["executable_refs_withheld"])

    def test_finder_desktop_ax_remains_usable_without_ax_window(self) -> None:
        context = _ax_context(
            "finder",
            "访达",
            [
                {
                    "role": "AXImage",
                    "label": "项目替身",
                    "url": "file:///Users/example/Desktop/item",
                    "supports": ["open"],
                    "ax_ref": _ref(
                        "finder",
                        "AXImage",
                        [0, 0],
                        "项目替身",
                        activation="open",
                    ),
                }
            ],
            query="打开项目替身",
        )

        self.assertEqual(context["surface"]["app_id"], "finder")
        self.assertEqual(context["affordances"][0]["kind"], "file_item")
        self.assertEqual(context["affordances"][0]["activation"], "open")


class MultiWindowTargetingTests(unittest.TestCase):
    def _search(
        self,
        app_id: str,
        *,
        collection_label: str,
        labels_by_window: list[list[str]],
        query: str,
        focused_window: int | None = None,
    ) -> dict[str, object]:
        cache = AXSnapshotCache()
        elements = _windowed_collection(
            app_id,
            collection_label=collection_label,
            labels_by_window=labels_by_window,
            focused_window=focused_window,
        )
        cache.put(
            app_id,
            pid=700,
            elements=elements,
            visited_count=len(elements),
            capture_truncated=False,
        )
        return cache.search(app_id, pid=700, query=query, limit=16)

    def test_qq_same_contact_in_two_windows_is_ambiguous(self) -> None:
        result = self._search(
            "qq",
            collection_label="会话列表",
            labels_by_window=[["Alice"], ["Alice"]],
            query="打开 Alice 会话",
        )

        self.assertFalse(result["sufficient"])
        self.assertEqual(
            result["insufficiency_reason"],
            "ambiguous_actionable_matches",
        )

    def test_qq_unique_focused_window_wins_over_background_window(self) -> None:
        result = self._search(
            "qq",
            collection_label="会话列表",
            labels_by_window=[["Alice"], ["Alice"]],
            query="打开 Alice 会话",
            focused_window=1,
        )

        targets = [
            item
            for item in result["elements"]
            if item.get("context_relation") == "collection_item"
        ]
        self.assertTrue(result["sufficient"])
        self.assertEqual(targets[0]["tree_path"][0], 1)

    def test_wechat_same_contact_in_two_windows_is_ambiguous(self) -> None:
        result = self._search(
            "wechat",
            collection_label="会话列表",
            labels_by_window=[["Alice"], ["Alice"]],
            query="选择 Alice 会话",
        )

        self.assertFalse(result["sufficient"])
        self.assertEqual(
            result["insufficiency_reason"],
            "ambiguous_actionable_matches",
        )

    def test_music_same_album_in_two_windows_is_ambiguous(self) -> None:
        result = self._search(
            "music",
            collection_label="专辑",
            labels_by_window=[["Album A"], ["Album A"]],
            query="选择 Album A 专辑",
        )

        self.assertFalse(result["sufficient"])
        self.assertEqual(
            result["insufficiency_reason"],
            "ambiguous_actionable_matches",
        )

    def test_finder_same_file_in_two_windows_is_ambiguous(self) -> None:
        result = self._search(
            "finder",
            collection_label="当前文件列表",
            labels_by_window=[["Report.pdf"], ["Report.pdf"]],
            query="选择 Report.pdf 文件",
        )

        self.assertFalse(result["sufficient"])
        self.assertEqual(
            result["insufficiency_reason"],
            "ambiguous_actionable_matches",
        )

    def test_unique_name_across_windows_remains_actionable(self) -> None:
        result = self._search(
            "qq",
            collection_label="会话列表",
            labels_by_window=[["Bob"], ["Alice"]],
            query="打开 Alice 会话",
        )

        targets = [
            item
            for item in result["elements"]
            if item.get("label") == "Alice"
            and isinstance(item.get("ax_ref"), dict)
        ]
        self.assertTrue(result["sufficient"])
        self.assertEqual([item["label"] for item in targets], ["Alice"])

    def test_unique_target_and_collection_anchor_share_one_window(self) -> None:
        result = self._search(
            "qq",
            collection_label="会话列表",
            labels_by_window=[["Bob"], ["Alice"]],
            query="打开 Alice 会话",
        )

        target = next(
            item
            for item in result["elements"]
            if item.get("label") == "Alice"
            and isinstance(item.get("ax_ref"), dict)
        )
        anchor = next(
            item
            for item in result["elements"]
            if item.get("context_relation") == "collection_anchor"
        )
        self.assertEqual(
            target["tree_path"][0],
            anchor["tree_path"][0],
        )

    def test_duplicate_names_inside_one_collection_remain_ambiguous(self) -> None:
        result = self._search(
            "wechat",
            collection_label="联系人列表",
            labels_by_window=[["Alice", "Alice"]],
            query="选择 Alice 联系人",
        )

        self.assertFalse(result["sufficient"])
        self.assertEqual(result["actionable_match_count"], 2)

    def test_window_signatures_distinguish_title_and_geometry(self) -> None:
        cache = AXSnapshotCache()
        elements = _windowed_collection(
            "finder",
            collection_label="当前文件列表",
            labels_by_window=[["A.txt"], ["B.txt"]],
        )
        cache.put(
            "finder",
            pid=701,
            elements=elements,
            visited_count=len(elements),
            capture_truncated=False,
        )

        result = cache.search("finder", pid=701, query="", limit=16)

        self.assertEqual(len(result["index"]["window_signatures"]), 2)


class VirtualizedCollectionTests(unittest.TestCase):
    def _search(
        self,
        app_id: str,
        *,
        collection_label: str,
        item_count: int,
        query: str,
        logical_total: int | None = None,
        long_labels: bool = False,
        capture_truncated: bool = False,
    ) -> dict[str, object]:
        cache = AXSnapshotCache()
        elements = _collection(
            app_id,
            label=collection_label,
            item_count=item_count,
            logical_total=logical_total,
            long_labels=long_labels,
        )
        cache.put(
            app_id,
            pid=800,
            elements=elements,
            visited_count=len(elements),
            capture_truncated=capture_truncated,
        )
        return cache.search(app_id, pid=800, query=query, limit=16)

    def test_music_virtualized_grid_does_not_claim_all_albums(self) -> None:
        result = self._search(
            "music",
            collection_label="专辑",
            item_count=20,
            logical_total=100,
            query="列出所有专辑名称",
        )

        self.assertFalse(result["collection_complete"])
        self.assertFalse(result["sufficient"])
        self.assertEqual(result["collection_item_total_count"], 100)

    def test_finder_virtualized_list_does_not_claim_all_files(self) -> None:
        result = self._search(
            "finder",
            collection_label="当前文件列表",
            item_count=18,
            logical_total=250,
            query="列出文件夹中的所有文件名称",
        )

        self.assertFalse(result["collection_complete"])
        self.assertFalse(result["sufficient"])
        self.assertEqual(result["collection_item_total_count"], 250)

    def test_music_sidebar_album_match_cannot_relabel_home_collection(self) -> None:
        cache = AXSnapshotCache()
        elements = [
            {
                "role": "AXWindow",
                "tree_path": [0],
                "label": "音乐",
                "bounds": {
                    "x": 40,
                    "y": 40,
                    "width": 1000,
                    "height": 760,
                },
            },
            {
                "role": "AXOutline",
                "tree_path": [0, 0],
                "label": "边栏",
                "bounds": {
                    "x": 60,
                    "y": 80,
                    "width": 200,
                    "height": 620,
                },
            },
            {
                "role": "AXRow",
                "tree_path": [0, 0, 0],
                "label": "专辑",
                "bounds": {
                    "x": 70,
                    "y": 180,
                    "width": 180,
                    "height": 28,
                },
                "supports": ["select"],
                "ax_ref": _ref(
                    "music",
                    "AXRow",
                    [0, 0, 0],
                    "专辑",
                ),
            },
            {
                "role": "AXGrid",
                "tree_path": [0, 1],
                "label": "主页",
                "bounds": {
                    "x": 280,
                    "y": 80,
                    "width": 720,
                    "height": 620,
                },
            },
            {
                "role": "AXRow",
                "tree_path": [0, 1, 0],
                "label": "主页推荐一",
                "bounds": {
                    "x": 300,
                    "y": 120,
                    "width": 220,
                    "height": 180,
                },
                "supports": ["select"],
            },
            {
                "role": "AXRow",
                "tree_path": [0, 1, 1],
                "label": "主页推荐二",
                "bounds": {
                    "x": 540,
                    "y": 120,
                    "width": 220,
                    "height": 180,
                },
                "supports": ["select"],
            },
        ]
        cache.put(
            "music",
            pid=801,
            elements=elements,
            visited_count=len(elements),
            capture_truncated=False,
        )

        result = cache.search(
            "music",
            pid=801,
            query="列出所有专辑名称",
            limit=16,
        )

        self.assertTrue(
            result["collection_label"] == "专辑"
            or not result["sufficient"]
        )

    def test_capture_truncation_never_returns_executable_complete_result(self) -> None:
        result = self._search(
            "music",
            collection_label="专辑",
            item_count=8,
            query="选择 Item 003 专辑",
            capture_truncated=True,
        )

        self.assertFalse(result["sufficient"])
        self.assertTrue(result["stale"])
        self.assertFalse(
            any("ax_ref" in item for item in result["elements"])
        )

    def test_sixty_five_item_collection_is_explicitly_incomplete(self) -> None:
        result = self._search(
            "music",
            collection_label="专辑",
            item_count=AX_COLLECTION_READ_MAX_ITEMS + 1,
            query="列出所有专辑名称",
        )

        self.assertFalse(result["collection_complete"])
        self.assertFalse(result["sufficient"])
        self.assertEqual(
            result["insufficiency_reason"],
            "collection_items_truncated",
        )

    def test_sixty_four_item_collection_reaches_brain_without_refs(self) -> None:
        result = self._search(
            "music",
            collection_label="专辑",
            item_count=AX_COLLECTION_READ_MAX_ITEMS,
            query="列出所有专辑名称",
        )
        context = context_helpers._infer_computer_use_context(
            "AX 读取完成",
            {
                "accessibility": {
                    "usable": True,
                    "app": {
                        "app_id": "music",
                        "name": "音乐",
                        "surface": "music_gui",
                    },
                    "elements": result["elements"],
                    "search": result,
                }
            },
            "列出所有专辑名称",
        )

        self.assertTrue(result["collection_complete"])
        self.assertTrue(result["collection_labels_complete"])
        self.assertEqual(
            len(context["ax_search"]["collection_items"]),
            AX_COLLECTION_READ_MAX_ITEMS,
        )
        self.assertEqual(context["affordances"], [])

    def test_long_collection_labels_report_loss_instead_of_lying(self) -> None:
        result = self._search(
            "music",
            collection_label="专辑",
            item_count=AX_COLLECTION_READ_MAX_ITEMS,
            query="列出所有专辑名称",
            long_labels=True,
        )

        self.assertTrue(result["collection_complete"])
        self.assertFalse(result["collection_labels_complete"])
        self.assertGreater(result["collection_labels_truncated_count"], 0)
        self.assertFalse(result["sufficient"])

    def test_persistent_chunk_store_reloads_all_four_apps(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            storage = Path(temporary)
            writer = AXSnapshotCache(storage_dir=storage, chunk_size=50)
            app_specs = {
                "qq": ("QQ", ("腾讯QQ",)),
                "wechat": ("微信", ("WeChat",)),
                "music": ("音乐", ("Music",)),
                "finder": ("访达", ("Finder",)),
            }
            for app_id, (name, aliases) in app_specs.items():
                elements = _collection(
                    app_id,
                    label="项目列表",
                    item_count=120,
                )
                writer.put(
                    app_id,
                    app_name=name,
                    aliases=aliases,
                    pid=900,
                    elements=elements,
                    visited_count=len(elements),
                    capture_truncated=False,
                )

            reader = AXSnapshotCache(storage_dir=storage, chunk_size=50)
            status = reader.status()

            self.assertEqual(status["app_count"], 4)
            self.assertEqual(reader.find_app_id("腾讯QQ"), "qq")
            self.assertEqual(reader.find_app_id("WeChat"), "wechat")
            self.assertEqual(reader.find_app_id("Music"), "music")
            self.assertEqual(reader.find_app_id("Finder"), "finder")
            for app_id in app_specs:
                with self.subTest(app_id=app_id):
                    result = reader.search(
                        app_id,
                        pid=900,
                        query="Item 119",
                        limit=8,
                    )
                    self.assertEqual(result["source"], "disk")
                    self.assertGreater(result["matched_count"], 0)


class ChatSendTransactionTests(unittest.TestCase):
    @staticmethod
    def _observation(
        *,
        app_id: str,
        value: str,
        contact: str = "Alice",
        role: str = "AXTextArea",
    ) -> dict[str, object]:
        return {
            "chat_context": {"contact": contact},
            "frame": {
                "accessibility": {
                    "app": {"app_id": app_id},
                    "elements": [
                        {
                            "role": role,
                            "value": value,
                            "tree_path": [0, 2],
                            "ax_ref": {
                                "app_id": app_id,
                                "role": role,
                                "path": [0, 2],
                            },
                        }
                    ],
                }
            },
            "observations": [],
        }

    def test_observation_requires_exact_full_draft_not_substring(self) -> None:
        observation = self._observation(
            app_id="qq",
            value="前缀 测试草稿 后缀",
        )

        self.assertFalse(
            observation_confirms_expected_draft(
                observation,
                "测试草稿",
                expected_chat="Alice",
                input_ax_ref={
                    "app_id": "qq",
                    "role": "AXTextArea",
                    "path": [0, 2],
                },
            )
        )

    def test_observation_rejects_draft_from_other_application(self) -> None:
        observation = self._observation(
            app_id="wechat",
            value="测试草稿",
        )

        self.assertFalse(
            observation_confirms_expected_draft(
                observation,
                "测试草稿",
                expected_chat="Alice",
                input_ax_ref={
                    "app_id": "qq",
                    "role": "AXTextArea",
                    "path": [0, 2],
                },
            )
        )

    def test_chat_draft_observation_requires_reviewed_input_reference(self) -> None:
        observation = self._observation(
            app_id="qq",
            value="测试草稿",
        )

        self.assertFalse(
            observation_confirms_expected_draft(
                observation,
                "测试草稿",
                expected_chat="Alice",
                input_ax_ref=None,
            )
        )

    def test_search_field_cannot_substitute_for_reviewed_chat_editor(self) -> None:
        observation = self._observation(
            app_id="qq",
            value="测试草稿",
            role="AXSearchField",
        )

        self.assertFalse(
            observation_confirms_expected_draft(
                observation,
                "测试草稿",
                expected_chat="Alice",
                input_ax_ref=None,
            )
        )

    def test_exact_draft_and_reviewed_input_still_pass(self) -> None:
        observation = self._observation(
            app_id="wechat",
            value="测试草稿",
        )

        self.assertTrue(
            observation_confirms_expected_draft(
                observation,
                "测试草稿",
                expected_chat="Alice",
                input_ax_ref={
                    "app_id": "wechat",
                    "role": "AXTextArea",
                    "path": [0, 2],
                },
            )
        )

    def test_live_send_verifier_requires_exact_full_draft(self) -> None:
        for target_app, app_name in (("QQ", "QQ"), ("WeChat", "微信")):
            with self.subTest(target_app=target_app):
                source = _ChatRuntime(
                    app_name,
                    draft="测试草稿",
                )
                profile = resolve_macos_ax_app(target_app)
                input_ref = _read_node(
                    source,
                    4,
                    profile,
                    path=(0, 1),
                    ancestors=(
                        {"role": "AXApplication", "label": app_name},
                        {"role": "AXWindow", "label": app_name},
                    ),
                )["ax_ref"]

                with self.assertRaisesRegex(
                    RuntimeError,
                    "expected draft",
                ):
                    verify_macos_accessibility_chat_context(
                        target_app,
                        intended_chat="Alice",
                        input_ax_ref=input_ref,
                        expected_text="测试草稿",
                        require_input_focused=True,
                        platform_name="darwin",
                        runtime_factory=lambda: _ChatRuntime(
                            app_name,
                            draft="测试草稿，外加了未审批的内容",
                        ),
                    )

    def test_transaction_overrides_cross_app_recipient_and_draft_hijack(self) -> None:
        trusted_ref = _ref(
            "qq",
            "AXTextArea",
            [0, 2],
            "Alice 输入框",
        )
        transaction = {
            "target_app": "QQ",
            "intended_chat": "Alice",
            "input_ax_ref": trusted_ref,
            "expected_text": "只发给 Alice",
        }
        intermediate = ReviewableProposal.act(
            action_type="click",
            summary="打开表情面板",
            payload={"target_app": "QQ", "label": "表情"},
        )
        malicious = BrainDecision.propose_act(
            "key_press",
            {
                "key": "enter",
                "target_app": "WeChat",
                "intended_chat": "Bob",
                "input_ax_ref": _ref(
                    "wechat",
                    "AXTextArea",
                    [0, 9],
                    "Bob 输入框",
                ),
                "expected_text": "发给 Bob",
            },
        )

        inherited = with_inherited_enter_expected_text(
            malicious,
            previous_proposal=intermediate,
            execution={"target_app": "QQ"},
            chat_send_transaction=transaction,
        )

        arguments = inherited.payload["arguments"]
        self.assertEqual(arguments["target_app"], "QQ")
        self.assertEqual(arguments["intended_chat"], "Alice")
        self.assertEqual(arguments["input_ax_ref"], trusted_ref)
        self.assertEqual(arguments["expected_text"], "只发给 Alice")

    def test_transaction_reference_is_deeply_isolated_from_next_proposal(self) -> None:
        transaction = {
            "target_app": "QQ",
            "intended_chat": "Alice",
            "input_ax_ref": {
                "app_id": "qq",
                "role": "AXTextArea",
                "path": [0, 2],
                "bounds": {
                    "x": 400,
                    "y": 600,
                    "width": 500,
                    "height": 100,
                },
            },
            "expected_text": "测试草稿",
        }
        previous = ReviewableProposal.act(
            action_type="click",
            summary="中间动作",
            payload={"target_app": "QQ"},
        )
        decision = BrainDecision.propose_act(
            "key_press",
            {"key": "return"},
        )

        inherited = with_inherited_enter_expected_text(
            decision,
            previous_proposal=previous,
            execution={},
            chat_send_transaction=transaction,
        )
        inherited.payload["arguments"]["input_ax_ref"]["path"][0] = 99
        inherited.payload["arguments"]["input_ax_ref"]["bounds"]["x"] = 999

        self.assertEqual(transaction["input_ax_ref"]["path"], [0, 2])
        self.assertEqual(transaction["input_ax_ref"]["bounds"]["x"], 400)

    def test_non_send_key_does_not_consume_chat_transaction(self) -> None:
        transaction = {
            "target_app": "QQ",
            "intended_chat": "Alice",
            "input_ax_ref": _ref(
                "qq",
                "AXTextArea",
                [0, 2],
                "Alice 输入框",
            ),
            "expected_text": "测试草稿",
        }
        previous = ReviewableProposal.act(
            action_type="click",
            summary="中间动作",
            payload={"target_app": "QQ"},
        )
        decision = BrainDecision.propose_act(
            "key_press",
            {"key": "tab"},
        )

        inherited = with_inherited_enter_expected_text(
            decision,
            previous_proposal=previous,
            execution={},
            chat_send_transaction=transaction,
        )

        self.assertNotIn(
            "expected_text",
            inherited.payload["arguments"],
        )


class LiveStateAndPersistentCacheTests(unittest.TestCase):
    def test_contact_search_query_requires_live_window_state(self) -> None:
        self.assertTrue(_query_requires_live_state("搜索联系人 Alice"))

    def test_chat_editor_query_requires_live_window_state(self) -> None:
        self.assertTrue(_query_requires_live_state("消息输入框 Alice"))

    def test_action_target_query_requires_live_window_state(self) -> None:
        self.assertTrue(_query_requires_live_state("打开 Alice 会话"))

    def test_exhaustive_album_query_already_requires_live_state(self) -> None:
        self.assertTrue(_query_requires_live_state("列出所有专辑名称"))

    def test_process_change_marks_cache_stale_and_strips_reference(self) -> None:
        cache = AXSnapshotCache()
        elements = _windowed_collection(
            "qq",
            collection_label="会话列表",
            labels_by_window=[["Alice"]],
        )
        cache.put(
            "qq",
            pid=100,
            elements=elements,
            visited_count=len(elements),
            capture_truncated=False,
        )

        result = cache.search(
            "qq",
            pid=101,
            query="打开 Alice 会话",
            limit=16,
        )

        self.assertTrue(result["process_changed"])
        self.assertTrue(result["stale"])
        self.assertFalse(result["sufficient"])
        self.assertFalse(
            any("ax_ref" in item for item in result["elements"])
        )

    def test_manual_invalidation_strips_reference_until_refresh(self) -> None:
        cache = AXSnapshotCache()
        elements = _windowed_collection(
            "finder",
            collection_label="当前文件列表",
            labels_by_window=[["Report.pdf"]],
        )
        cache.put(
            "finder",
            pid=200,
            elements=elements,
            visited_count=len(elements),
            capture_truncated=False,
        )
        cache.invalidate("finder", reason="window_changed")

        result = cache.search(
            "finder",
            pid=200,
            query="选择 Report.pdf 文件",
            limit=16,
        )

        self.assertTrue(result["stale"])
        self.assertEqual(result["stale_reason"], "window_changed")
        self.assertFalse(result["sufficient"])
        self.assertFalse(
            any("ax_ref" in item for item in result["elements"])
        )


if __name__ == "__main__":
    unittest.main()
