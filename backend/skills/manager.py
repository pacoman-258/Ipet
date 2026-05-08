from __future__ import annotations

import ast
import json
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

from .adapters import normalize_subdir, safe_relative_subdir
from .discovery import AUTO_SCRIPT_IGNORED_PARTS, IGNORED_DISCOVERY_DIRS, SKILL_JSON_NAME, SKILL_MD_NAME
from .discovery import discover_builtin_candidates, discover_imported_candidates
from .models import SkillDiscoveryCandidate, SkillImportResult, SkillManifest, SkillRecord, SkillScriptDefinition
from .normalizer import discover_resource_entries, manifest_allowlist_from_sources, normalize_skill_record, safe_skill_id
from .parsers import build_site_metadata, load_clawhub_metadata, load_source_metadata, parse_frontmatter, parse_simple_yaml, write_source_metadata
from ..storage_paths import hermes_skills_dir, legacy_third_party_skills_dir



def _script_tool_name_from_path(relative_path: str) -> str:
    normalized = str(relative_path or "").replace("\\", "/").strip().strip("/")
    if not normalized:
        return ""
    raw_parts = list(Path(normalized).with_suffix("").parts)
    if raw_parts and raw_parts[0] == "scripts":
        raw_parts = raw_parts[1:]
    cleaned_parts: list[str] = []
    for part in raw_parts:
        token = safe_skill_id(part).replace(".", "-")
        if token:
            cleaned_parts.append(token)
    return ".".join(cleaned_parts)



def _script_summary(path: Path) -> str:
    try:
        module = ast.parse(path.read_text(encoding="utf-8"))
    except Exception:
        return f"Run {path.name}"
    raw_doc = ast.get_docstring(module)
    if raw_doc:
        first_line = str(raw_doc).strip().splitlines()[0].strip()
        if first_line:
            return first_line
    return f"Run {path.name}"



def _default_cli_input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Command-line arguments passed to the skill script.",
            },
            "stdin": {
                "type": "string",
                "description": "Optional plain-text stdin passed to the script.",
            },
            "stdin_json": {
                "type": "object",
                "description": "Optional JSON payload encoded to stdin.",
            },
        },
    }



def _auto_script_spec(tool_name: str, summary: str) -> tuple[str, dict[str, Any], str]:
    generic_description = f"{summary} (auto-discovered CLI script)"
    if tool_name == "office.pack":
        return (
            "Create or save the final .pptx/.docx/.xlsx file by packing an edited unpacked directory. Use this after editing slides or XML. (auto-discovered CLI script)",
            {
                "type": "object",
                "properties": {
                    "input_directory": {"type": "string", "description": "Unpacked Office document directory to pack."},
                    "output_file": {"type": "string", "description": "Output .pptx/.docx/.xlsx file path to create."},
                    "original_file": {"type": "string", "description": "Optional original Office file used for validation comparison."},
                    "validate": {"type": "boolean", "description": "Whether to run validation before packing. Defaults to true."},
                },
                "required": ["input_directory", "output_file"],
            },
            "argv_office_pack",
        )
    if tool_name == "office.unpack":
        return (
            "Unpack a .pptx/.docx/.xlsx into a working directory so it can be edited slide-by-slide or XML-by-XML. (auto-discovered CLI script)",
            {
                "type": "object",
                "properties": {
                    "input_file": {"type": "string", "description": "Source Office file path to unpack."},
                    "output_directory": {"type": "string", "description": "Directory that will receive the unpacked Office contents."},
                    "merge_runs": {"type": "boolean", "description": "DOCX-only option to merge adjacent runs."},
                    "simplify_redlines": {"type": "boolean", "description": "DOCX-only option to simplify adjacent tracked changes."},
                },
                "required": ["input_file", "output_directory"],
            },
            "argv_office_unpack",
        )
    if tool_name == "office.validate":
        return (
            "Validate an Office file or unpacked directory before packing/exporting it. (auto-discovered CLI script)",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to a packed Office file or unpacked directory."},
                    "original_file": {"type": "string", "description": "Optional original Office file for comparison."},
                    "auto_repair": {"type": "boolean", "description": "Automatically repair common issues before validating."},
                    "author": {"type": "string", "description": "Optional author name for redlining validation."},
                    "verbose": {"type": "boolean", "description": "Enable verbose validator output."},
                },
                "required": ["path"],
            },
            "argv_office_validate",
        )
    if tool_name == "add_slide":
        return (
            "Add a new slide to an unpacked PPTX directory by duplicating an existing slide or creating one from a layout. (auto-discovered CLI script)",
            {
                "type": "object",
                "properties": {
                    "unpacked_dir": {"type": "string", "description": "Unpacked PPTX working directory."},
                    "source": {"type": "string", "description": "Source slide or layout XML filename."},
                },
                "required": ["unpacked_dir", "source"],
            },
            "argv_pptx_add_slide",
        )
    if tool_name == "clean":
        return (
            "Clean an unpacked PPTX directory by removing unreferenced slides and assets before packing the final .pptx. (auto-discovered CLI script)",
            {
                "type": "object",
                "properties": {
                    "unpacked_dir": {"type": "string", "description": "Unpacked PPTX working directory to clean."},
                },
                "required": ["unpacked_dir"],
            },
            "argv_pptx_clean",
        )
    if tool_name == "thumbnail":
        return (
            "Render thumbnail grids from a PPTX so the model can visually inspect slides or layout before or after editing. (auto-discovered CLI script)",
            {
                "type": "object",
                "properties": {
                    "input": {"type": "string", "description": "Input .pptx file path."},
                    "output_prefix": {"type": "string", "description": "Optional output image prefix."},
                    "cols": {"type": "integer", "description": "Optional number of thumbnail columns."},
                },
                "required": ["input"],
            },
            "argv_pptx_thumbnail",
        )
    return generic_description, _default_cli_input_schema(), "argv"



