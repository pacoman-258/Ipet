from __future__ import annotations

import threading
import unittest

from app import managed_child_service


class _FakeProcess:
    def __init__(self, returncode=None) -> None:
        self.returncode = returncode

    def poll(self):
        return self.returncode


class ManagedChildServiceTests(unittest.TestCase):
    def test_parent_identity_change_is_detected(self) -> None:
        self.assertFalse(managed_child_service.parent_has_changed(42, getppid=lambda: 42))
        self.assertTrue(managed_child_service.parent_has_changed(42, getppid=lambda: 1))

    def test_parent_loss_terminates_the_child(self) -> None:
        process = _FakeProcess()
        terminated = []
        result = managed_child_service.monitor_child(
            process,
            42,
            threading.Event(),
            getppid=lambda: 1,
            terminate=terminated.append,
            interval_sec=0.01,
        )

        self.assertEqual(result, 0)
        self.assertEqual(terminated, [process])

    def test_child_exit_code_is_forwarded(self) -> None:
        process = _FakeProcess(returncode=7)
        result = managed_child_service.monitor_child(process, 42, threading.Event())
        self.assertEqual(result, 7)


if __name__ == "__main__":
    unittest.main()
