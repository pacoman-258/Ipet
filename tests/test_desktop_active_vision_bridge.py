from __future__ import annotations

import subprocess
import sys
import unittest
from unittest import mock


class _FakeActiveVisionModule:
    def __init__(self) -> None:
        self.discovery_calls: list[dict] = []
        self.capture_calls: list[dict] = []

        def desktop_provider(**kwargs):
            return [{"source": "body_desktop", **kwargs}]

        def dock_provider(**kwargs):
            return [{"source": "body_dock", **kwargs}]

        def running_provider(**kwargs):
            return [{"source": "body_running", **kwargs}]

        def interaction_runner(**kwargs):
            return {"source": "body_interaction", **kwargs}

        def discover_active_vision_target_candidates(**kwargs):
            self.discovery_calls.append(kwargs)
            return {"discovered": True}

        def capture_active_vision_frame_payload(window, command_payload, config, **kwargs):
            self.capture_calls.append(
                {
                    "window": window,
                    "command_payload": command_payload,
                    "config": config,
                    **kwargs,
                }
            )
            return {"captured": True}

        self.enumerate_active_vision_desktop_targets = desktop_provider
        self.enumerate_macos_dock_item_candidates = dock_provider
        self.enumerate_active_vision_running_app_candidates = running_provider
        self.run_active_vision_light_interaction = interaction_runner
        self.discover_active_vision_target_candidates = discover_active_vision_target_candidates
        self.capture_active_vision_frame_payload = capture_active_vision_frame_payload


class _FakeScreenCaptureBridge:
    def __init__(self) -> None:
        self.unwrap_frame_calls: list[dict] = []
        self.unwrap_observation_calls: list[dict] = []

        def body_frame_encoder(screen, config):
            return {"screen": screen, "config": config}

        def body_observation_provider(config=None, **kwargs):
            return {"config": config, **kwargs}

        self.body_frame_encoder = body_frame_encoder
        self.body_observation_provider = body_observation_provider

    def unwrap_capture_screen_frame_payload_provider(self, provider, *, default_provider):
        self.unwrap_frame_calls.append({"provider": provider, "default_provider": default_provider})
        if provider is default_provider:
            return self.body_frame_encoder
        return provider

    def unwrap_collect_screen_observations_provider(self, provider, *, default_provider):
        self.unwrap_observation_calls.append({"provider": provider, "default_provider": default_provider})
        if provider is default_provider:
            return self.body_observation_provider
        return provider


def _identity(value):
    return value


def _vision_config(config):
    return {"vision": dict(config)}


def _active_observation_config(config):
    return {"active": dict(config)}


