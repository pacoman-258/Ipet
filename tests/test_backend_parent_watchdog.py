from __future__ import annotations

import threading
import unittest
from unittest import mock

from backend import parent_watchdog


class ParentWatchdogTests(unittest.TestCase):
    def test_parent_pid_configuration_is_strict(self) -> None:
        self.assertEqual(
            parent_watchdog.configured_parent_pid({parent_watchdog.PARENT_PID_ENV: "4321"}),
            4321,
        )
        for value in ("", "abc", "0", "1", "-2"):
            self.assertIsNone(
                parent_watchdog.configured_parent_pid({parent_watchdog.PARENT_PID_ENV: value})
            )

    def test_parent_identity_change_is_detected(self) -> None:
        self.assertFalse(parent_watchdog.parent_has_changed(42, getppid=lambda: 42))
        self.assertTrue(parent_watchdog.parent_has_changed(42, getppid=lambda: 1))

    def test_watchdog_terminates_service_after_parent_disappears(self) -> None:
        terminated = threading.Event()
        with mock.patch.dict(
            parent_watchdog.os.environ,
            {parent_watchdog.PARENT_PID_ENV: "4321"},
        ):
            thread = parent_watchdog.start_parent_watchdog(
                interval_sec=0.01,
                getppid=lambda: 1,
                terminate=terminated.set,
            )

        self.assertIsNotNone(thread)
        thread.join(timeout=1)
        self.assertTrue(terminated.is_set())
        self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
