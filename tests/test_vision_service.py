from __future__ import annotations

import json
import unittest

from backend.vision import (
    DEFAULT_VISION_CONFIG,
    VisionDisabledError,
    VisionService,
    build_grounded_context_prefix,
    normalize_vision_config,
)


class VisionServiceTests(unittest.TestCase):
    def test_default_config_is_disabled(self) -> None:
        config = normalize_vision_config({})
        self.assertFalse(config["enabled"])
        self.assertEqual(config["inject_policy"], "when_requested")
        self.assertEqual(DEFAULT_VISION_CONFIG["persist_frames"], False)
        self.assertTrue(config["active_observation"]["enabled"])
        self.assertEqual(config["active_observation"]["allowed_interaction"], "light")
        self.assertGreaterEqual(config["active_observation"]["timeout_sec"], 12.0)
        self.assertGreaterEqual(config["active_observation"]["settle_ms"], 350)
        self.assertFalse(config["passive_capture"]["use_for_forced"])

    def test_normalize_clamps_boundaries_and_policy(self) -> None:
        config = normalize_vision_config(
            {
                "enabled": True,
                "capture_interval_ms": 1,
                "max_width": 99,
                "jpeg_quality": 99,
                "context_ttl_sec": 999,
                "inject_policy": "bad",
                "persist_frames": True,
                "active_observation": {
                    "enabled": True,
                    "allowed_interaction": "type_password",
                    "timeout_sec": 999,
                    "settle_ms": 99999,
                },
                "passive_capture": {"use_for_forced": True},
            }
        )
        self.assertEqual(config["capture_interval_ms"], 1000)
        self.assertEqual(config["max_width"], 320)
        self.assertEqual(config["jpeg_quality"], 95)
        self.assertEqual(config["context_ttl_sec"], 300)
        self.assertEqual(config["inject_policy"], "when_requested")
        self.assertFalse(config["persist_frames"])
        self.assertFalse(config["force_grounding"])
        self.assertEqual(config["grounding_mode"], "auto")
        self.assertTrue(config["include_ui_metadata"])
        self.assertEqual(config["routing"]["visual_change_threshold"], 0)
        self.assertEqual(config["routing"]["vlm_cooldown_sec"], 10.0)
        self.assertEqual(config["routing"]["stable_after_change_ms"], 700)
        self.assertEqual(config["active_observation"]["allowed_interaction"], "light")
        self.assertEqual(config["active_observation"]["timeout_sec"], 20.0)
        self.assertEqual(config["active_observation"]["settle_ms"], 2000)
        self.assertTrue(config["passive_capture"]["use_for_forced"])

    def test_normalize_clamps_routing_config(self) -> None:
        config = normalize_vision_config(
            {
                "routing": {
                    "enabled": True,
                    "visual_change_threshold": 999,
                    "vlm_cooldown_sec": 999,
                    "stable_after_change_ms": 99999,
                }
            }
        )

        self.assertTrue(config["routing"]["enabled"])
        self.assertEqual(config["routing"]["visual_change_threshold"], 64)
        self.assertEqual(config["routing"]["vlm_cooldown_sec"], 300.0)
        self.assertEqual(config["routing"]["stable_after_change_ms"], 10000)

    def test_disabled_service_rejects_frame_writes(self) -> None:
        service = VisionService({"enabled": False})
        with self.assertRaises(VisionDisabledError):
            service.update_frame({"mime_type": "image/jpeg", "data_url": "data:image/jpeg;base64,abc"})
        self.assertFalse(service.status()["has_frame"])

    def test_frame_write_context_and_clear_stay_in_memory(self) -> None:
        service = VisionService({"enabled": True}, now=lambda: 10.0)
        service.update_frame({"mime_type": "image/jpeg", "data_url": "data:image/jpeg;base64,abc"})

        status = service.status()
        self.assertTrue(status["has_frame"])
        self.assertEqual(status["mime_type"], "image/jpeg")
        self.assertNotIn("data_url", status)

        context = service.context(include_image=True)
        self.assertTrue(context["available"])
        self.assertEqual(context["image"]["data_url"], "data:image/jpeg;base64,abc")
        self.assertIn("最近捕获的屏幕帧", context["summary"])
        self.assertNotIn("窗口", context["summary"])

        service.clear()
        self.assertFalse(service.status()["has_frame"])

    def test_status_and_context_do_not_expose_analyzer_or_desktop_secrets(self) -> None:
        service = VisionService(
            {
                "enabled": True,
                "analyzer": {
                    "enabled": True,
                    "provider": "openai_compatible_vlm",
                    "api_key": "sk-vision-secret",
                    "api_key_env": "VISION_SECRET_ENV",
                    "base_url": "https://example.test/v1",
                    "model": "vision-model",
                },
            },
            now=lambda: 10.0,
        )
        service.update_frame(
            {
                "mime_type": "image/jpeg",
                "data_url": "data:image/jpeg;base64,abc",
                "desktop_context": {
                    "foreground_app": "Code",
                    "api_key": "desktop-secret",
                    "access_token": "desktop-token",
                    "metadata": {"authToken": "nested-token", "workspace": "Ipet"},
                },
            }
        )

        status = service.status()
        context = service.context()
        combined = json.dumps({"status": status, "context": context}, ensure_ascii=False)

        self.assertNotIn("sk-vision-secret", combined)
        self.assertNotIn("VISION_SECRET_ENV", combined)
        self.assertNotIn("desktop-secret", combined)
        self.assertNotIn("desktop-token", combined)
        self.assertNotIn("nested-token", combined)
        self.assertNotIn("api_key", status["analyzer"])
        self.assertNotIn("api_key_env", context["analyzer"])
        self.assertEqual(status["desktop_context"], {"foreground_app": "Code", "metadata": '{"workspace":"Ipet"}'})

    def test_frame_payload_with_observations_builds_grounded_context(self) -> None:
        service = VisionService({"enabled": True}, now=lambda: 10.0)
        service.update_frame(
            {
                "mime_type": "image/jpeg",
                "data_url": "data:image/jpeg;base64,abc",
                "frame_id": "screen-1",
                "frame_hash": "sha256:abcdef",
                "summary": "屏幕上打开着设置窗口。",
                "observations": [
                    {
                        "claim": "设置页中显示自动视觉区域",
                        "evidence": "标题为自动视觉，旁边有默认关闭状态",
                        "region": "center",
                        "confidence": 0.91,
                        "source": "mock-analyzer",
                    }
                ],
                "unknowns": ["看不清后台窗口标题"],
            }
        )

        status = service.status()
        context = service.context()
        self.assertTrue(status["grounded"])
        self.assertEqual(status["evidence_count"], 1)
        self.assertEqual(status["frame_id"], "screen-1")
        self.assertEqual(status["frame_hash"], "sha256:abcdef")
        self.assertTrue(context["grounded"])
        self.assertEqual(context["verified_summary"], "屏幕上打开着设置窗口。")
        self.assertEqual(context["observations"][0]["claim"], "设置页中显示自动视觉区域")

        prefix = build_grounded_context_prefix(context, forced=True)
        self.assertIn("[强制视觉证据]", prefix)
        self.assertIn("frame_id=screen-1", prefix)
        self.assertIn("设置页中显示自动视觉区域", prefix)
        self.assertIn("只能基于下面列出的当前屏幕证据回答", prefix)
        self.assertIn("不要根据应用名", prefix)
        self.assertIn("不要推测用户正在做什么", prefix)

    def test_macos_menu_metadata_does_not_count_as_visual_evidence(self) -> None:
        service = VisionService({"enabled": True}, now=lambda: 10.0)
        service.update_frame(
            {
                "mime_type": "image/jpeg",
                "data_url": "data:image/jpeg;base64,abc",
                "frame_id": "screen-menu-only",
                "frame_hash": "sha256:menuonly",
                "summary": "macOS 菜单栏左上角显示当前前台应用：Kazumi",
                "desktop_context": {
                    "foreground_app": "Kazumi",
                    "window_title": "",
                    "menu_bar_items": ["Apple", "Kazumi", "编辑", "播放器", "窗口", "帮助"],
                },
                "observations": [
                    {
                        "claim": "macOS 菜单栏左上角显示当前前台应用：Kazumi",
                        "evidence": "System Events 返回的前台应用为 Kazumi；菜单栏项目为 Apple, Kazumi, 编辑, 播放器, 窗口, 帮助",
                        "region": "macOS menu bar left",
                        "confidence": 0.88,
                        "source": "macos-system-events",
                    }
                ],
            }
        )

        context = service.context()
        self.assertTrue(context["available"])
        self.assertFalse(context["grounded"])
        self.assertEqual(context["evidence_count"], 0)
        self.assertEqual(context["observations"], [])
        self.assertEqual(context["desktop_context"]["foreground_app"], "Kazumi")

        prefix = build_grounded_context_prefix(context, forced=True)
        self.assertIn("尚无可验证视觉证据", prefix)
        self.assertIn("我无法从当前截图确认", prefix)
        self.assertNotIn("菜单栏左上角显示当前前台应用", prefix)

    def test_observations_are_sanitized_and_limited(self) -> None:
        service = VisionService({"enabled": True}, now=lambda: 10.0)
        service.update_frame(
            {
                "mime_type": "image/png",
                "data_url": "data:image/png;base64,abc",
                "summary": "S" * 900,
                "observations": [
                    {
                        "claim": f"claim-{index}-" + ("x" * 500),
                        "evidence": "e" * 700,
                        "region": "r" * 120,
                        "confidence": 2 if index % 2 == 0 else -1,
                        "source": "s" * 80,
                    }
                    for index in range(12)
                ],
                "unknowns": ["u" * 300 for _ in range(12)],
            }
        )

        context = service.context()
        self.assertEqual(context["evidence_count"], 8)
        self.assertLessEqual(len(context["verified_summary"]), 500)
        self.assertEqual(len(context["observations"]), 8)
        self.assertLessEqual(len(context["observations"][0]["claim"]), 240)
        self.assertLessEqual(len(context["observations"][0]["evidence"]), 360)
        self.assertLessEqual(len(context["observations"][0]["region"]), 80)
        self.assertLessEqual(len(context["observations"][0]["source"]), 40)
        self.assertEqual(context["observations"][0]["confidence"], 1.0)
        self.assertEqual(context["observations"][1]["confidence"], 0.0)
        self.assertEqual(len(context["unknowns"]), 8)
        self.assertLessEqual(len(context["unknowns"][0]), 160)

    def test_context_expires_after_ttl(self) -> None:
        ticks = [10.0]
        service = VisionService({"enabled": True, "context_ttl_sec": 5}, now=lambda: ticks[0])
        service.update_frame({"mime_type": "image/png", "data_url": "data:image/png;base64,abc"})
        ticks[0] = 16.0

        status = service.status()
        context = service.context(include_image=True)
        self.assertFalse(status["has_frame"])
        self.assertFalse(context["available"])
        self.assertNotIn("image", context)

    def test_disabling_service_hides_existing_frame(self) -> None:
        service = VisionService({"enabled": True}, now=lambda: 10.0)
        service.update_frame({"mime_type": "image/jpeg", "data_url": "data:image/jpeg;base64,abc"})
        service.configure({"enabled": False})

        self.assertFalse(service.status()["has_frame"])
        self.assertFalse(service.context(include_image=True)["available"])

    def test_should_inject_respects_policy_and_explicit_request_state(self) -> None:
        service = VisionService({"enabled": True, "inject_policy": "when_requested"}, now=lambda: 10.0)
        service.update_frame({"mime_type": "image/jpeg", "data_url": "data:image/jpeg;base64,abc"})
        self.assertFalse(service.should_inject(requested=False))
        self.assertTrue(service.should_inject(requested=True))

        service.configure({"enabled": True, "inject_policy": "always_summary"})
        self.assertTrue(service.should_inject(requested=False))

        service.configure({"enabled": True, "inject_policy": "off"})
        self.assertFalse(service.should_inject(requested=True))

    def test_forced_prefix_refuses_when_frame_has_no_observations(self) -> None:
        service = VisionService({"enabled": True}, now=lambda: 10.0)
        service.update_frame({"mime_type": "image/jpeg", "data_url": "data:image/jpeg;base64,abc"})

        prefix = build_grounded_context_prefix(service.context(), forced=True)
        self.assertIn("有最新截图，但尚无可验证视觉证据", prefix)
        self.assertIn("我无法从当前截图确认", prefix)
        self.assertIn("不要推测失败原因", prefix)
        self.assertIn("不要承诺重新截图", prefix)
        self.assertNotIn("最近捕获的屏幕帧，可在需要时进行视觉分析", prefix)

    def test_forced_prefix_uses_attached_active_frame_without_observations(self) -> None:
        service = VisionService({"enabled": True}, now=lambda: 10.0)
        service.update_frame(
            {
                "mime_type": "image/jpeg",
                "data_url": "data:image/jpeg;base64,abc",
                "frame_id": "active-main",
                "frame_hash": "sha256:main",
                "unknowns": ["VLM 尚未返回结构化画面证据"],
            }
        )

        prefix = build_grounded_context_prefix(service.context(), forced=True, attached_active_frames=True)

        self.assertIn("直接看本轮附带的 active screenshot/vision_frames", prefix)
        self.assertIn("metadata / target_candidates / app/window title 仅用于路由和调试", prefix)
        self.assertIn("不要因为缺少 structured observations 自动拒答", prefix)
        self.assertNotIn("必须回答“我无法从当前截图确认”", prefix)

    def test_non_forced_prefix_does_not_promise_retry_when_unverified(self) -> None:
        service = VisionService({"enabled": True}, now=lambda: 10.0)
        service.update_frame({"mime_type": "image/jpeg", "data_url": "data:image/jpeg;base64,abc"})

        prefix = build_grounded_context_prefix(service.context(), forced=False)
        self.assertIn("尚无可验证视觉证据", prefix)
        self.assertIn("无法从当前截图确认", prefix)
        self.assertIn("不要承诺重新截图", prefix)

    def test_passive_timeline_keeps_recent_semantic_changes_and_updates_last_observed(self) -> None:
        ticks = [10.0]
        service = VisionService({"enabled": True}, now=lambda: ticks[0])

        for index in range(6):
            ticks[0] = 10.0 + index
            service.update_frame(
                {
                    "mime_type": "image/jpeg",
                    "data_url": f"data:image/jpeg;base64,change-{index}",
                    "frame_id": f"frame-{index}",
                    "summary": f"屏幕变化 {index}",
                    "important_objects": [f"object-{index}"],
                    "visible_text": [f"text-{index}"],
                    "confidence": 0.5 + index / 10,
                    "route_decision": {
                        "action": "analyze",
                        "reason": "change_detected",
                        "events": ["visual_hash_changed"],
                        "should_analyze": True,
                    },
                }
            )

        timeline = service.context()["timeline"]
        self.assertEqual(len(timeline), 5)
        self.assertEqual([item["frame_id"] for item in timeline], [f"frame-{index}" for index in range(1, 6)])
        self.assertEqual(timeline[-1]["change_summary"], "屏幕变化 5")
        self.assertEqual(timeline[-1]["important_objects"], ["object-5"])
        self.assertEqual(timeline[-1]["visible_text"], ["text-5"])

        ticks[0] = 20.0
        service.update_frame(
            {
                "mime_type": "image/jpeg",
                "data_url": "data:image/jpeg;base64,change-5",
                "frame_id": "frame-5-repeat",
                "summary": "不应新增",
                "route_decision": {"action": "reuse", "reason": "no_change", "events": [], "reuse_evidence": True},
            }
        )

        after_no_change = service.context()["timeline"]
        self.assertEqual(len(after_no_change), 5)
        self.assertEqual(after_no_change[-1]["frame_id"], "frame-5")
        self.assertEqual(after_no_change[-1]["last_observed_at"], 20.0)

    def test_passive_timeline_prefix_is_compact_background_context(self) -> None:
        service = VisionService({"enabled": True}, now=lambda: 10.0)
        service.update_frame(
            {
                "mime_type": "image/jpeg",
                "data_url": "data:image/jpeg;base64,abc",
                "frame_id": "frame-1",
                "summary": "设置页切到自动视觉区域。",
                "important_objects": ["设置页", "自动视觉"],
                "visible_text": ["自动视觉"],
                "confidence": 0.82,
                "route_decision": {
                    "action": "analyze",
                    "reason": "change_detected",
                    "events": ["window_title_changed"],
                    "should_analyze": True,
                },
            }
        )

        prefix = service.passive_timeline_prefix()
        self.assertIn("最近屏幕变化：", prefix)
        self.assertIn("1. 设置页切到自动视觉区域。", prefix)
        self.assertIn("对象=设置页, 自动视觉", prefix)
        self.assertLess(len(prefix), 300)

    def test_active_observation_does_not_pollute_passive_timeline(self) -> None:
        ticks = [10.0]
        service = VisionService({"enabled": True}, now=lambda: ticks[0])
        service.update_frame(
            {
                "mime_type": "image/jpeg",
                "data_url": "data:image/jpeg;base64,active",
                "frame_id": "active-1",
                "summary": "主动截图看到浏览器。",
                "observations": [{"claim": "浏览器可见", "confidence": 0.9}],
                "active_observation": {
                    "status": "success",
                    "mode": "focus_target",
                    "target_id": "target-safari",
                    "target_hint": "target-safari",
                    "desktop_targets": [
                        {
                            "target_id": "target-safari",
                            "app": "Safari",
                            "title": "Video",
                            "bounds": {"x": 10, "y": 20, "width": 900, "height": 600},
                            "focus_point": {"x": 120, "y": 32},
                        }
                    ],
                    "focused_target": {"target_id": "target-safari", "app": "Safari", "title": "Video"},
                    "action_trace": [{"action": "accessibility_raise", "target_id": "target-safari"}],
                    "click_point": {"x": 120, "y": 32},
                    "click_policy": "window_focus_only",
                    "discovery_errors": ["System Events failed: -10827"],
                    "target_candidates": [
                        {
                            "target_id": "target-safari",
                            "source": "window_enumeration",
                            "app": "Safari",
                            "title": "Video",
                            "focusable": True,
                        }
                    ],
                    "selected_candidate": {
                        "target_id": "target-safari",
                        "source": "window_enumeration",
                        "app": "Safari",
                        "title": "Video",
                        "focusable": True,
                    },
                    "focus_result": {"status": "success", "method": "window_focus_only"},
                    "verify_result": {"status": "captured", "frame_hash": "sha256:active"},
                    "detail_frames_count": 1,
                },
                "route_decision": {
                    "action": "analyze",
                    "reason": "forced",
                    "events": ["active_observation"],
                    "should_analyze": True,
                },
            }
        )

        self.assertEqual(service.passive_timeline(), [])
        self.assertTrue(service.active_context()["available"])
        self.assertEqual(service.active_context()["frame_id"], "active-1")
        active = service.active_context()["active_observation"]
        self.assertEqual(active["desktop_targets"][0]["target_id"], "target-safari")
        self.assertEqual(active["focused_target"]["app"], "Safari")
        self.assertEqual(active["click_policy"], "window_focus_only")
        self.assertEqual(active["discovery_errors"], ["System Events failed: -10827"])
        self.assertEqual(active["target_candidates"][0]["source"], "window_enumeration")
        self.assertEqual(active["selected_candidate"]["target_id"], "target-safari")
        self.assertEqual(active["focus_result"]["status"], "success")
        self.assertEqual(active["verify_result"]["status"], "captured")
        self.assertEqual(active["detail_frames_count"], 1)

        ticks[0] = 11.0
        service.update_frame(
            {
                "mime_type": "image/jpeg",
                "data_url": "data:image/jpeg;base64,passive",
                "frame_id": "passive-1",
                "summary": "后台切到设置页。",
                "route_decision": {
                    "action": "analyze",
                    "reason": "change_detected",
                    "events": ["window_title_changed"],
                    "should_analyze": True,
                },
            }
        )

        self.assertEqual(service.active_context()["frame_id"], "active-1")
        self.assertEqual([item["frame_id"] for item in service.passive_timeline()], ["passive-1"])


if __name__ == "__main__":
    unittest.main()
