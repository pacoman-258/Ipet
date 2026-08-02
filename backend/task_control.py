from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from time import time
from typing import Any, MutableMapping


class TaskStopped(asyncio.CancelledError):
    """Raised at safe boundaries after a user has stopped a task."""


class TaskBudgetExhausted(RuntimeError):
    """Raised before an expensive task phase would exceed its shared budget."""


DEFAULT_TASK_BUDGET = 10
MAX_TASK_BUDGET = 20


@dataclass
class TaskRecord:
    task_id: str
    session_id: str
    state: str = "running"
    completed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    uncertain: list[str] = field(default_factory=list)
    inference_routes: list[dict[str, Any]] = field(default_factory=list)
    budget_total: int = DEFAULT_TASK_BUDGET
    budget_remaining: int = DEFAULT_TASK_BUDGET
    budget_trace: list[dict[str, Any]] = field(default_factory=list)
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
            "budget_total": self.budget_total,
            "budget_remaining": self.budget_remaining,
        }


class LocalTaskControl:
    """Process-local, network-independent authority over active task chains."""

    def __init__(self) -> None:
        self._records: dict[str, TaskRecord] = {}

    def start(
        self,
        task_id: str,
        session_id: str,
        *,
        budget_total: int = DEFAULT_TASK_BUDGET,
    ) -> TaskRecord:
        total = self._bounded_budget(budget_total)
        record = TaskRecord(
            task_id=task_id,
            session_id=session_id,
            budget_total=total,
            budget_remaining=total,
        )
        self._records[task_id] = record
        while len(self._records) > 128:
            self._records.pop(next(iter(self._records)))
        return record

    def get(self, task_id: str) -> TaskRecord | None:
        return self._records.get(task_id)

    @staticmethod
    def _bounded_budget(value: object) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = DEFAULT_TASK_BUDGET
        return max(1, min(MAX_TASK_BUDGET, parsed))

    def remaining_budget(self, task_id: str) -> int:
        record = self.get(task_id)
        return max(0, int(record.budget_remaining)) if record is not None else 0

    def consume_budget(self, task_id: str, phase: str) -> bool:
        record = self.get(task_id)
        if record is None or record.budget_remaining <= 0:
            return False
        record.budget_remaining -= 1
        record.budget_trace.append(
            {
                "phase": str(phase or "step")[:80],
                "remaining": record.budget_remaining,
            }
        )
        del record.budget_trace[:-MAX_TASK_BUDGET]
        record.updated_at = time()
        return True

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

    def learn_observation_route(
        self,
        task_id: str,
        observation: dict[str, Any] | None,
    ) -> dict[str, Any]:
        result = dict(observation) if isinstance(observation, dict) else {}
        record = self.get(task_id)
        if record is None:
            return result
        route = (
            result.get("route_decision")
            if isinstance(result.get("route_decision"), dict)
            else {}
        )
        if route:
            learned = {
                "selected": str(route.get("selected") or "")[:40],
                "reason": str(route.get("reason") or "")[:120],
                "outcome": str(route.get("outcome") or "")[:40],
                "intent": str(route.get("intent") or "")[:40],
                "target_app": str(route.get("target_app") or "")[:160],
                "attempted": [
                    str(item or "")[:40]
                    for item in (
                        route.get("attempted")
                        if isinstance(route.get("attempted"), list)
                        else []
                    )
                    if str(item or "").strip()
                ][:6],
                "query_fingerprint": str(
                    route.get("query_fingerprint") or ""
                )[:32],
                "ax_insufficiency_reason": str(
                    route.get("ax_insufficiency_reason") or ""
                )[:120],
                "repeat_count": 1,
            }
            signature = tuple(
                learned.get(key)
                for key in (
                    "selected",
                    "reason",
                    "outcome",
                    "intent",
                    "target_app",
                    "query_fingerprint",
                    "ax_insufficiency_reason",
                )
            )
            previous = record.inference_routes[-1] if record.inference_routes else {}
            previous_signature = tuple(
                previous.get(key)
                for key in (
                    "selected",
                    "reason",
                    "outcome",
                    "intent",
                    "target_app",
                    "query_fingerprint",
                    "ax_insufficiency_reason",
                )
            )
            if previous and previous_signature == signature:
                learned["repeat_count"] = min(
                    99,
                    max(1, int(previous.get("repeat_count") or 1)) + 1,
                )
                record.inference_routes[-1] = learned
            else:
                record.inference_routes.append(learned)
                del record.inference_routes[:-12]
            record.updated_at = time()
        if record.inference_routes:
            result["route_history"] = [
                dict(item)
                for item in record.inference_routes[-4:]
            ]
        return result

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
            skipped = "待执行动作" if proposal.get("authorization_mode") == "full" else "待审批动作"
            record.skipped.append(skipped)
        record.state = "stopped"
        record.updated_at = time()
        return record.payload()


TASK_CONTROL = LocalTaskControl()