class ActiveVisionBridgeTests(unittest.TestCase):
    def _bridge(self, fake_active=None, fake_screen=None):
        from app.active_vision_bridge import ActiveVisionBridge

        fake_active = fake_active or _FakeActiveVisionModule()
        fake_screen = fake_screen or _FakeScreenCaptureBridge()
        return ActiveVisionBridge(
            active_vision_module=fake_active,
            screen_capture_bridge_factory=lambda: fake_screen,
            payload_normalizer=_identity,
            vision_config_normalizer=_vision_config,
            active_observation_config_normalizer=_active_observation_config,
        )

    def test_bridge_import_does_not_import_main_or_qt(self) -> None:
        code = (
            "import sys\n"
            "for name in ('main', 'PySide6', 'PyQt6'):\n"
            "    sys.modules.pop(name, None)\n"
            "import app.active_vision_bridge\n"
            "blocked = [name for name in sys.modules if name == 'main' or name.startswith(('PySide6', 'PyQt6'))]\n"
            "raise SystemExit(1 if blocked else 0)\n"
        )
        result = subprocess.run([sys.executable, "-c", code], check=False)
        self.assertEqual(result.returncode, 0)

    def test_discovery_unwraps_default_main_providers_to_body_module(self) -> None:
        fake_active = _FakeActiveVisionModule()
        bridge = self._bridge(fake_active=fake_active)

        def main_desktop_provider(**kwargs):
            raise AssertionError("main desktop wrapper should be unwrapped")

        def main_dock_provider(**kwargs):
            raise AssertionError("main dock wrapper should be unwrapped")

        def main_running_provider(**kwargs):
            raise AssertionError("main running wrapper should be unwrapped")

        result = bridge.discover_active_vision_target_candidates(
            desktop_targets_provider=main_desktop_provider,
            dock_items_provider=main_dock_provider,
            running_apps_provider=main_running_provider,
            default_desktop_targets_provider=main_desktop_provider,
            default_dock_items_provider=main_dock_provider,
            default_running_apps_provider=main_running_provider,
            platform_name="darwin",
            runner="runner",
        )

        self.assertEqual(result, {"discovered": True})
        call = fake_active.discovery_calls[0]
        self.assertIs(call["desktop_targets_provider"], fake_active.enumerate_active_vision_desktop_targets)
        self.assertIs(call["dock_items_provider"], fake_active.enumerate_macos_dock_item_candidates)
        self.assertIs(call["running_apps_provider"], fake_active.enumerate_active_vision_running_app_candidates)
        self.assertEqual(call["platform_name"], "darwin")
        self.assertEqual(call["runner"], "runner")

    def test_capture_unwraps_default_main_providers_to_body_module(self) -> None:
        fake_active = _FakeActiveVisionModule()
        fake_screen = _FakeScreenCaptureBridge()
        bridge = self._bridge(fake_active=fake_active, fake_screen=fake_screen)

        def main_frame_encoder(screen, config):
            raise AssertionError("main frame wrapper should be unwrapped")

        def main_observation_provider(config=None, **kwargs):
            raise AssertionError("main observation wrapper should be unwrapped")

        def main_desktop_provider(**kwargs):
            raise AssertionError("main desktop wrapper should be unwrapped")

        def main_dock_provider(**kwargs):
            raise AssertionError("main dock wrapper should be unwrapped")

        def main_running_provider(**kwargs):
            raise AssertionError("main running wrapper should be unwrapped")

        def main_interaction_runner(**kwargs):
            raise AssertionError("main interaction wrapper should be unwrapped")

        result = bridge.capture_active_vision_frame_payload(
            "window",
            {"mode": "desktop_survey"},
            {"enabled": True},
            frame_encoder=main_frame_encoder,
            observation_provider=main_observation_provider,
            desktop_targets_provider=main_desktop_provider,
            dock_items_provider=main_dock_provider,
            running_apps_provider=main_running_provider,
            interaction_runner=main_interaction_runner,
            default_frame_encoder=main_frame_encoder,
            default_observation_provider=main_observation_provider,
            default_desktop_targets_provider=main_desktop_provider,
            default_dock_items_provider=main_dock_provider,
            default_running_apps_provider=main_running_provider,
            default_interaction_runner=main_interaction_runner,
            sleeper="sleeper",
            platform_name="darwin",
        )

        self.assertEqual(result, {"captured": True})
        call = fake_active.capture_calls[0]
        self.assertIs(call["frame_encoder"], fake_screen.body_frame_encoder)
        self.assertIs(call["observation_provider"], fake_screen.body_observation_provider)
        self.assertIs(call["desktop_targets_provider"], fake_active.enumerate_active_vision_desktop_targets)
        self.assertIs(call["dock_items_provider"], fake_active.enumerate_macos_dock_item_candidates)
        self.assertIs(call["running_apps_provider"], fake_active.enumerate_active_vision_running_app_candidates)
        self.assertIs(call["interaction_runner"], fake_active.run_active_vision_light_interaction)
        self.assertIs(call["payload_normalizer"], _identity)
        self.assertIs(call["default_frame_encoder"], fake_screen.body_frame_encoder)
        self.assertIs(call["vision_config_normalizer"], _vision_config)
        self.assertIs(call["active_observation_config_normalizer"], _active_observation_config)

    def test_capture_keeps_custom_providers(self) -> None:
        fake_active = _FakeActiveVisionModule()
        fake_screen = _FakeScreenCaptureBridge()
        bridge = self._bridge(fake_active=fake_active, fake_screen=fake_screen)

        def main_frame_encoder(screen, config):
            raise AssertionError("main frame wrapper should only be used as the default identity")

        def main_observation_provider(config=None, **kwargs):
            raise AssertionError("main observation wrapper should only be used as the default identity")

        def custom_frame_encoder(screen, config):
            return {"custom": "frame"}

        def custom_observation_provider(config=None, **kwargs):
            return {"custom": "observation"}

        def custom_desktop_provider(**kwargs):
            return []

        def custom_dock_provider(**kwargs):
            return []

        def custom_running_provider(**kwargs):
            return []

        def custom_interaction_runner(**kwargs):
            return {}

        bridge.capture_active_vision_frame_payload(
            "window",
            {},
            {},
            frame_encoder=custom_frame_encoder,
            observation_provider=custom_observation_provider,
            desktop_targets_provider=custom_desktop_provider,
            dock_items_provider=custom_dock_provider,
            running_apps_provider=custom_running_provider,
            interaction_runner=custom_interaction_runner,
            default_frame_encoder=main_frame_encoder,
            default_observation_provider=main_observation_provider,
            default_desktop_targets_provider=lambda **kwargs: [],
            default_dock_items_provider=lambda **kwargs: [],
            default_running_apps_provider=lambda **kwargs: [],
            default_interaction_runner=lambda **kwargs: {},
        )

        call = fake_active.capture_calls[0]
        self.assertIs(call["frame_encoder"], custom_frame_encoder)
        self.assertIs(call["observation_provider"], custom_observation_provider)
        self.assertIs(call["desktop_targets_provider"], custom_desktop_provider)
        self.assertIs(call["dock_items_provider"], custom_dock_provider)
        self.assertIs(call["running_apps_provider"], custom_running_provider)
        self.assertIs(call["interaction_runner"], custom_interaction_runner)
        self.assertIs(call["default_frame_encoder"], main_frame_encoder)

    def test_main_active_vision_wrappers_delegate_to_bridge(self) -> None:
        import main

        bridge = mock.Mock()
        bridge.discover_active_vision_target_candidates.return_value = {"discovered": True}
        bridge.capture_active_vision_frame_payload.return_value = {"captured": True}

        with mock.patch.object(main, "_active_vision_bridge", return_value=bridge):
            discovery = main.discover_active_vision_target_candidates(platform_name="darwin")
            capture = main.capture_active_vision_frame_payload(
                "window",
                {"mode": "desktop_survey"},
                {"enabled": True},
                platform_name="darwin",
            )

        self.assertEqual(discovery, {"discovered": True})
        self.assertEqual(capture, {"captured": True})

        discovery_kwargs = bridge.discover_active_vision_target_candidates.call_args.kwargs
        self.assertIs(discovery_kwargs["default_desktop_targets_provider"], main.enumerate_active_vision_desktop_targets)
        self.assertIs(discovery_kwargs["default_dock_items_provider"], main.enumerate_macos_dock_item_candidates)
        self.assertIs(discovery_kwargs["default_running_apps_provider"], main.enumerate_active_vision_running_app_candidates)

        capture_kwargs = bridge.capture_active_vision_frame_payload.call_args.kwargs
        self.assertIs(capture_kwargs["default_frame_encoder"], main.capture_screen_frame_payload)
        self.assertIs(capture_kwargs["default_observation_provider"], main.collect_screen_observations)
        self.assertIs(capture_kwargs["default_desktop_targets_provider"], main.enumerate_active_vision_desktop_targets)
        self.assertIs(capture_kwargs["default_dock_items_provider"], main.enumerate_macos_dock_item_candidates)
        self.assertIs(capture_kwargs["default_running_apps_provider"], main.enumerate_active_vision_running_app_candidates)
        self.assertIs(capture_kwargs["default_interaction_runner"], main.run_active_vision_light_interaction)


if __name__ == "__main__":
    unittest.main()
