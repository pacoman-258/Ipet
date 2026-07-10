from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Callable

from app.service_lifecycle import DesktopServiceLifecycle


@dataclass(frozen=True)
class DesktopServiceLifecycleWiringDependencies:
    root_dir: Path
    default_backend_url: str
    default_asr_api_base_url: str
    local_api_token_env: str
    local_api_token: str
    os_module: ModuleType
    subprocess_module: ModuleType
    requests_module: ModuleType
    threading_module: ModuleType
    time_module: ModuleType
    sys_module: ModuleType
    print_func: Callable[..., None]
    is_backend_healthy: Callable[[str], bool]
    is_backend_live: Callable[[str], bool]
    is_asr_healthy: Callable[[str], bool]
    is_local_service_url: Callable[[str], bool]
    pick_backend_launch_url: Callable[[str], str]
    parse_service_host_port: Callable[..., tuple[str, int]]
    resolve_backend_python: Callable[[], str]
    resolve_asr_python: Callable[[], str]
    service_log_path: Callable[[str], Path]
    truncate_service_log: Callable[[Path], Path]
    tail_service_log: Callable[..., str]


def create_desktop_service_lifecycle(
    config: dict,
    deps: DesktopServiceLifecycleWiringDependencies,
) -> DesktopServiceLifecycle:
    return DesktopServiceLifecycle(
        config,
        root_dir=deps.root_dir,
        default_backend_url=deps.default_backend_url,
        default_asr_api_base_url=deps.default_asr_api_base_url,
        local_api_token_env=deps.local_api_token_env,
        local_api_token=deps.local_api_token,
        os_module=deps.os_module,
        subprocess_module=deps.subprocess_module,
        requests_module=deps.requests_module,
        threading_module=deps.threading_module,
        time_module=deps.time_module,
        sys_module=deps.sys_module,
        print_func=deps.print_func,
        is_backend_healthy=deps.is_backend_healthy,
        is_backend_live=deps.is_backend_live,
        is_asr_healthy=deps.is_asr_healthy,
        is_local_service_url=deps.is_local_service_url,
        pick_backend_launch_url=deps.pick_backend_launch_url,
        parse_service_host_port=deps.parse_service_host_port,
        resolve_backend_python=deps.resolve_backend_python,
        resolve_asr_python=deps.resolve_asr_python,
        service_log_path=deps.service_log_path,
        truncate_service_log=deps.truncate_service_log,
        tail_service_log=deps.tail_service_log,
    )
