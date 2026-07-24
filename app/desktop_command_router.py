from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Callable


def _identity_model_path(value: str) -> str:
    return value


def _default_directory_seed(value: str) -> str:
    return value


def _default_background_path(value: str) -> Path:
    return Path(value)


def _default_clean_text(value, *, max_length: int) -> str:
    return str(value or "").strip()[:max_length]


def _default_pick_directory(_host, _title: str, _start_dir: str) -> str:
    return ""


def _default_pick_image_file(_host, _title: str, _start_dir: str, _filter_text: str):
    return "", ""


def _not_configured_action(_payload):
    raise RuntimeError("desktop command action is not configured")


def _default_background_runner(task: Callable[[], None]) -> None:
    threading.Thread(target=task, name="ipet-ax-index-refresh", daemon=True).start()


class DesktopCommandRouter:
    def __init__(
        self,
        host,
        *,
        root_dir: Path,
        command_path: Path,
        default_response_path: Path,
        heartbeat_path: Path,
        normalize_model_path: Callable[[str], str] = _identity_model_path,
        resolve_desktop_directory_seed: Callable[[str], str] = _default_directory_seed,
        resolve_background_image_path: Callable[[str], Path] = _default_background_path,
        clean_vision_text: Callable[..., str] = _default_clean_text,
        pick_directory_func: Callable[..., str] = _default_pick_directory,
        pick_image_file_func: Callable[..., object] = _default_pick_image_file,
        execute_human_ops_click: Callable[[dict], dict[str, object]] = _not_configured_action,
        execute_human_ops_type_text: Callable[[dict], dict[str, object]] = _not_configured_action,
        execute_human_ops_launch_app: Callable[[dict], dict[str, object]] = _not_configured_action,
        execute_human_ops_key_press: Callable[[dict], dict[str, object]] = _not_configured_action,
        focus_target_application: Callable[[dict], dict[str, object]] = _not_configured_action,
        execute_human_ops_native_approval: Callable[[dict], dict[str, object]] = _not_configured_action,
        hide_window_for_desktop_click: Callable[[object], bool] = lambda _host: False,
        restore_window_after_desktop_click: Callable[[object, bool], None] = lambda _host, _was_hidden: None,
        capture_active_vision_frame_payload: Callable[..., dict] = _not_configured_action,
        accessibility_index_status: Callable[[], dict] = lambda: {},
        refresh_accessibility_index: Callable[[], dict] = lambda: {},
        background_runner: Callable[[Callable[[], None]], None] = _default_background_runner,
        sleep: Callable[[float], None] = time.sleep,
        write_response_func: Callable[[dict, str, dict[str, object] | None], None] | None = None,
        heartbeat_func: Callable[[], None] | None = None,
        dispatch_func: Callable[[dict], None] | None = None,
        time_module=time,
        os_module=os,
        print_func: Callable[[str], None] = print,
    ) -> None:
        self.host = host
        self.root_dir = Path(root_dir)
        self.command_path = Path(command_path)
        self.default_response_path = Path(default_response_path)
        self.heartbeat_path = Path(heartbeat_path)
        self.normalize_model_path = normalize_model_path
        self.resolve_desktop_directory_seed = resolve_desktop_directory_seed
        self.resolve_background_image_path = resolve_background_image_path
        self.clean_vision_text = clean_vision_text
        self.pick_directory_func = pick_directory_func
        self.pick_image_file_func = pick_image_file_func
        self.execute_human_ops_click = execute_human_ops_click
        self.execute_human_ops_type_text = execute_human_ops_type_text
        self.execute_human_ops_launch_app = execute_human_ops_launch_app
        self.execute_human_ops_key_press = execute_human_ops_key_press
        self.focus_target_application = focus_target_application
        self.execute_human_ops_native_approval = execute_human_ops_native_approval
        self.hide_window_for_desktop_click = hide_window_for_desktop_click
        self.restore_window_after_desktop_click = restore_window_after_desktop_click
        self.capture_active_vision_frame_payload = capture_active_vision_frame_payload
        self.accessibility_index_status = accessibility_index_status
        self.refresh_accessibility_index = refresh_accessibility_index
        self.background_runner = background_runner
        self.sleep = sleep
        self._write_response_func = write_response_func
        self._heartbeat_func = heartbeat_func
        self._dispatch_func = dispatch_func
        self.time_module = time_module
        self.os_module = os_module
        self.print_func = print_func

    def desktop_command_mtime_token(self):
        try:
            stat = self.command_path.stat()
        except Exception:
            return None
        return (stat.st_mtime_ns, stat.st_size)

    def write_desktop_host_heartbeat(self) -> None:
        if self._heartbeat_func is not None:
            self._heartbeat_func()
            return
        payload = {
            "pid": self.os_module.getpid(),
            "timestamp_ns": self.time_module.time_ns(),
        }
        try:
            self.heartbeat_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def response_path_for_command(self, command: dict) -> Path:
        payload = command.get("payload", {})
        if not isinstance(payload, dict):
            payload = {}
        response_path_text = str(
            payload.get("response_path")
            or command.get("response_path")
            or self.default_response_path
        ).strip()
        path = Path(response_path_text)
        if not path.is_absolute():
            path = self.root_dir / path
        try:
            return path.resolve()
        except Exception:
            return path

    def write_desktop_command_response(
        self,
        command: dict,
        status: str,
        result: dict[str, object] | None = None,
    ) -> None:
        if self._write_response_func is not None:
            self._write_response_func(command, status, result)
            return
        nonce = str(command.get("nonce") or "").strip()
        if not nonce:
            return
        response = {
            "nonce": nonce,
            "type": str(command.get("type") or "").strip(),
            "status": status,
            "ok": status == "success",
            "result": result or {},
            "timestamp_ns": self.time_module.time_ns(),
        }
        if status == "error" and not response["result"]:
            response["result"] = {"error": "desktop command failed"}
        response_path = self.response_path_for_command(command)
        try:
            response_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = response_path.with_name(f"{response_path.name}.tmp")
            tmp_path.write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp_path.replace(response_path)
        except Exception as exc:
            self.print_func(f"写入 desktop command 响应失败: {exc}")
        finally:
            self.write_desktop_host_heartbeat()

    def on_desktop_command_poll(self) -> None:
        token = self.desktop_command_mtime_token()
        if token is None or token == getattr(self.host, "_desktop_command_mtime", None):
            return
        setattr(self.host, "_desktop_command_mtime", token)
        try:
            command = json.loads(self.command_path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(command, dict):
            return
        nonce = str(command.get("nonce") or "").strip()
        if not nonce:
            return
        if nonce == getattr(self.host, "_last_desktop_command_nonce", ""):
            return
        setattr(self.host, "_last_desktop_command_nonce", nonce)
        self.write_desktop_host_heartbeat()
        if self._dispatch_func is not None:
            self._dispatch_func(command)
            return
        self.process_desktop_command(command)

    def process_desktop_command(self, command: dict) -> None:
        command_type = str(command.get("type") or "").strip()
        payload = command.get("payload", {})
        if not isinstance(payload, dict):
            payload = {}

        config = getattr(self.host, "config", None)
        if not isinstance(config, dict):
            config = {}
            setattr(self.host, "config", config)

        model_path = str(payload.get("model_path") or "").strip()
        if model_path:
            normalized = self.normalize_model_path(model_path)
            if normalized:
                config["model_path"] = normalized
                self.host.refresh_motion_list(prefer_reset=False)

        if command_type == "load_model":
            self.host.apply_config_to_web()
            self.write_desktop_command_response(command, "success", {"model_path": config.get("model_path", "")})
            return

        if command_type == "play_motion":
            group = str(payload.get("group") or "").strip()
            try:
                index = max(0, int(payload.get("index", 0)))
            except Exception:
                index = 0
            if group:
                self.host.play_motion(group, index, reload_model=bool(model_path))
                self.write_desktop_command_response(
                    command,
                    "success",
                    {"group": group, "index": index, "model_path": config.get("model_path", "")},
                )
            else:
                self.write_desktop_command_response(command, "error", {"error": "group is required"})
            return

        if command_type == "play_expression":
            name = str(payload.get("name") or "").strip()
            if name:
                self.host.play_expression(name, reload_model=bool(model_path))
                self.write_desktop_command_response(
                    command,
                    "success",
                    {"name": name, "model_path": config.get("model_path", "")},
                )
            else:
                self.write_desktop_command_response(command, "error", {"error": "name is required"})
            return

        if command_type == "pick_directory":
            self._process_pick_directory(command, payload)
            return

        if command_type == "pick_image_file":
            self._process_pick_image_file(command, payload)
            return

        if command_type == "human_ops_click":
            self._process_human_ops_click(command, payload)
            return

        if command_type == "human_ops_type_text":
            self._process_human_ops_type_text(command, payload)
            return

        if command_type == "human_ops_launch_app":
            self._process_human_ops_launch_app(command, payload)
            return

        if command_type == "human_ops_key_press":
            self._process_human_ops_key_press(command, payload)
            return

        if command_type == "human_ops_native_approval":
            self._process_human_ops_native_approval(command, payload)
            return

        if command_type == "active_vision_capture":
            self._process_active_vision_capture(command, payload, config)
            return

        if command_type == "accessibility_index_status":
            self._process_accessibility_index_status(command)
            return

        if command_type == "accessibility_index_refresh":
            self._process_accessibility_index_refresh(command)
            return

        self.write_desktop_command_response(command, "error", {"error": f"unsupported command: {command_type}"})
        self.write_desktop_host_heartbeat()

    def _process_pick_directory(self, command: dict, payload: dict) -> None:
        start_dir = self.resolve_desktop_directory_seed(str(payload.get("start_dir") or ""))
        try:
            self.host.raise_()
            self.host.activateWindow()
            selected = self.pick_directory_func(self.host, "选择目录", start_dir)
            if selected:
                self.write_desktop_command_response(
                    command,
                    "success",
                    {"directory": selected, "start_dir": start_dir},
                )
            else:
                self.write_desktop_command_response(
                    command,
                    "cancelled",
                    {"directory": "", "start_dir": start_dir},
                )
        except Exception as exc:
            self.write_desktop_command_response(
                command,
                "error",
                {"error": str(exc), "start_dir": start_dir},
            )
        self.write_desktop_host_heartbeat()

    def _process_pick_image_file(self, command: dict, payload: dict) -> None:
        start_path = str(payload.get("start_path") or payload.get("start_dir") or "").strip()
        seed_path = self.resolve_background_image_path(start_path) if start_path else self.root_dir
        start_dir = str(seed_path.parent if seed_path.exists() and seed_path.is_file() else seed_path)
        try:
            self.host.raise_()
            self.host.activateWindow()
            selected, _ = self.pick_image_file_func(
                self.host,
                "选择背景图片",
                start_dir,
                "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;All Files (*)",
            )
            if selected:
                self.write_desktop_command_response(
                    command,
                    "success",
                    {"path": selected, "start_path": start_path},
                )
            else:
                self.write_desktop_command_response(
                    command,
                    "cancelled",
                    {"path": "", "start_path": start_path},
                )
        except Exception as exc:
            self.write_desktop_command_response(
                command,
                "error",
                {"error": str(exc), "start_path": start_path},
            )
        self.write_desktop_host_heartbeat()

    def _process_human_ops_click(self, command: dict, payload: dict) -> None:
        try:
            focus = self.focus_target_application(payload)
            result = self.execute_human_ops_click(payload)
            self.write_desktop_command_response(command, "success", {**focus, **result})
        except Exception as exc:
            self.write_desktop_command_response(command, "error", {"error": str(exc)})
        self.write_desktop_host_heartbeat()

    def _process_human_ops_type_text(self, command: dict, payload: dict) -> None:
        try:
            focus = self.focus_target_application(payload)
            result = self.execute_human_ops_type_text(payload)
            self.write_desktop_command_response(command, "success", {**focus, **result})
        except Exception as exc:
            self.write_desktop_command_response(command, "error", {"error": str(exc)})
        self.write_desktop_host_heartbeat()

    def _process_human_ops_launch_app(self, command: dict, payload: dict) -> None:
        try:
            result = self.execute_human_ops_launch_app(payload)
            focus = self.focus_target_application({**payload, "target_app": result.get("app") or payload.get("app")})
            self.write_desktop_command_response(command, "success", {**result, **focus})
        except Exception as exc:
            self.write_desktop_command_response(command, "error", {"error": str(exc)})
        self.write_desktop_host_heartbeat()

    def _process_human_ops_key_press(self, command: dict, payload: dict) -> None:
        try:
            focus = self.focus_target_application(payload)
            result = self.execute_human_ops_key_press(payload)
            self.write_desktop_command_response(command, "success", {**focus, **result})
        except Exception as exc:
            self.write_desktop_command_response(command, "error", {"error": str(exc)})
        self.write_desktop_host_heartbeat()

    def _process_human_ops_native_approval(self, command: dict, payload: dict) -> None:
        try:
            result = self.execute_human_ops_native_approval(payload)
            if payload.get("notice_only") is not True and not bool(result.get("approved")):
                if hasattr(self.host, "show"):
                    self.host.show()
                if hasattr(self.host, "raise_"):
                    self.host.raise_()
                if hasattr(self.host, "activateWindow"):
                    self.host.activateWindow()
            self.write_desktop_command_response(command, "success", result)
        except Exception as exc:
            self.write_desktop_command_response(command, "error", {"error": str(exc)})
        self.write_desktop_host_heartbeat()

    def _process_active_vision_capture(self, command: dict, payload: dict, config: dict) -> None:
        try:
            target_app = str(payload.get("target_app") or "").strip()
            focus = self.focus_target_application(payload) if target_app else {}
            frame = self.capture_active_vision_frame_payload(self.host, payload, config.get("vision", {}))
            if focus and isinstance(frame, dict):
                frame["focus_verification"] = focus
            trace = frame.get("active_observation") if isinstance(frame, dict) and isinstance(frame.get("active_observation"), dict) else {}
            self.write_desktop_command_response(command, "success", {"frame": frame, "trace": trace})
        except Exception as exc:
            self.write_desktop_command_response(
                command,
                "error",
                {
                    "error": str(exc),
                    "trace": {
                        "status": "error",
                        "target_hint": self.clean_vision_text(payload.get("target_hint"), max_length=80),
                        "actions": [],
                        "unknowns": [str(exc)],
                    },
                },
            )
        self.write_desktop_host_heartbeat()

    def _process_accessibility_index_status(self, command: dict) -> None:
        try:
            self.write_desktop_command_response(command, "success", self.accessibility_index_status())
        except Exception as exc:
            self.write_desktop_command_response(command, "error", {"error": str(exc)})
        self.write_desktop_host_heartbeat()

    def _process_accessibility_index_refresh(self, command: dict) -> None:
        def refresh() -> None:
            try:
                self.write_desktop_command_response(command, "success", self.refresh_accessibility_index())
            except Exception as exc:
                self.write_desktop_command_response(command, "error", {"error": str(exc)})
            self.write_desktop_host_heartbeat()

        try:
            self.background_runner(refresh)
        except Exception as exc:
            self.write_desktop_command_response(command, "error", {"error": str(exc)})
            self.write_desktop_host_heartbeat()