def _discover_auto_script_entries(root_path: Path, *, existing_names: set[str]) -> tuple[SkillScriptDefinition, ...]:
    scripts_root = root_path / "scripts"
    if not scripts_root.exists() or not scripts_root.is_dir():
        return ()

    entries: list[SkillScriptDefinition] = []
    for candidate in sorted(scripts_root.rglob("*.py")):
        if candidate.name == "__init__.py":
            continue
        try:
            relative_under_scripts = candidate.relative_to(scripts_root)
            relative_path = candidate.relative_to(root_path).as_posix()
        except ValueError:
            continue
        if any(part in AUTO_SCRIPT_IGNORED_PARTS for part in relative_under_scripts.parts):
            continue
        try:
            source_text = candidate.read_text(encoding="utf-8")
        except Exception:
            continue
        if "__main__" not in source_text:
            continue
        tool_name = _script_tool_name_from_path(relative_path)
        if not tool_name or tool_name in existing_names:
            continue
        existing_names.add(tool_name)
        summary = _script_summary(candidate)
        description, input_schema, runner = _auto_script_spec(tool_name, summary)
        entries.append(
            SkillScriptDefinition(
                name=tool_name,
                description=description,
                path=relative_path,
                absolute_path=candidate.resolve(),
                input_schema=input_schema,
                timeout_sec=120,
                runner=runner,
            )
        )
    return tuple(entries)


