from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.desktop_command_router import DesktopCommandRouter


@dataclass(frozen=True, slots=True)
class DesktopCommandRouterWiringDependencies:
    root_dir: Path
    command_path: Path
    default_response_path: Path
    heartbeat_path: Path
    normalize_model_path: Callable[[str], str]
    resolve_desktop_directory_seed: Callable[[str], str]
    resolve_background_image_path: Callable[[str], Path]
    clean_vision_text: Callable[..., str]
    qfiledialog: object
    execute_human_ops_click: Callable[[dict | None], dict[str, object]]
    execute_human_ops_type_text: Callable[[dict | None], dict[str, object]]
    execute_human_ops_key_press: Callable[[dict | None], dict[str, object]]
    hide_window_for_desktop_click: Callable[[object], bool]
    restore_window_after_desktop_click: Callable[[object, bool], None]
    capture_active_vision_frame_payload: Callable[..., dict]
    sleep: Callable[[float], None]
    time_module: object
    os_module: object
    print_func: Callable[[str], None]
    pick_directory_func: Callable[..., str] | None = None
    pick_image_file_func: Callable[..., object] | None = None


def _pick_directory_with_qfiledialog(qfiledialog: object) -> Callable[..., str]:
    def pick_directory(owner, title: str, start_dir: str) -> str:
        return qfiledialog.getExistingDirectory(owner, title, start_dir)

    return pick_directory


def _pick_image_file_with_qfiledialog(qfiledialog: object) -> Callable[..., object]:
    def pick_image_file(owner, title: str, start_dir: str, filter_text: str):
        return qfiledialog.getOpenFileName(owner, title, start_dir, filter_text)

    return pick_image_file


def create_desktop_command_router(
    owner,
    deps: DesktopCommandRouterWiringDependencies,
    write_response_func=None,
    heartbeat_func=None,
):
    pick_directory_func = deps.pick_directory_func or _pick_directory_with_qfiledialog(deps.qfiledialog)
    pick_image_file_func = deps.pick_image_file_func or _pick_image_file_with_qfiledialog(deps.qfiledialog)
    return DesktopCommandRouter(
        owner,
        root_dir=deps.root_dir,
        command_path=deps.command_path,
        default_response_path=deps.default_response_path,
        heartbeat_path=deps.heartbeat_path,
        normalize_model_path=deps.normalize_model_path,
        resolve_desktop_directory_seed=deps.resolve_desktop_directory_seed,
        resolve_background_image_path=deps.resolve_background_image_path,
        clean_vision_text=deps.clean_vision_text,
        pick_directory_func=pick_directory_func,
        pick_image_file_func=pick_image_file_func,
        execute_human_ops_click=deps.execute_human_ops_click,
        execute_human_ops_type_text=deps.execute_human_ops_type_text,
        execute_human_ops_key_press=deps.execute_human_ops_key_press,
        hide_window_for_desktop_click=deps.hide_window_for_desktop_click,
        restore_window_after_desktop_click=deps.restore_window_after_desktop_click,
        capture_active_vision_frame_payload=deps.capture_active_vision_frame_payload,
        sleep=deps.sleep,
        write_response_func=write_response_func,
        heartbeat_func=heartbeat_func,
        time_module=deps.time_module,
        os_module=deps.os_module,
        print_func=deps.print_func,
    )
