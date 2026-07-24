from __future__ import annotations

import asyncio
import unittest

from backend.task_control import LocalTaskControl, TaskStopped


class LocalTaskControlTests(unittest.TestCase):
    def test_stop_cancels_bound_non_atomic_task(self) -> None:
        async def scenario() -> bool:
            control = LocalTaskControl()
            control.start("task-a", "session")
            waiter = asyncio.create_task(asyncio.Event().wait())
            control.get("task-a").tasks.add(waiter)
            control.stop("task-a", {})
            await asyncio.sleep(0)
            return waiter.cancelled()

        self.assertTrue(asyncio.run(scenario()))

    def test_stop_does_not_cancel_in_flight_atomic_task(self) -> None:
        async def scenario() -> tuple[bool, bool]:
            control = LocalTaskControl()
            control.start("task-a", "session")
            waiter = asyncio.create_task(asyncio.Event().wait())
            record = control.get("task-a")
            record.tasks.add(waiter)
            record.atomic_tasks.add(waiter)
            control.stop("task-a", {})
            pending = not waiter.done()
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)
            return pending, waiter.cancelled()

        self.assertEqual(asyncio.run(scenario()), (True, True))

    def test_stop_invalidates_only_same_task_pending_proposals(self) -> None:
        control = LocalTaskControl()
        control.start("task-a", "session")
        pending = {
            "a": {"task_id": "task-a", "status": "pending"},
            "b": {"task_id": "task-b", "status": "pending"},
        }

        result = control.stop("task-a", pending)

        self.assertEqual(result["state"], "stopped")
        self.assertEqual(pending["a"]["status"], "invalidated_by_stop")
        self.assertEqual(pending["b"]["status"], "pending")
        with self.assertRaises(TaskStopped):
            control.check("task-a", next_action="Brain 调用")
        self.assertIn("Brain 调用", control.get("task-a").skipped)

    def test_stop_names_full_authorization_proposal_as_not_executed(self) -> None:
        control = LocalTaskControl()
        control.start("task-full", "session")
        pending = {
            "full": {
                "task_id": "task-full",
                "status": "pending",
                "authorization_mode": "full",
            }
        }

        result = control.stop("task-full", pending)

        self.assertEqual(result["not_executed"], ["待执行动作"])
        self.assertEqual(pending["full"]["status"], "invalidated_by_stop")

    def test_completed_and_uncertain_actions_are_reported_without_rollback(self) -> None:
        control = LocalTaskControl()
        control.start("task-a", "session")
        control.complete_step("task-a", "已执行：打开应用")
        control.mark_uncertain("task-a", "原子动作可能已经发生：发送按键")

        result = control.stop("task-a", {})

        self.assertEqual(result["completed"], ["已执行：打开应用"])
        self.assertEqual(result["uncertain"], ["原子动作可能已经发生：发送按键"])
