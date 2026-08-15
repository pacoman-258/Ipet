from __future__ import annotations

import ast
import importlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import main


ROOT_DIR = Path(__file__).resolve().parents[1]


def _parse_source(relative_path: str) -> ast.Module:
    return ast.parse((ROOT_DIR / relative_path).read_text(encoding="utf-8"))


class _FakeProcess:
    pid = 4321
    returncode = None

    def __init__(self) -> None:
        self.terminated = False
        self.killed = False
        self.waits: list[float | int | None] = []

    def poll(self):
        return None

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout=None) -> None:
        self.waits.append(timeout)


class _FakeSubprocess:
    STDOUT = object()
    DEVNULL = object()
    CREATE_NO_WINDOW = 0x08000000
    CREATE_NEW_PROCESS_GROUP = 0x00000200

    def __init__(self) -> None:
        self.popen_calls: list[dict] = []
        self.run_calls: list[dict] = []
        self.process = _FakeProcess()

    def Popen(self, cmd, **kwargs):
        self.popen_calls.append({"cmd": list(cmd), **kwargs})
        return self.process

    def run(self, cmd, **kwargs):
        self.run_calls.append({"cmd": list(cmd), **kwargs})
        return SimpleNamespace(returncode=0)


class DesktopServiceLifecycleSplitTests(unittest.TestCase):
    def test_service_lifecycle_module_exists_without_importing_main(self) -> None:
        tree = _parse_source("app/service_lifecycle.py")

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.assertNotIn("main", {alias.name for alias in node.names})
            elif isinstance(node, ast.ImportFrom):
                self.assertNotEqual(node.module, "main")

        module = importlib.import_module("app.service_lifecycle")
        self.assertTrue(hasattr(module, "DesktopServiceLifecycle"))

    def test_main_imports_and_constructs_service_lifecycle(self) -> None:
        tree = _parse_source("main.py")

        imports_lifecycle = any(
            isinstance(node, ast.ImportFrom)
            and node.module == "app.desktop_service_wiring"
            and any(
                alias.name == "DesktopServiceLifecycleWiringDependencies"
                or alias.name == "create_desktop_service_lifecycle"
                for alias in node.names
            )
            for node in ast.walk(tree)
        )
        self.assertTrue(imports_lifecycle)

        desktop_pet = next(
            node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == "DesktopPet"
        )
        constructs_direct_lifecycle = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "DesktopServiceLifecycle"
            for node in ast.walk(desktop_pet)
        )
        self.assertFalse(constructs_direct_lifecycle)

        delegates_to_factory = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "create_desktop_service_lifecycle"
            for node in ast.walk(desktop_pet)
        )
        self.assertTrue(delegates_to_factory)

    def test_desktop_pet_service_methods_delegate_to_lifecycle(self) -> None:
        lifecycle = mock.Mock()
        lifecycle.backend_process = "backend-proc"
        lifecycle.backend_started_by_app = True
        lifecycle.asr_process = "asr-proc"
        lifecycle.asr_started_by_app = True
        host = SimpleNamespace(service_lifecycle=lifecycle)

        main.DesktopPet.ensure_backend_service(host)
        main.DesktopPet.ensure_asr_service(host)
        main.DesktopPet.start_qwen_tts_service_async(host)
        main.DesktopPet.request_asr_warmup(host)
        main.DesktopPet._start_asr_warmup_progress_monitor(host, "http://127.0.0.1:8008/")
        main.DesktopPet._run_asr_warmup_progress_monitor(host, "http://127.0.0.1:8008")
        main.DesktopPet._stop_managed_process(host, "proc", started_by_app=True)
        main.DesktopPet.stop_backend_service(host)
        main.DesktopPet.stop_asr_service(host)
        main.DesktopPet.stop_qwen_tts_service(host)

        lifecycle.ensure_backend_service.assert_called_once_with()
        lifecycle.ensure_asr_service.assert_called_once_with()
        lifecycle.start_qwen_tts_service_async.assert_called_once_with()
        lifecycle.request_asr_warmup.assert_called_once_with()
        lifecycle.start_asr_warmup_progress_monitor.assert_called_once_with("http://127.0.0.1:8008/")
        lifecycle.run_asr_warmup_progress_monitor.assert_called_once_with("http://127.0.0.1:8008")
        lifecycle.stop_managed_process.assert_called_once_with("proc", started_by_app=True)
        lifecycle.stop_backend_service.assert_called_once_with()
        lifecycle.stop_asr_service.assert_called_once_with()
        lifecycle.stop_qwen_tts_service.assert_called_once_with()

        self.assertEqual(main.DesktopPet.backend_process.__get__(host), "backend-proc")
        self.assertTrue(main.DesktopPet.backend_started_by_app.__get__(host))
        self.assertEqual(main.DesktopPet.asr_process.__get__(host), "asr-proc")
        self.assertTrue(main.DesktopPet.asr_started_by_app.__get__(host))

    def test_backend_launch_uses_injected_uvicorn_command_and_local_token(self) -> None:
        module = importlib.import_module("app.service_lifecycle")
        fake_subprocess = _FakeSubprocess()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = {"chat": {"backend_url": "http://127.0.0.1:8008"}}
            service = module.DesktopServiceLifecycle(
                config,
                root_dir=root,
                default_backend_url="http://127.0.0.1:8008",
                default_asr_api_base_url="http://127.0.0.1:8012",
                local_api_token_env="IPET_LOCAL_API_TOKEN",
                local_api_token="secret-token",
                subprocess_module=fake_subprocess,
                os_module=SimpleNamespace(name="posix", environ={"PATH": "/usr/bin"}),
                time_module=SimpleNamespace(sleep=lambda _seconds: None, perf_counter=lambda: 0.0),
                is_backend_healthy=lambda _url: False,
                is_backend_live=lambda _url: True,
                is_asr_healthy=lambda _url: False,
                is_local_service_url=lambda _url: True,
                pick_backend_launch_url=lambda url: url,
                parse_service_host_port=lambda _url, *, default_port: ("127.0.0.1", default_port),
                resolve_backend_python=lambda: "/fake/python",
                resolve_asr_python=lambda: "/fake/asr-python",
                service_log_path=lambda name: root / f"{name}.log",
                print_func=lambda *_args, **_kwargs: None,
            )

            service.ensure_backend_service()

        self.assertEqual(len(fake_subprocess.popen_calls), 1)
        call = fake_subprocess.popen_calls[0]
        self.assertEqual(
            call["cmd"],
            [
                "/fake/python",
                "-m",
                "uvicorn",
                "backend.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8008",
                "--log-level",
                "warning",
            ],
        )
        self.assertEqual(call["cwd"], str(root))
        self.assertEqual(call["env"]["IPET_LOCAL_API_TOKEN"], "secret-token")
        self.assertGreater(int(call["env"]["IPET_DESKTOP_PARENT_PID"]), 1)
        self.assertIs(service.backend_process, fake_subprocess.process)
        self.assertTrue(service.backend_started_by_app)

    def test_backend_publishes_actual_loopback_url_for_game_bridge(self) -> None:
        module = importlib.import_module("app.service_lifecycle")
        with tempfile.TemporaryDirectory() as tmp:
            discovery_path = Path(tmp) / "ipet-game-bridge.json"
            config = {"chat": {"backend_url": "http://127.0.0.1:8017"}}
            service = module.DesktopServiceLifecycle(
                config,
                root_dir=Path(tmp),
                default_backend_url="http://127.0.0.1:8008",
                default_asr_api_base_url="http://127.0.0.1:8012",
                local_api_token_env="IPET_LOCAL_API_TOKEN",
                local_api_token="secret-token",
                is_backend_healthy=lambda _url: True,
                is_backend_authorized=lambda _url: True,
                is_backend_live=lambda _url: True,
                is_local_service_url=lambda _url: True,
                game_bridge_discovery_path=discovery_path,
            )

            service.ensure_backend_service()

            self.assertEqual(
                json.loads(discovery_path.read_text(encoding="utf-8")),
                {"base_url": "http://127.0.0.1:8017", "token": "secret-token"},
            )

    def test_stale_local_backend_with_another_token_is_not_reused(self) -> None:
        module = importlib.import_module("app.service_lifecycle")
        fake_subprocess = _FakeSubprocess()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = {"chat": {"backend_url": "http://127.0.0.1:8009"}}
            service = module.DesktopServiceLifecycle(
                config,
                root_dir=root,
                default_backend_url="http://127.0.0.1:8008",
                default_asr_api_base_url="http://127.0.0.1:8012",
                local_api_token_env="IPET_LOCAL_API_TOKEN",
                local_api_token="new-token",
                subprocess_module=fake_subprocess,
                time_module=SimpleNamespace(sleep=lambda _seconds: None, perf_counter=lambda: 0.0),
                is_backend_healthy=lambda url: url.endswith(":8009"),
                is_backend_authorized=lambda _url: False,
                is_backend_live=lambda _url: True,
                is_asr_healthy=lambda _url: False,
                is_local_service_url=lambda _url: True,
                pick_backend_launch_url=lambda _url: "http://127.0.0.1:8010",
                parse_service_host_port=lambda _url, *, default_port: ("127.0.0.1", 8010),
                resolve_backend_python=lambda: "/fake/python",
                resolve_asr_python=lambda: "/fake/asr-python",
                service_log_path=lambda name: root / f"{name}.log",
                print_func=lambda *_args, **_kwargs: None,
            )

            service.ensure_backend_service()

        self.assertEqual(config["chat"]["backend_url"], "http://127.0.0.1:8010")
        self.assertEqual(fake_subprocess.popen_calls[0]["cmd"][-3:], ["8010", "--log-level", "warning"])

    def test_local_backend_reuse_probe_uses_the_process_token(self) -> None:
        module = importlib.import_module("app.service_lifecycle")
        requests_module = mock.Mock()
        requests_module.get.return_value = SimpleNamespace(status_code=200)
        service = module.DesktopServiceLifecycle(
            {},
            root_dir=ROOT_DIR,
            default_backend_url="http://127.0.0.1:8008",
            default_asr_api_base_url="http://127.0.0.1:8012",
            local_api_token_env="IPET_LOCAL_API_TOKEN",
            local_api_token="process-token",
            requests_module=requests_module,
        )

        self.assertTrue(service._backend_accepts_local_token("http://127.0.0.1:8009/"))
        requests_module.get.assert_called_once_with(
            "http://127.0.0.1:8009/api/vision/context",
            params={"include_image": "false", "lane": "active"},
            headers={"X-Ipet-Local-Token": "process-token"},
            timeout=0.75,
        )

    def test_asr_uses_local_backend_shortcut_without_launching_process(self) -> None:
        module = importlib.import_module("app.service_lifecycle")
        fake_subprocess = _FakeSubprocess()
        config = {
            "chat": {
                "backend_url": "http://127.0.0.1:8008",
                "asr": {"enabled": True, "api_base_url": "http://127.0.0.1:8012"},
            }
        }
        service = module.DesktopServiceLifecycle(
            config,
            root_dir=ROOT_DIR,
            default_backend_url="http://127.0.0.1:8008",
            default_asr_api_base_url="http://127.0.0.1:8012",
            local_api_token_env="IPET_LOCAL_API_TOKEN",
            local_api_token="secret-token",
            subprocess_module=fake_subprocess,
            is_backend_healthy=lambda _url: True,
            is_backend_authorized=lambda _url: True,
            is_backend_live=lambda _url: True,
            is_asr_healthy=lambda _url: False,
            is_local_service_url=lambda _url: True,
            resolve_backend_python=lambda: "/fake/python",
            resolve_asr_python=lambda: "/fake/asr-python",
            print_func=lambda *_args, **_kwargs: None,
        )

        service.ensure_asr_service()

        self.assertEqual(config["chat"]["asr"]["api_base_url"], "http://127.0.0.1:8008")
        self.assertEqual(fake_subprocess.popen_calls, [])
        self.assertIsNone(service.asr_process)
        self.assertFalse(service.asr_started_by_app)

    def test_qwen_tts_launches_without_waiting_for_model_readiness(self) -> None:
        module = importlib.import_module("app.service_lifecycle")
        fake_subprocess = _FakeSubprocess()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            qwen_root = root / "qwen"
            qwen_python = qwen_root / ".venv" / "bin" / "python"
            qwen_python.parent.mkdir(parents=True)
            qwen_python.touch()
            service = module.DesktopServiceLifecycle(
                {"chat": {}},
                root_dir=root,
                default_backend_url="http://127.0.0.1:8008",
                default_asr_api_base_url="http://127.0.0.1:8012",
                local_api_token_env="IPET_LOCAL_API_TOKEN",
                local_api_token="secret-token",
                subprocess_module=fake_subprocess,
                os_module=SimpleNamespace(name="posix", environ={}),
                print_func=lambda *_args, **_kwargs: None,
            )
            with mock.patch.object(module, "QWEN_TTS_PROJECT_DIR", qwen_root), mock.patch.object(
                module, "qwen_tts_python", return_value=qwen_python
            ), mock.patch.object(service, "_qwen_tts_is_healthy", return_value=False):
                service.start_qwen_tts_service_async()

        call = fake_subprocess.popen_calls[0]
        self.assertEqual(call["cwd"], str(root))
        self.assertEqual(call["cmd"][:3], [service.sys.executable, "-m", "app.managed_child_service"])
        self.assertEqual(call["cmd"][-10:-6], [str(qwen_python), "-m", "uvicorn", "app:app"])
        self.assertIn("--parent-pid", call["cmd"])
        self.assertEqual(call["cmd"][call["cmd"].index("--cwd") + 1], str(qwen_root))
        self.assertIs(service.qwen_tts_process, fake_subprocess.process)
        self.assertTrue(service.qwen_tts_started_by_app)


if __name__ == "__main__":
    unittest.main()
