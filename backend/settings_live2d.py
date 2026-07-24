from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from body import live2d_assets


PREVIEW_COMMAND_TYPES = {"load_model", "play_motion", "play_expression"}


def _empty_model_metadata() -> dict[str, list[Any]]:
    return {
        "motions": [],
        "expressions": [],
        "motion_actions": [],
        "expression_actions": [],
    }


def model_metadata(model_path: Path) -> dict[str, list[Any]]:
    metadata = _empty_model_metadata()
    if not model_path.exists() or not model_path.is_file():
        return metadata

    try:
        model_json = json.loads(model_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        model_json = {}
    if not isinstance(model_json, dict):
        model_json = {}

    motion_groups = live2d_assets.extract_motion_groups(model_json) or live2d_assets.scan_motion_groups(model_path)
    expression_defs = live2d_assets.extract_expression_defs(model_json) or live2d_assets.scan_expression_defs(model_path)

    for group_name, entries in motion_groups.items():
        if not isinstance(entries, list):
            continue
        group = str(group_name or "").strip()
        if not group:
            continue
        for index, _entry in enumerate(entries):
            label = f"{group}[{index}]"
            metadata["motions"].append(label)
            metadata["motion_actions"].append({"group": group, "index": index, "label": label})

    for item in expression_defs:
        if not isinstance(item, dict):
            continue
        name = str(item.get("Name") or "").strip()
        if not name:
            continue
        metadata["expressions"].append(name)
        metadata["expression_actions"].append({"name": name, "label": name})

    return metadata


def list_local_models(*, root_dir: Path) -> list[dict[str, Any]]:
    model_root = Path(root_dir) / "model"
    if not model_root.exists() or not model_root.is_dir():
        return []

    models: list[dict[str, Any]] = []
    for path in sorted(model_root.rglob("*.model3.json"), key=lambda item: item.as_posix()):
        if not path.is_file() or path.name.endswith(".autogen.model3.json"):
            continue
        try:
            relative_path = path.relative_to(root_dir).as_posix()
        except ValueError:
            relative_path = str(path)
        metadata = model_metadata(path)
        models.append(
            {
                "path": relative_path,
                "label": f"{path.parent.name} / {path.name}",
                **metadata,
            }
        )
    return models


def preview_command(payload: dict[str, Any], *, root_dir: Path) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("preview payload must be an object")

    command_type = str(payload.get("type") or "").strip()
    if command_type not in PREVIEW_COMMAND_TYPES:
        raise ValueError("unsupported preview action")

    model_path_text = str(payload.get("model_path") or "").strip()
    model_path = live2d_assets.normalize_model_path(model_path_text, root_dir=root_dir) if model_path_text else ""
    command_payload: dict[str, Any] = {}
    if model_path:
        resolved_model_path = live2d_assets.resolve_model_path(model_path, root_dir=root_dir)
        if not resolved_model_path.exists() or not resolved_model_path.is_file():
            raise ValueError("model_path does not point to an existing model")
        command_payload["model_path"] = model_path

    if command_type == "load_model":
        if not model_path:
            raise ValueError("model_path is required")
    elif command_type == "play_motion":
        group = str(payload.get("group") or "").strip()
        if not group:
            raise ValueError("group is required")
        try:
            index = max(0, int(payload.get("index", 0)))
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid motion index") from exc
        command_payload.update({"group": group, "index": index})
    else:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("name is required")
        command_payload["name"] = name

    return {"type": command_type, "payload": command_payload}


__all__ = ["PREVIEW_COMMAND_TYPES", "list_local_models", "model_metadata", "preview_command"]
