from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from time import time
from typing import Any, MutableMapping


class TaskStopped(asyncio.CancelledError):
    """Raised at safe boundaries after a user has stopped a task."""


@dataclass
class TaskRecord:
    task_id: str
    session_id: str
    state: str = "running"
    completed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    uncertain: list[str] = field(default_factory=list)
    tasks: set[asyncio.Task[Any]] = field(default_factory=set, repr=False)
    atomic_tasks: set[asyncio.Task[Any]] = field(default_factory=set, repr=False)
    updated_at: float = field(default_factory=time)

    def payload(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "session_id": self.session_id,
            "state": self.state,
            "completed": list(self.completed),
            "not_executed": list(self.skipped),
            "uncertain": list(self.uncertain),
        }


class LocalTaskControl:
    """Process-local, network-independent authority over active task chains."""

    def __init__(self) -> None:
        self._records: dict[str, TaskRecord] = {}

    def start(self, task_id: str, session_id: str) -> TaskRecord:
        record = TaskRecord(task_id=task_id, session_id=session_id)
        self._records[task_id] = record
        while len(self._records) > 128:
            self._records.pop(next(iter(self._records)))
        return record

    def get(self, task_id: str) -> TaskRecord | None:
        return self._records.get(task_id)

    def check(self, task_id: str, *, next_action: str = "") -> None:
        record = self.get(task_id)
        if record is None or record.state == "running":
            return
        if next_action and next_action not in record.skipped:
            record.skipped.append(next_action)
        raise TaskStopped()

    def bind_current_task(self, task_id: str) -> None:
        self.bind_task(task_id, asyncio.current_task())

    def bind_task(self, task_id: str, task: asyncio.Task[Any] | None) -> None:
        record = self.get(task_id)
        if record is None or task is None:
            return
        record.tasks.add(task)

        def discard(done_task: asyncio.Task[Any]) -> None:
            current = self.get(task_id)
            if current is not None:
                current.tasks.discard(done_task)
                current.atomic_tasks.discard(done_task)

        task.add_done_callback(discard)

    def enter_atomic(self, task_id: str) -> None:
        record = self.get(task_id)
        task = asyncio.current_task()
        if record is not None and task is not None:
            record.atomic_tasks.add(task)

    def exit_atomic(self, task_id: str) -> None:
        record = self.get(task_id)
        task = asyncio.current_task()
        if record is not None and task is not None:
            record.atomic_tasks.discard(task)

    def complete_step(self, task_id: str, description: str) -> None:
        record = self.get(task_id)
        if record is not None and description and description not in record.completed:
            record.completed.append(description)

    def mark_uncertain(self, task_id: str, description: str) -> None:
        record = self.get(task_id)
        if record is not None and description and description not in record.uncertain:
            record.uncertain.append(description)

    def stop(
        self,
        task_id: str,
        pending_proposals: MutableMapping[str, dict[str, Any]],
    ) -> dict[str, Any]:
        record = self.get(task_id)
        if record is None:
            record = self.start(task_id, "")
        record.state = "stopping"
        record.updated_at = time()
        for task in tuple(record.tasks):
            if not task.done() and task not in record.atomic_tasks:
                task.cancel()
        for proposal in pending_proposals.values():
            if str(proposal.get("task_id") or "") != task_id or proposal.get("status") != "pending":
                continue
            proposal["status"] = "invalidated_by_stop"
            record.skipped.append("待审批动作")
        record.state = "stopped"
        record.updated_at = time()
        return record.payload()


TASK_CONTROL = LocalTaskControl()
