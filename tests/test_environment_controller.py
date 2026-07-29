from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from body.environment_controller import EnvironmentController, desktop_metadata_payload, read_idle_seconds


class _Signal:
    def __init__(self) -> None:
        self.callback = None

    def connect(self, callback) -> None:
        self.callback = callback


class _Timer:
    def __init__(self, _parent=None) -> None:
        self.timeout = _Signal()
        self.started = []
        self.stopped = 0

    def start(self, value) -> None:
        self.started.append(value)

    def stop(self) -> None:
        self.stopped += 1


class EnvironmentControllerTests(unittest.TestCase):
    def test_metadata_payload_never_forwards_visible_text(self) -> None:
        observation_configs = []
        payload = desktop_metadata_payload(
            {"include_window_titles": False},
            observation_provider=lambda config: observation_configs.append(config) or {
                "foreground_app": "Editor",
                "window_title": "secret title",
                "visible_text": ["raw screen text"],
                "change_summary": "raw summary",
            },
            idle_provider=lambda: 42,
        )
        self.assertEqual(payload, {"event_type": "desktop_state", "foreground_app": "Editor", "idle_seconds": 42})
        self.assertEqual(observation_configs, [{"include_ui_metadata": True}])

    def test_window_title_is_only_sent_after_explicit_opt_in(self) -> None:
        payload = desktop_metadata_payload(
            {"include_window_titles": True},
            observation_provider=lambda _config: {"foreground_app": "Editor", "window_title": "Tests"},
            idle_provider=lambda: 0,
        )
        self.assertEqual(payload["window_title"], "Tests")

    def test_macos_idle_time_uses_nanoseconds(self) -> None:
        runner = lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, '"HIDIdleTime" = 9000000000', "")
        self.assertEqual(read_idle_seconds(platform_name="darwin", runner=runner), 9)

    def test_controller_stops_when_disabled_and_posts_when_active(self) -> None:
        timer = _Timer()
        posts = []
        controller = EnvironmentController(
            timer_factory=lambda _parent: timer,
            metadata_provider=lambda _config: {"foreground_app": "Editor", "visible_text": ["do not send"]},
            idle_provider=lambda: 3,
            task_runner=lambda task: task(),
            post_event=lambda url, payload, timeout: posts.append((url, payload, timeout)),
        )
        controller.apply_config({"environment": {"mode": "off"}})
        self.assertGreater(timer.stopped, 0)
        controller.apply_config(
            {
                "environment": {"mode": "active", "metadata_enabled": True, "sensor_interval_sec": 7},
                "chat": {"backend_url": "http://127.0.0.1:9000"},
            }
        )
        self.assertEqual(timer.started[-1], 7000)
        controller.collect_once()
        self.assertEqual(posts[0][0], "http://127.0.0.1:9000/api/environment/events")
        self.assertNotIn("visible_text", posts[0][1])

    def test_repeated_identical_post_error_is_logged_once_until_recovery(self) -> None:
        timer = _Timer()
        outcomes = [RuntimeError("403"), RuntimeError("403"), None, RuntimeError("403")]

        def post_event(_url, _payload, _timeout):
            outcome = outcomes.pop(0)
            if outcome is not None:
                raise outcome

        controller = EnvironmentController(
            timer_factory=lambda _parent: timer,
            metadata_provider=lambda _config: {"foreground_app": "Editor"},
            idle_provider=lambda: 0,
            task_runner=lambda task: task(),
            post_event=post_event,
        )
        controller.apply_config({"environment": {"mode": "active", "metadata_enabled": True}})
        with mock.patch("builtins.print") as print_mock:
            for _ in range(4):
                controller.collect_once()

        self.assertEqual(print_mock.call_count, 2)


if __name__ == "__main__":
    unittest.main()
