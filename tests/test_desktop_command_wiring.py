from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app import desktop_command_wiring as wiring


class _FakeQFileDialog:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def getExistingDirectory(self, owner, title: str, start_dir: str) -> str:
        self.calls.append(("directory", owner, title, start_dir))
        return "/chosen/directory"

    def getOpenFileName(self, owner, title: str, start_dir: str, filter_text: str):
        self.calls.append(("image", owner, title, start_dir, filter_text))
        return ("/chosen/image.png", "PNG Files (*.png)")


class DesktopCommandWiringTests(unittest.TestCase):
    def test_create_router_passes_dependencies_and_qt_picker_adapters(self) -> None:
        owner = SimpleNamespace(name="owner")
        qfiledialog = _FakeQFileDialog()
        write_calls: list[tuple] = []
        heartbeat_calls: list[str] = []
        write_response_func = lambda *args: write_calls.append(args)
        heartbeat_func = lambda: heartbeat_calls.append("heartbeat")
        deps = wiring.DesktopCommandRouterWiringDependencies(
            root_dir=Path("/root"),
            command_path=Path("/root/command.json"),
            default_response_path=Path("/root/response.json"),
            heartbeat_path=Path("/root/heartbeat.json"),
            normalize_model_path=lambda value: f"normalized:{value}",
            resolve_desktop_directory_seed=lambda value: f"seed:{value}",
            resolve_background_image_path=lambda value: Path(f"/bg/{value}"),
            clean_vision_text=lambda value, *, max_length: f"clean:{value}:{max_length}",
            qfiledialog=qfiledialog,
            execute_human_ops_click=lambda payload: {"click": payload},
            execute_human_ops_type_text=lambda payload: {"type": payload},
            execute_human_ops_key_press=lambda payload: {"key": payload},
            hide_window_for_desktop_click=lambda host: True,
            restore_window_after_desktop_click=lambda host, was_hidden: None,
            capture_active_vision_frame_payload=lambda *args, **kwargs: {"frame": True},
            sleep=lambda seconds: None,
            time_module=SimpleNamespace(time_ns=lambda: 123),
            os_module=SimpleNamespace(getpid=lambda: 456),
            print_func=lambda message: None,
        )

        captured: dict[str, object] = {}

        def fake_desktop_command_router(owner_arg, **kwargs):
            captured["owner"] = owner_arg
            captured["kwargs"] = kwargs
            return SimpleNamespace(created=True, **kwargs)

        with mock.patch.object(wiring, "DesktopCommandRouter", side_effect=fake_desktop_command_router):
            router = wiring.create_desktop_command_router(
                owner,
                deps,
                write_response_func=write_response_func,
                heartbeat_func=heartbeat_func,
            )

        self.assertTrue(router.created)
        self.assertIs(captured["owner"], owner)
        kwargs = captured["kwargs"]
        self.assertIs(kwargs["root_dir"], deps.root_dir)
        self.assertIs(kwargs["command_path"], deps.command_path)
        self.assertIs(kwargs["default_response_path"], deps.default_response_path)
        self.assertIs(kwargs["heartbeat_path"], deps.heartbeat_path)
        self.assertIs(kwargs["normalize_model_path"], deps.normalize_model_path)
        self.assertIs(kwargs["resolve_desktop_directory_seed"], deps.resolve_desktop_directory_seed)
        self.assertIs(kwargs["resolve_background_image_path"], deps.resolve_background_image_path)
        self.assertIs(kwargs["clean_vision_text"], deps.clean_vision_text)
        self.assertIs(kwargs["execute_human_ops_click"], deps.execute_human_ops_click)
        self.assertIs(kwargs["execute_human_ops_type_text"], deps.execute_human_ops_type_text)
        self.assertIs(kwargs["execute_human_ops_key_press"], deps.execute_human_ops_key_press)
        self.assertIs(kwargs["hide_window_for_desktop_click"], deps.hide_window_for_desktop_click)
        self.assertIs(kwargs["restore_window_after_desktop_click"], deps.restore_window_after_desktop_click)
        self.assertIs(kwargs["capture_active_vision_frame_payload"], deps.capture_active_vision_frame_payload)
        self.assertIs(kwargs["sleep"], deps.sleep)
        self.assertIs(kwargs["time_module"], deps.time_module)
        self.assertIs(kwargs["os_module"], deps.os_module)
        self.assertIs(kwargs["print_func"], deps.print_func)
        self.assertIs(kwargs["write_response_func"], write_response_func)
        self.assertIs(kwargs["heartbeat_func"], heartbeat_func)

        directory_picker = kwargs["pick_directory_func"]
        image_picker = kwargs["pick_image_file_func"]
        self.assertEqual(directory_picker(owner, "Choose folder", "/start"), "/chosen/directory")
        self.assertEqual(
            image_picker(owner, "Choose image", "/start", "Images (*.png)"),
            ("/chosen/image.png", "PNG Files (*.png)"),
        )
        self.assertEqual(
            qfiledialog.calls,
            [
                ("directory", owner, "Choose folder", "/start"),
                ("image", owner, "Choose image", "/start", "Images (*.png)"),
            ],
        )

    def test_create_router_uses_explicit_picker_overrides_when_present(self) -> None:
        owner = SimpleNamespace(name="owner")
        deps = wiring.DesktopCommandRouterWiringDependencies(
            root_dir=Path("/root"),
            command_path=Path("/root/command.json"),
            default_response_path=Path("/root/response.json"),
            heartbeat_path=Path("/root/heartbeat.json"),
            normalize_model_path=lambda value: value,
            resolve_desktop_directory_seed=lambda value: value,
            resolve_background_image_path=lambda value: Path(value),
            clean_vision_text=lambda value, *, max_length: str(value),
            qfiledialog=_FakeQFileDialog(),
            execute_human_ops_click=lambda payload: payload,
            execute_human_ops_type_text=lambda payload: payload,
            execute_human_ops_key_press=lambda payload: payload,
            hide_window_for_desktop_click=lambda host: False,
            restore_window_after_desktop_click=lambda host, was_hidden: None,
            capture_active_vision_frame_payload=lambda *args, **kwargs: {},
            sleep=lambda seconds: None,
            time_module=SimpleNamespace(time_ns=lambda: 1),
            os_module=SimpleNamespace(getpid=lambda: 2),
            print_func=lambda message: None,
            pick_directory_func=lambda owner_arg, title, start_dir: "custom-directory",
            pick_image_file_func=lambda owner_arg, title, start_dir, filter_text: ("custom-image", "Custom"),
        )

        captured: dict[str, object] = {}

        def fake_desktop_command_router(owner_arg, **kwargs):
            captured["kwargs"] = kwargs
            return SimpleNamespace(created=True, **kwargs)

        with mock.patch.object(wiring, "DesktopCommandRouter", side_effect=fake_desktop_command_router):
            wiring.create_desktop_command_router(owner, deps)

        self.assertEqual(captured["kwargs"]["pick_directory_func"](owner, "title", "/start"), "custom-directory")
        self.assertEqual(
            captured["kwargs"]["pick_image_file_func"](owner, "title", "/start", "filters"),
            ("custom-image", "Custom"),
        )


if __name__ == "__main__":
    unittest.main()