class SkillManager:
    def __init__(
        self,
        root_dir: Path,
        builtin_dir: Path | None = None,
        imported_dir: Path | None = None,
        legacy_imported_dir: Path | None = None,
    ) -> None:
        self.root_dir = Path(root_dir)
        self.builtin_dir = Path(builtin_dir or (self.root_dir / "skills" / "builtin"))
        self.imported_dir = Path(imported_dir or hermes_skills_dir(self.root_dir))
        self.legacy_imported_dir = None
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
        candidate = SkillDiscoveryCandidate(
            source_type=source_type,
            package_root=root_path,
            discovery_root=root_path,
        )
        skill = self._load_skill(candidate, preferred_name=preferred_name)
        if not skill.ok:
            raise ValueError("; ".join(skill.errors) or "invalid skill")
        return skill

    def list_skills(self) -> list[SkillRecord]:
        items: list[SkillRecord] = []
        seen_ids: set[str] = set()
        candidates = [*discover_builtin_candidates(self.builtin_dir)]
        for imported_dir in self._imported_read_dirs():
            candidates.extend(discover_imported_candidates(imported_dir))
        for candidate in candidates:
            item = self._load_skill(candidate)
            if item.skill_id in seen_ids:
                continue
            seen_ids.add(item.skill_id)
            items.append(item)
        return items

    def get_skill(self, skill_id: str) -> SkillRecord | None:
        target = safe_skill_id(skill_id)
        for item in self.list_skills():
            if item.skill_id == target or target in item.aliases:
                return item
        return None

    def canonicalize_skill_id(self, skill_id: str) -> str:
        target = safe_skill_id(skill_id)
        if not target:
            return ""
        skill = self.get_skill(target)
        return skill.skill_id if skill is not None else target

    def canonicalize_skill_ids(self, skill_ids: list[str] | tuple[str, ...] | None) -> list[str]:
        seen: set[str] = set()
        items: list[str] = []
        for skill_id in skill_ids or []:
            canonical = self.canonicalize_skill_id(str(skill_id or "").strip())
            if not canonical or canonical in seen:
                continue
            seen.add(canonical)
            items.append(canonical)
        return items

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
        write_source_metadata(
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
        write_source_metadata(
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
        record = self.get_skill(skill_id)
        if record is not None and record.source_type == "imported":
            target_path = record.package_root or record.root_path
        else:
            target_path = self.imported_dir / safe_skill_id(skill_id)
        if not target_path.exists():
            raise FileNotFoundError(str(target_path))
        shutil.rmtree(target_path)

    def _load_skill(self, candidate: SkillDiscoveryCandidate, *, preferred_name: str = "") -> SkillRecord:
        root_path = candidate.discovery_root.resolve()
        skill_md_path = root_path / SKILL_MD_NAME
        if not skill_md_path.exists():
            return SkillRecord(
                skill_id=safe_skill_id(preferred_name or root_path.name),
                name=preferred_name or root_path.name,
                description="",
                source_type=candidate.source_type,
                root_path=root_path,
                skill_md_path=skill_md_path,
                prompt_body="",
                ok=False,
                errors=("missing SKILL.md",),
                package_root=candidate.package_root,
                discovery_root=root_path,
            )

        raw_frontmatter, prompt_body = parse_frontmatter(skill_md_path.read_text(encoding="utf-8"))
        metadata = parse_simple_yaml(root_path / "agents" / "openai.yaml")
        source_meta = load_source_metadata(root_path)
        manifest, manifest_payload, manifest_errors = self._load_manifest(root_path)
        clawhub_meta = load_clawhub_metadata(root_path)
        site_metadata = build_site_metadata(
            root_path=root_path,
            source_meta=source_meta,
            manifest_payload=manifest_payload,
            clawhub_meta=clawhub_meta,
        )
        if manifest.tool_allowlist:
            allowlist = manifest.tool_allowlist
        else:
            allowlist = manifest_allowlist_from_sources(raw_frontmatter, manifest_payload)
            if allowlist:
                manifest = SkillManifest(version=manifest.version, tool_allowlist=allowlist, scripts=manifest.scripts)
        if not manifest.scripts:
            existing_names: set[str] = set()
            auto_scripts = _discover_auto_script_entries(root_path, existing_names=existing_names)
            if auto_scripts:
                manifest = SkillManifest(
                    version=manifest.version,
                    tool_allowlist=manifest.tool_allowlist,
                    scripts=tuple(auto_scripts),
                )
        resources = discover_resource_entries(root_path)
        record = normalize_skill_record(
            source_type=candidate.source_type,
            root_path=root_path,
            package_root=candidate.package_root.resolve(),
            skill_md_path=skill_md_path,
            prompt_body=prompt_body,
            frontmatter=raw_frontmatter,
            metadata=metadata,
            source_meta=source_meta,
            site_metadata=site_metadata,
            manifest=manifest,
            has_manifest=root_path.joinpath(SKILL_JSON_NAME).exists(),
            resources=resources,
            preferred_name=preferred_name,
        )
        if manifest_errors:
            merged_errors = tuple(list(record.errors) + list(manifest_errors))
            return SkillRecord(
                **{**record.__dict__, "ok": False, "errors": merged_errors}
            )
        return record

    def _load_manifest(self, root_path: Path) -> tuple[SkillManifest, dict[str, Any], tuple[str, ...]]:
        manifest_path = root_path / SKILL_JSON_NAME
        if not manifest_path.exists():
            return SkillManifest(), {}, ()
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return SkillManifest(), {}, (f"invalid skill.json: {exc}",)
        if not isinstance(payload, dict):
            return SkillManifest(), {}, ("skill.json must be an object",)

        version = str(payload.get("version") or "1.0.0").strip() or "1.0.0"
        tool_allowlist = manifest_allowlist_from_sources({}, payload)
        raw_scripts = payload.get("scripts") or []
        errors: list[str] = []
        if raw_scripts and not isinstance(raw_scripts, list):
            errors.append("skill.json scripts must be a list")
            raw_scripts = []

        scripts: list[SkillScriptDefinition] = []
        seen_names: set[str] = set()
        for raw_script in raw_scripts if isinstance(raw_scripts, list) else []:
            if not isinstance(raw_script, dict):
                errors.append("skill.json script entries must be objects")
                continue
            script_name = safe_skill_id(raw_script.get("name") or "")
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
                    runner=str(raw_script.get("runner") or "json_stdin").strip() or "json_stdin",
                )
            )
        return SkillManifest(version=version, tool_allowlist=tool_allowlist, scripts=tuple(scripts)), payload, tuple(errors)

    def _ensure_skill_id_available(self, skill_id: str) -> None:
        if self.get_skill(skill_id) is not None:
            raise FileExistsError(skill_id)

    def _imported_read_dirs(self) -> list[Path]:
        dirs: list[Path] = []
        seen: set[Path] = set()
        for item in (self.imported_dir, self.legacy_imported_dir):
            if item is None:
                continue
            resolved = item.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            dirs.append(item)
        return dirs
