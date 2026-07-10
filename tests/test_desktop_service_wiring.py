from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from unittest import mock

from app.desktop_service_wiring import (
    DesktopServiceLifecycleWiringDependencies,
    create_desktop_service_lifecycle,
)


class _FakeLifecycle:
    def __init__(self, config, **kwargs) -> None:
        self.config = config
        self.kwargs = kwargs


class DesktopServiceWiringTests(unittest.TestCase):
    def test_create_desktop_service_lifecycle_transfers_all_dependencies(self) -> None:
        deps = DesktopServiceLifecycleWiringDependencies(
            root_dir=Path("/tmp/ipet"),
            default_backend_url="http://127.0.0.1:8008",
            default_asr_api_base_url="http://127.0.0.1:8012",
            local_api_token_env="IPET_LOCAL_API_TOKEN",
            local_api_token="secret-token",
            os_module=SimpleNamespace(name="posix"),
            subprocess_module=SimpleNamespace(name="subprocess"),
            requests_module=SimpleNamespace(name="requests"),
            threading_module=SimpleNamespace(name="threading"),
            time_module=SimpleNamespace(name="time"),
            sys_module=SimpleNamespace(name="sys"),
            print_func=lambda *_args, **_kwargs: None,
            is_backend_healthy=lambda _url: True,
            is_backend_live=lambda _url: True,
            is_asr_healthy=lambda _url: True,
            is_local_service_url=lambda _url: False,
            pick_backend_launch_url=lambda url: f"{url}/launch",
            parse_service_host_port=lambda _url, *, default_port: ("127.0.0.1", default_port),
            resolve_backend_python=lambda: "/fake/python",
            resolve_asr_python=lambda: "/fake/asr-python",
            service_log_path=lambda name: Path("/tmp") / f"{name}.log",
            truncate_service_log=lambda path: path,
            tail_service_log=lambda path, *, max_lines=20: path.read_text() if path.exists() else "",
        )
        config = {"chat": {"backend_url": "http://127.0.0.1:8008"}}

        with mock.patch("app.desktop_service_wiring.DesktopServiceLifecycle", side_effect=_FakeLifecycle) as factory:
            lifecycle = create_desktop_service_lifecycle(config, deps)

        self.assertIs(lifecycle.config, config)
        self.assertEqual(lifecycle.kwargs["root_dir"], deps.root_dir)
        self.assertEqual(lifecycle.kwargs["default_backend_url"], deps.default_backend_url)
        self.assertEqual(lifecycle.kwargs["default_asr_api_base_url"], deps.default_asr_api_base_url)
        self.assertEqual(lifecycle.kwargs["local_api_token_env"], deps.local_api_token_env)
        self.assertEqual(lifecycle.kwargs["local_api_token"], deps.local_api_token)
        factory.assert_called_once_with(
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
