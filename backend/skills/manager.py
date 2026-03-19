from __future__ import annotations

import json
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

from .adapters import detect_adapter_profile, normalize_subdir, resolve_compatibility_mode, safe_relative_subdir
from .models import SkillImportResult, SkillManifest, SkillRecord, SkillResourceEntry, SkillScriptDefinition


SKILL_MD_NAME = "SKILL.md"
SKILL_JSON_NAME = "skill.json"
SKILL_SOURCE_META_NAME = ".skill_source.json"
IGNORED_DISCOVERY_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
    ".uv-cache",
    ".uv-tools",
    "dist",
    "build",
}
RESOURCE_TEXT_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml"}


def _safe_skill_id(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(name or "").strip())
    cleaned = cleaned.strip("._-").lower()
    return cleaned or "skill"


def _dedupe_strings(items: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items or []:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return tuple(out)


def _parse_frontmatter(raw_text: str) -> tuple[dict[str, str], str]:
    text = str(raw_text or "")
    match = re.match(r"^\s*---\s*\r?\n(.*?)\r?\n---\s*\r?\n?(.*)$", text, re.DOTALL)
    if not match:
        return {}, text.strip()
    frontmatter_text = match.group(1)
    body = match.group(2).strip()
    frontmatter: dict[str, str] = {}
    for raw_line in frontmatter_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        frontmatter[key.strip()] = value.strip().strip('"').strip("'")
    return frontmatter, body


def _parse_simple_yaml(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    payload: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        payload[key.strip()] = value.strip().strip('"').strip("'")
    return payload


def _relative_files(root: Path, directory_name: str) -> tuple[str, ...]:
    target = root / directory_name
    if not target.exists() or not target.is_dir():
        return ()
    items: list[str] = []
    for child in sorted(target.rglob("*")):
        if not child.is_file():
            continue
        try:
            items.append(child.relative_to(root).as_posix())
        except ValueError:
            continue
    return tuple(items)


def _load_source_metadata(root_path: Path) -> dict[str, Any]:
    meta_path = root_path / SKILL_SOURCE_META_NAME
    if not meta_path.exists():
        return {}
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_source_metadata(root_path: Path, metadata: dict[str, Any]) -> None:
    payload = {str(key): value for key, value in dict(metadata or {}).items() if value not in (None, "")}
    if not payload:
        return
    (root_path / SKILL_SOURCE_META_NAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _discover_resource_entries(root_path: Path) -> tuple[SkillResourceEntry, ...]:
    resources: list[SkillResourceEntry] = []
    seen_paths: set[str] = set()

    def _append(path: Path, category: str) -> None:
        if not path.exists() or not path.is_file():
            return
        try:
            relative = path.relative_to(root_path).as_posix()
        except ValueError:
            return
        if relative in seen_paths:
            return
        seen_paths.add(relative)
        try:
            size_bytes = int(path.stat().st_size)
        except Exception:
            size_bytes = 0
        resources.append(
            SkillResourceEntry(
                path=relative,
                category=category,
                absolute_path=path.resolve(),
                size_bytes=size_bytes,
            )
        )

    _append(root_path / SKILL_MD_NAME, "prompt")
    for directory_name, category in (("references", "reference"), ("assets", "asset")):
        directory = root_path / directory_name
        if not directory.exists() or not directory.is_dir():
            continue
        for child in sorted(directory.rglob("*")):
            _append(child, category)
    for child in sorted(root_path.iterdir(), key=lambda item: item.name.lower()):
        if not child.is_file():
            continue
        if child.name in {SKILL_MD_NAME, SKILL_JSON_NAME, SKILL_SOURCE_META_NAME}:
            continue
        if child.suffix.lower() not in RESOURCE_TEXT_SUFFIXES:
            continue
        _append(child, "document")
    return tuple(resources)


class SkillManager:
    def __init__(self, root_dir: Path, builtin_dir: Path | None = None, imported_dir: Path | None = None) -> None:
        self.root_dir = Path(root_dir)
        self.builtin_dir = Path(builtin_dir or (self.root_dir / "skills" / "builtin"))
        self.imported_dir = Path(imported_dir or (self.root_dir / "third_party_skills"))
        self.builtin_dir.mkdir(parents=True, exist_ok=True)
        self.imported_dir.mkdir(parents=True, exist_ok=True)

    def validate_skill_dir(
        self,
        skill_dir: str | Path,
        *,
        source_type: str = "builtin",
        source: str = "",
        preferred_name: str = "",
    ) -> SkillRecord:
        root_path = Path(skill_dir).resolve()
        if not root_path.exists() or not root_path.is_dir():
            raise FileNotFoundError(str(root_path))
        skill = self._load_skill(root_path, source_type=source_type, preferred_name=preferred_name)
        if not skill.ok:
            raise ValueError("; ".join(skill.errors) or "invalid skill")
        return skill

    def list_skills(self) -> list[SkillRecord]:
        items: list[SkillRecord] = []
        for source_type, base_dir in (("builtin", self.builtin_dir), ("imported", self.imported_dir)):
            if not base_dir.exists():
                continue
            for child in sorted(base_dir.iterdir(), key=lambda item: item.name.lower()):
                if child.is_dir():
                    items.append(self._load_skill(child, source_type=source_type))
        return items

    def get_skill(self, skill_id: str) -> SkillRecord | None:
        target = _safe_skill_id(skill_id)
        for item in self.list_skills():
            if item.skill_id == target:
                return item
        return None

    def import_local_directory(self, source_dir: str | Path, name: str = "") -> SkillImportResult:
        src = Path(source_dir).resolve()
        skill = self.validate_skill_dir(src, source_type="imported", preferred_name=name)
        self._ensure_skill_id_available(skill.skill_id)
        target_path = self.imported_dir / skill.skill_id
        shutil.copytree(
            src,
            target_path,
            ignore=shutil.ignore_patterns(*IGNORED_DISCOVERY_DIRS),
        )
        _write_source_metadata(
            target_path,
            {
                "source_type": "local",
                "source_dir": str(src),
            },
        )
        installed = self.validate_skill_dir(target_path, source_type="imported")
        return SkillImportResult(
            skill=installed,
            target_path=target_path,
            metadata={
                "source_type": "local",
                "source_dir": str(src),
                "source_repo": "",
                "source_ref": "",
                "source_subdir": "",
            },
        )

    def install_from_git(self, repo_url: str, name: str = "", ref: str = "", subdir: str = "") -> SkillImportResult:
        repo = str(repo_url or "").strip()
        if not repo:
            raise ValueError("repo_url cannot be empty")
        subdir_value = normalize_subdir(subdir)
        ref_name = str(ref or "").strip()
        temp_path = self.root_dir / f".skill_git_{uuid.uuid4().hex}"
        temp_path.mkdir(parents=True, exist_ok=False)
        try:
            clone_path = temp_path / "repo"
            subprocess.run(["git", "clone", "--depth", "1", repo, str(clone_path)], check=True, cwd=str(self.root_dir))
            if ref_name:
                subprocess.run(["git", "checkout", ref_name], check=True, cwd=str(clone_path))
            skill_source_path = safe_relative_subdir(clone_path, subdir_value)
            if not skill_source_path.exists() or not skill_source_path.is_dir():
                raise ValueError(f"skill subdir not found: {subdir_value or '.'}")
            skill = self.validate_skill_dir(skill_source_path, source_type="imported", preferred_name=name)
            self._ensure_skill_id_available(skill.skill_id)
            target_path = self.imported_dir / skill.skill_id
            shutil.copytree(
                skill_source_path,
                target_path,
                ignore=shutil.ignore_patterns(*IGNORED_DISCOVERY_DIRS),
            )
        finally:
            shutil.rmtree(temp_path, ignore_errors=True)
        _write_source_metadata(
            target_path,
            {
                "source_type": "git",
                "source_repo": repo,
                "source_ref": ref_name,
                "source_subdir": subdir_value,
            },
        )
        skill = self.validate_skill_dir(target_path, source_type="imported", preferred_name=name)
        return SkillImportResult(
            skill=skill,
            target_path=target_path,
            metadata={
                "repo_url": repo,
                "source_repo": repo,
                "ref": ref_name,
                "source_ref": ref_name,
                "subdir": subdir_value,
                "source_subdir": subdir_value,
                "source_type": "git",
            },
        )

    def import_from_git(self, repo_url: str, name: str = "", ref: str = "", subdir: str = "") -> SkillImportResult:
        return self.install_from_git(repo_url=repo_url, name=name, ref=ref, subdir=subdir)

    def delete_imported_skill(self, skill_id: str) -> None:
        target_path = self.imported_dir / _safe_skill_id(skill_id)
        if not target_path.exists():
            raise FileNotFoundError(str(target_path))
        shutil.rmtree(target_path)

    def _load_skill(self, root_path: Path, *, source_type: str, preferred_name: str = "") -> SkillRecord:
        skill_md_path = root_path / SKILL_MD_NAME
        if not skill_md_path.exists():
            return SkillRecord(
                skill_id=_safe_skill_id(preferred_name or root_path.name),
                name=preferred_name or root_path.name,
                description="",
                source_type=source_type,
                root_path=root_path,
                skill_md_path=skill_md_path,
                prompt_body="",
                ok=False,
                errors=("missing SKILL.md",),
            )

        raw_frontmatter, prompt_body = _parse_frontmatter(skill_md_path.read_text(encoding="utf-8"))
        name = str(raw_frontmatter.get("name") or preferred_name or root_path.name).strip()
        description = str(raw_frontmatter.get("description") or "").strip()
        errors: list[str] = []
        if not str(raw_frontmatter.get("name") or "").strip():
            errors.append("SKILL.md requires name in frontmatter")
        if not description:
            errors.append("SKILL.md requires description in frontmatter")

        metadata = _parse_simple_yaml(root_path / "agents" / "openai.yaml")
        display_name = str(metadata.get("display_name") or name).strip() or name
        short_description = str(metadata.get("short_description") or description).strip() or description
        source_meta = _load_source_metadata(root_path)
        source_repo = str(source_meta.get("source_repo") or source_meta.get("repo_url") or "").strip()
        source_ref = str(source_meta.get("source_ref") or source_meta.get("ref") or "").strip()
        source_subdir = normalize_subdir(source_meta.get("source_subdir") or source_meta.get("subdir") or "")

        manifest = self._load_manifest(root_path, errors)
        has_manifest = root_path.joinpath(SKILL_JSON_NAME).exists()
        resources = _discover_resource_entries(root_path)
        adapter_profile = detect_adapter_profile(source_repo=source_repo, source_subdir=source_subdir)
        compatibility_mode = resolve_compatibility_mode(
            has_manifest=has_manifest,
            adapter_profile=adapter_profile,
            source_repo=source_repo,
            source_subdir=source_subdir,
            resource_count=len(resources),
        )
        return SkillRecord(
            skill_id=_safe_skill_id(preferred_name or name or root_path.name),
            name=name or root_path.name,
            description=description,
            source_type=source_type,
            root_path=root_path,
            skill_md_path=skill_md_path,
            prompt_body=prompt_body,
            manifest=manifest,
            has_manifest=has_manifest,
            ok=not errors,
            errors=tuple(errors),
            references=_relative_files(root_path, "references"),
            assets=_relative_files(root_path, "assets"),
            resources=resources,
            display_name=display_name,
            short_description=short_description,
            has_openai_metadata=bool(metadata),
            source_repo=source_repo,
            source_ref=source_ref,
            source_subdir=source_subdir,
            compatibility_mode=compatibility_mode,
            adapter_profile=adapter_profile,
        )

    def _load_manifest(self, root_path: Path, errors: list[str]) -> SkillManifest:
        manifest_path = root_path / SKILL_JSON_NAME
        if not manifest_path.exists():
            return SkillManifest()
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"invalid skill.json: {exc}")
            return SkillManifest()
        if not isinstance(payload, dict):
            errors.append("skill.json must be an object")
            return SkillManifest()

        version = str(payload.get("version") or "1.0.0").strip() or "1.0.0"
        tool_allowlist = _dedupe_strings(payload.get("tool_allowlist"))
        raw_scripts = payload.get("scripts") or []
        if raw_scripts and not isinstance(raw_scripts, list):
            errors.append("skill.json scripts must be a list")
            raw_scripts = []

        scripts: list[SkillScriptDefinition] = []
        seen_names: set[str] = set()
        for raw_script in raw_scripts if isinstance(raw_scripts, list) else []:
            if not isinstance(raw_script, dict):
                errors.append("skill.json script entries must be objects")
                continue
            script_name = _safe_skill_id(raw_script.get("name") or "")
            if not script_name:
                errors.append("skill.json script requires name")
                continue
            if script_name in seen_names:
                errors.append(f"duplicate script name: {script_name}")
                continue
            seen_names.add(script_name)
            script_path = str(raw_script.get("path") or "").replace("\\", "/").strip()
            if not script_path:
                errors.append(f"script {script_name} requires path")
                continue
            absolute_path = (root_path / script_path).resolve()
            if absolute_path != root_path and root_path not in absolute_path.parents:
                errors.append(f"script path escapes skill directory: {script_path}")
                continue
            if absolute_path.suffix.lower() != ".py":
                errors.append(f"script must be a Python file: {script_path}")
                continue
            if not absolute_path.exists():
                errors.append(f"script file not found: {script_path}")
                continue
            input_schema = raw_script.get("input_schema")
            if not isinstance(input_schema, dict):
                input_schema = {"type": "object", "properties": {}}
            try:
                timeout_sec = max(1, min(600, int(raw_script.get("timeout_sec", 60))))
            except Exception:
                timeout_sec = 60
            scripts.append(
                SkillScriptDefinition(
                    name=script_name,
                    description=str(raw_script.get("description") or script_name).strip() or script_name,
                    path=script_path,
                    absolute_path=absolute_path,
                    input_schema=input_schema,
                    timeout_sec=timeout_sec,
                )
            )
        return SkillManifest(version=version, tool_allowlist=tool_allowlist, scripts=tuple(scripts))

    def _ensure_skill_id_available(self, skill_id: str) -> None:
        if self.get_skill(skill_id) is not None:
            raise FileExistsError(skill_id)
