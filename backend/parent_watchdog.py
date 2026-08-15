from __future__ import annotations

import os
import signal
import threading
from collections.abc import Callable, Mapping


PARENT_PID_ENV = "IPET_DESKTOP_PARENT_PID"


def configured_parent_pid(environ: Mapping[str, str] | None = None) -> int | None:
    source = os.environ if environ is None else environ
    try:
        value = int(str(source.get(PARENT_PID_ENV) or "").strip())
    except (TypeError, ValueError):
        return None
    return value if value > 1 else None


def parent_has_changed(expected_pid: int, *, getppid: Callable[[], int] = os.getppid) -> bool:
    try:
        return int(getppid()) != int(expected_pid)
    except (OSError, TypeError, ValueError):
        return True


def start_parent_watchdog(
    *,
    interval_sec: float = 1.0,
    getppid: Callable[[], int] = os.getppid,
    terminate: Callable[[], None] | None = None,
) -> threading.Thread | None:
    """Stop a desktop-managed service after its owning desktop disappears."""
    expected_pid = configured_parent_pid()
    if expected_pid is None:
        return None

    def terminate_process() -> None:
        os.kill(os.getpid(), signal.SIGTERM)

    stop_process = terminate or terminate_process

    def monitor() -> None:
        while True:
            threading.Event().wait(max(0.1, float(interval_sec)))
            if parent_has_changed(expected_pid, getppid=getppid):
                stop_process()
                return

    thread = threading.Thread(target=monitor, name="ipet-parent-watchdog", daemon=True)
    thread.start()
    return thread
