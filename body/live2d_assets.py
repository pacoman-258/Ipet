from __future__ import annotations

import json
import re
from pathlib import Path


AUTOGEN_MODEL_SUFFIX = ".autogen.model3.json"


def find_default_model(root_dir: Path) -> str:
    root = Path(root_dir)
    preferred = root / "model" / "hiyori_free_zh" / "runtime" / "hiyori_free_t08.model3.json"
    if preferred.exists():
        return preferred.relative_to(root).as_posix()

    for candidate in (root / "model").rglob("*.model3.json"):
        try:
            return candidate.relative_to(root).as_posix()
        except ValueError:
            return str(candidate)

    return ""


def canonicalize_model_source_path(
    model_path: Path,
    *,
    autogen_model_suffix: str = AUTOGEN_MODEL_SUFFIX,
) -> Path:
    original = model_path
    candidate = model_path
    while candidate.name.endswith(autogen_model_suffix):
        source_name = f"{candidate.name[: -len(autogen_model_suffix)]}.json"
        source_path = candidate.with_name(source_name)
        if source_path.exists():
            candidate = source_path
            continue
        return original
    return candidate


def resolve_model_path(path_text: str, *, root_dir: Path) -> Path:
    model_path = Path(path_text)
    root = Path(root_dir)
    if not model_path.is_absolute():
        model_path = root / model_path
    try:
        resolved = model_path.resolve()
    except Exception:
        resolved = model_path
    return canonicalize_model_source_path(resolved)


def normalize_model_path(path_text: str, *, root_dir: Path) -> str:
    if not path_text:
        return ""

    root = Path(root_dir)
    resolved = resolve_model_path(path_text, root_dir=root)
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return str(resolved)


def resolve_background_image_path(path_text: str, *, root_dir: Path) -> Path:
    image_path = Path(str(path_text or "").strip())
    root = Path(root_dir)
    if not str(image_path):
        return root
    if not image_path.is_absolute():
        image_path = root / image_path
    try:
        return image_path.resolve()
    except Exception:
        return image_path


def extract_motion_groups(model_json: dict) -> dict:
    groups = model_json.get("FileReferences", {}).get("Motions")
    if isinstance(groups, dict):
        return groups
    groups = model_json.get("Motions", {})
    return groups if isinstance(groups, dict) else {}


def infer_motion_group(file_name: str) -> str:
    name = file_name
    if name.lower().endswith(".motion3.json"):
        name = name[: -len(".motion3.json")]
    token = re.split(r"[\d_\-\s]+", name.strip())[0]
    if not token:
        return "Auto"
    mapping = {
        "idle": "Idle",
        "tap": "Tap",
        "flick": "Flick",
    }
    return mapping.get(token.lower(), token[:1].upper() + token[1:])


def resolve_desktop_directory_seed(start_dir_text: str, *, root_dir: Path) -> str:
    text = str(start_dir_text or "").strip()
    root = Path(root_dir)
    if not text:
        return str(root)
    path = Path(text)
    if not path.is_absolute():
        path = root / path
    try:
        resolved = path.resolve()
    except Exception:
        resolved = path
    if resolved.exists() and resolved.is_dir():
        return str(resolved)
    return str(root)


def scan_motion_groups(model_path: Path) -> dict:
    model_dir = model_path.parent
    motion_files = sorted(model_dir.rglob("*.motion3.json"))
    groups: dict[str, list[dict]] = {}
    for file_path in motion_files:
        try:
            rel = file_path.relative_to(model_dir).as_posix()
        except ValueError:
            rel = file_path.name
        group = infer_motion_group(file_path.name)
        groups.setdefault(group, []).append({"File": rel})
    return groups


def extract_expression_defs(model_json: dict) -> list[dict]:
    exprs = model_json.get("FileReferences", {}).get("Expressions")
    return exprs if isinstance(exprs, list) else []


def scan_expression_defs(model_path: Path) -> list[dict]:
    model_dir = model_path.parent
    expr_files = sorted(model_dir.rglob("*.exp3.json"))
    defs: list[dict] = []
    for file_path in expr_files:
        try:
            rel = file_path.relative_to(model_dir).as_posix()
        except ValueError:
            rel = file_path.name
        name = file_path.name
        if name.lower().endswith(".exp3.json"):
            name = name[: -len(".exp3.json")]
        defs.append({"Name": name, "File": rel})
    return defs


