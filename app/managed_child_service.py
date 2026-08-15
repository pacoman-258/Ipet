from __future__ import annotations

import argparse
import os
import signal
import subprocess
import threading
from collections.abc import Callable, Sequence


def parent_has_changed(parent_pid: int, *, getppid: Callable[[], int] = os.getppid) -> bool:
    try:
        return int(getppid()) != int(parent_pid)
    except (OSError, TypeError, ValueError):
        return True


def terminate_child(process: subprocess.Popen, *, timeout_sec: float = 2.0) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=timeout_sec)
    except ProcessLookupError:
        return


def monitor_child(
    process: subprocess.Popen,
    parent_pid: int,
    stop_event: threading.Event,
    *,
    getppid: Callable[[], int] = os.getppid,
    terminate: Callable[[subprocess.Popen], None] = terminate_child,
    interval_sec: float = 0.5,
) -> int:
    while process.poll() is None:
        if stop_event.is_set() or parent_has_changed(parent_pid, getppid=getppid):
            terminate(process)
            return 0
        stop_event.wait(max(0.1, float(interval_sec)))
    return int(process.returncode or 0)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one service for the lifetime of its desktop parent.")
    parser.add_argument("--parent-pid", required=True, type=int)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    if args.parent_pid <= 1 or not command or parent_has_changed(args.parent_pid):
        return 2

    stop_event = threading.Event()
    previous_handlers: dict[int, object] = {}

    def request_stop(_signum, _frame) -> None:
        stop_event.set()

    for signum in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[signum] = signal.signal(signum, request_stop)

    process = subprocess.Popen(command, cwd=args.cwd)
    try:
        return monitor_child(process, args.parent_pid, stop_event)
    finally:
        terminate_child(process)
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())