def extract_lipsync_meta(model_json: dict) -> dict:
    controllers = model_json.get("Controllers", {})
    if not isinstance(controllers, dict):
        controllers = {}

    gain = 1.0
    lipsync_cfg = controllers.get("LipSync", {})
    if isinstance(lipsync_cfg, dict):
        try:
            gain = max(0.25, float(lipsync_cfg.get("Gain", 1.0)))
        except Exception:
            gain = 1.0

    mouth_open_ids: list[str] = ["ParamMouthOpenY", "PARAM_MOUTH_OPEN_Y", "ParamMouthOpenX", "LipSync"]
    mouth_form_ids: list[str] = ["ParamMouthForm", "PARAM_MOUTH_FORM"]

    face_tracking = controllers.get("FaceTracking", {})
    if isinstance(face_tracking, dict):
        open_items = face_tracking.get("MouthOpenY", [])
        if isinstance(open_items, list):
            for item in open_items:
                if isinstance(item, dict):
                    param_id = str(item.get("Id") or "").strip()
                    if param_id and param_id not in mouth_open_ids:
                        mouth_open_ids.append(param_id)
        form_items = face_tracking.get("MouthForm", [])
        if isinstance(form_items, list):
            for item in form_items:
                if isinstance(item, dict):
                    param_id = str(item.get("Id") or "").strip()
                    if param_id and param_id not in mouth_form_ids:
                        mouth_form_ids.append(param_id)

    return {
        "gain": gain,
        "mouth_open_ids": mouth_open_ids,
        "mouth_form_ids": mouth_form_ids,
    }


def ensure_live2d_model_from_json(model_path: Path, model_json: dict) -> tuple[Path, dict, list[dict]]:
    groups = extract_motion_groups(model_json)
    exprs = extract_expression_defs(model_json)
    need_patch = False

    if not groups:
        groups = scan_motion_groups(model_path)
        if groups:
            need_patch = True
    if not exprs:
        exprs = scan_expression_defs(model_path)
        if exprs:
            need_patch = True

    if not need_patch:
        return model_path, groups, exprs

    patched = json.loads(json.dumps(model_json))
    patched.setdefault("FileReferences", {})
    if groups:
        patched["FileReferences"]["Motions"] = groups
    if exprs:
        patched["FileReferences"]["Expressions"] = exprs

    generated_path = model_path.with_name(f"{model_path.stem}.autogen.model3.json")
    try:
        with generated_path.open("w", encoding="utf-8") as f:
            json.dump(patched, f, ensure_ascii=False, indent=2)
        return generated_path, groups, exprs
    except Exception:
        return model_path, groups, exprs


def ensure_live2d_model(model_path: Path) -> tuple[Path, dict, list[dict]]:
    if not model_path.exists():
        return model_path, {}, []
    try:
        with model_path.open("r", encoding="utf-8") as f:
            model_json = json.load(f)
    except Exception:
        return model_path, {}, []
    return ensure_live2d_model_from_json(model_path, model_json)


def action_items_from_defs(groups: dict, exprs: list[dict]) -> list[dict]:
    items: list[dict] = []
    for group_name, entries in groups.items():
        if not isinstance(entries, list):
            continue
        for idx, _ in enumerate(entries):
            items.append(
                {
                    "type": "motion",
                    "group": group_name,
                    "index": idx,
                    "label": f"{group_name}[{idx}]",
                }
            )
    for expr in exprs:
        if not isinstance(expr, dict):
            continue
        name = str(expr.get("Name") or "").strip()
        if not name:
            continue
        items.append(
            {
                "type": "expression",
                "name": name,
                "label": f"Expr:{name}",
            }
        )
    return items


_extract_motion_groups = extract_motion_groups
_infer_motion_group = infer_motion_group
_resolve_desktop_directory_seed = resolve_desktop_directory_seed
_scan_motion_groups = scan_motion_groups
_extract_expression_defs = extract_expression_defs
_scan_expression_defs = scan_expression_defs
