from __future__ import annotations

import json
import importlib.util
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zipfile import ZipFile

from ..tool_runtime import Tool, ToolRegistry, ToolResult
from .adapters import PPTX_ADAPTER_PROFILE
from .manager import SkillManager
from .models import ResolvedSkillSet, SkillRecord, SkillResourceEntry, SkillScriptDefinition


def _safe_json_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def _resolve_skill_relative_path(root: Path, relative_path: str) -> Path:
    normalized = str(relative_path or "").replace("\\", "/").strip().strip("/")
    if not normalized:
        raise ValueError("path cannot be empty")
    resolved_root = root.resolve()
    target = (resolved_root / normalized).resolve()
    if target != resolved_root and resolved_root not in target.parents:
        raise ValueError(f"path escapes skill directory: {relative_path}")
    return target


def tool_name_matches_pattern(tool_name: str, pattern: str) -> bool:
    name = str(tool_name or "").strip()
    matcher = str(pattern or "").strip()
    if not name or not matcher:
        return False
    if matcher.endswith("*"):
        return name.startswith(matcher[:-1])
    if matcher.endswith("."):
        return name.startswith(matcher)
    return name == matcher or name.startswith(f"{matcher}.")


def render_skill_prompt(skills: list[SkillRecord] | tuple[SkillRecord, ...]) -> str:
    blocks: list[str] = []
    for skill in skills:
        body = str(skill.prompt_body or "").strip()
        if not body:
            continue
        blocks.append(f"[Skill: {skill.display_name or skill.name}]\n{body}")
    return "\n\n".join(blocks).strip()


@dataclass(frozen=True)
class SkillScriptTool:
    skill: SkillRecord
    definition: SkillScriptDefinition

    @property
    def full_name(self) -> str:
        return f"skill.{self.skill.skill_id}.{self.definition.tool_name}"


class SkillRuntime:
    def __init__(self, manager: SkillManager) -> None:
        self.manager = manager

    def resolve_active_skills(
        self,
        skill_ids: list[str] | tuple[str, ...] | None = None,
        *,
        default_active_ids: list[str] | tuple[str, ...] | None = None,
        enabled: bool = True,
    ) -> ResolvedSkillSet:
        if not enabled:
            return ResolvedSkillSet(defaulted=not skill_ids)

        requested = [str(item).strip() for item in (skill_ids or []) if str(item).strip()]
        defaulted = not requested
        candidate_ids = requested or [str(item).strip() for item in (default_active_ids or []) if str(item).strip()]
        resolved_skills: list[SkillRecord] = []
        seen: set[str] = set()
        for skill_id in candidate_ids:
            if skill_id in seen:
                continue
            seen.add(skill_id)
            skill = self.manager.get_skill(skill_id)
            if skill is None or not skill.ok:
                continue
            resolved_skills.append(skill)

        allowlist: list[str] = []
        allow_seen: set[str] = set()
        for skill in resolved_skills:
            for pattern in skill.manifest.tool_allowlist:
                normalized = str(pattern or "").strip()
                if normalized and normalized not in allow_seen:
                    allowlist.append(normalized)
                    allow_seen.add(normalized)

        partial = ResolvedSkillSet(skills=tuple(resolved_skills))
        resource_tools = tuple(self.build_resource_tools(partial))
        adapter_tools = tuple(self.build_adapter_tools(partial))
        explicit_script_tools = tuple(self.build_script_tools(partial))
        return ResolvedSkillSet(
            skill_ids=tuple(skill.skill_id for skill in resolved_skills),
            skills=tuple(resolved_skills),
            prompt_text=render_skill_prompt(resolved_skills),
            tool_allowlist=tuple(allowlist),
            resource_tools=resource_tools,
            adapter_tools=adapter_tools,
            script_tools=explicit_script_tools,
            defaulted=defaulted,
        )

    def build_resource_tools(self, resolved: ResolvedSkillSet) -> list[Tool]:
        tools: list[Tool] = []
        for skill in resolved.skills:
            if skill.compatibility_mode not in {"standard_prompt", "standard_adapter"}:
                continue
            resource_entries = tuple(skill.resources)
            if not resource_entries:
                continue
            tools.append(
                Tool(
                    name=f"skill.{skill.skill_id}.list_resources",
                    description=f"[skill:{skill.skill_id}] List readable resources bundled with this skill.",
                    input_schema={"type": "object", "properties": {}},
                    invoke=lambda arguments, bound_skill=skill, bound_resources=resource_entries: self.list_resources_tool(
                        bound_skill,
                        bound_resources,
                        arguments,
                    ),
                    source=f"skill:{skill.skill_id}",
                    metadata={"skill_id": skill.skill_id, "resource_count": len(resource_entries)},
                )
            )
            tools.append(
                Tool(
                    name=f"skill.{skill.skill_id}.read_resource",
                    description=f"[skill:{skill.skill_id}] Read one allowed resource file from this skill.",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Relative path returned by list_resources."}
                        },
                        "required": ["path"],
                    },
                    invoke=lambda arguments, bound_skill=skill, bound_resources=resource_entries: self.read_resource_tool(
                        bound_skill,
                        bound_resources,
                        arguments,
                    ),
                    source=f"skill:{skill.skill_id}",
                    metadata={"skill_id": skill.skill_id, "resource_count": len(resource_entries)},
                )
            )
        return tools

    def list_resources_tool(
        self,
        skill: SkillRecord,
        resource_entries: tuple[SkillResourceEntry, ...],
        arguments: dict[str, Any] | None = None,
    ) -> ToolResult:
        return ToolResult.from_value(
            {
                "skill_id": skill.skill_id,
                "resources": [
                    {
                        "path": entry.path,
                        "category": entry.category,
                        "size_bytes": entry.size_bytes,
                    }
                    for entry in resource_entries
                ],
                "count": len(resource_entries),
            }
        )

    def read_resource_tool(
        self,
        skill: SkillRecord,
        resource_entries: tuple[SkillResourceEntry, ...],
        arguments: dict[str, Any] | None = None,
    ) -> ToolResult:
        args = arguments if isinstance(arguments, dict) else {}
        relative_path = str(args.get("path") or "").replace("\\", "/").strip().strip("/")
        if not relative_path:
            return ToolResult.from_error("path cannot be empty")
        resource_map = {entry.path: entry for entry in resource_entries}
        entry = resource_map.get(relative_path)
        if entry is None:
            return ToolResult.from_error(f"resource not available: {relative_path}")
        try:
            target = _resolve_skill_relative_path(skill.root_path.resolve(), relative_path)
        except ValueError as exc:
            return ToolResult.from_error(str(exc))
        if not target.exists() or not target.is_file():
            return ToolResult.from_error(f"resource not found: {relative_path}")
        if entry.size_bytes > 256_000:
            return ToolResult.from_error(f"resource too large to read inline: {relative_path}")
        try:
            content = target.read_text(encoding="utf-8")
        except Exception as exc:
            return ToolResult.from_error(f"failed to read resource: {exc}")
        return ToolResult.from_value(
            {
                "skill_id": skill.skill_id,
                "path": relative_path,
                "category": entry.category,
                "content": content,
            }
        )

    def build_adapter_tools(self, resolved: ResolvedSkillSet) -> list[Tool]:
        tools: list[Tool] = []
        for skill in resolved.skills:
            if skill.adapter_profile == PPTX_ADAPTER_PROFILE:
                tools.extend(self._build_pptx_adapter_tools(skill))
        return tools

    def _build_pptx_adapter_tools(self, skill: SkillRecord) -> list[Tool]:
        skill_prefix = f"skill.{skill.skill_id}"
        return [
            Tool(
                name=f"{skill_prefix}.extract_text",
                description=f"[skill:{skill.skill_id}] Extract text/markdown from a PPTX file using markitdown.",
                input_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "Absolute or relative PPTX file path."}},
                    "required": ["path"],
                },
                invoke=lambda arguments, bound_skill=skill: self.pptx_extract_text_tool(bound_skill, arguments),
                source=f"skill:{skill.skill_id}",
                metadata={"skill_id": skill.skill_id, "adapter_profile": PPTX_ADAPTER_PROFILE},
            ),
            Tool(
                name=f"{skill_prefix}.thumbnail",
                description=f"[skill:{skill.skill_id}] Generate preview images for a PPTX when the helper script is available.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "output_dir": {"type": "string"},
                    },
                    "required": ["path", "output_dir"],
                },
                invoke=lambda arguments, bound_skill=skill: self.pptx_thumbnail_tool(bound_skill, arguments),
                source=f"skill:{skill.skill_id}",
                metadata={"skill_id": skill.skill_id, "adapter_profile": PPTX_ADAPTER_PROFILE},
            ),
            Tool(
                name=f"{skill_prefix}.unpack_xml",
                description=f"[skill:{skill.skill_id}] Unpack a PPTX into an XML working directory.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "output_dir": {"type": "string"},
                    },
                    "required": ["path", "output_dir"],
                },
                invoke=lambda arguments, bound_skill=skill: self.pptx_unpack_xml_tool(bound_skill, arguments),
                source=f"skill:{skill.skill_id}",
                metadata={"skill_id": skill.skill_id, "adapter_profile": PPTX_ADAPTER_PROFILE},
            ),
        ]

    def build_script_tools(self, resolved: ResolvedSkillSet) -> list[Tool]:
        tools: list[Tool] = []
        for skill in resolved.skills:
            for definition in skill.manifest.scripts:
                script_tool = SkillScriptTool(skill=skill, definition=definition)
                tools.append(
                    Tool(
                        name=script_tool.full_name,
                        description=f"[skill:{skill.skill_id}] {definition.description}",
                        input_schema=definition.input_schema,
                        invoke=lambda arguments, bound_tool=script_tool: self.run_script_tool(bound_tool, arguments),
                        source=f"skill:{skill.skill_id}",
                        metadata={
                            "skill_id": skill.skill_id,
                            "skill_name": skill.display_name or skill.name,
                            "script_name": definition.name,
                            "script_path": definition.path,
                            "timeout_sec": definition.timeout_sec,
                        },
                    )
                )
        return tools

    def run_script_tool(self, tool: SkillScriptTool, arguments: dict[str, Any] | None = None) -> ToolResult:
        args = arguments if isinstance(arguments, dict) else {}
        script_path = tool.definition.absolute_path.resolve()
        if not script_path.exists():
            return ToolResult.from_error(f"skill script missing: {tool.definition.path}")
        if tool.skill.root_path.resolve() not in script_path.parents:
            return ToolResult.from_error(f"skill script escapes skill directory: {tool.definition.path}")

        payload = json.dumps(args, ensure_ascii=False)
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            completed = subprocess.run(
                [sys.executable, str(script_path)],
                input=payload,
                capture_output=True,
                text=True,
                timeout=tool.definition.timeout_sec,
                cwd=str(tool.skill.root_path),
                encoding="utf-8",
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return ToolResult.from_error(f"skill script timed out after {tool.definition.timeout_sec}s", raw=exc)
        except Exception as exc:
            return ToolResult.from_error(str(exc), raw=exc)

        stdout = str(completed.stdout or "").strip()
        stderr = str(completed.stderr or "").strip()
        if completed.returncode != 0:
            detail = stderr or stdout or f"skill script failed with exit code {completed.returncode}"
            return ToolResult.from_error(
                detail,
                raw={"returncode": completed.returncode, "stderr": stderr, "stdout": stdout},
            )
        if not stdout:
            return ToolResult.from_error("skill script returned empty stdout", raw={"stderr": stderr})
        try:
            parsed = json.loads(stdout)
        except Exception as exc:
            return ToolResult.from_error(
                f"skill script stdout was not valid JSON: {exc}",
                raw={"stdout": stdout, "stderr": stderr},
            )
        return self._tool_result_from_script_output(parsed, stderr=stderr)

    def pptx_extract_text_tool(self, skill: SkillRecord, arguments: dict[str, Any] | None = None) -> ToolResult:
        args = arguments if isinstance(arguments, dict) else {}
        target_path = str(args.get("path") or "").strip()
        if not target_path:
            return ToolResult.from_error("path cannot be empty")
        source = Path(target_path).expanduser().resolve()
        if not source.exists() or not source.is_file():
            return ToolResult.from_error(f"pptx file not found: {source}")
        if source.suffix.lower() not in {".pptx", ".ppt"}:
            return ToolResult.from_error("path must point to a .pptx or .ppt file")
        command: list[str] | None = None
        if importlib.util.find_spec("markitdown") is not None:
            command = [sys.executable, "-m", "markitdown", str(source)]
        elif shutil.which("markitdown") is not None:
            command = ["markitdown", str(source)]
        if command is None:
            return ToolResult.from_error(
                "markitdown is required for skill.pptx.extract_text; install it before using this tool"
            )
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                cwd=str(skill.root_path),
                check=False,
            )
        except Exception as exc:
            return ToolResult.from_error(f"failed to run markitdown: {exc}", raw=exc)
        if completed.returncode != 0:
            detail = str(completed.stderr or completed.stdout or "").strip() or "markitdown failed"
            return ToolResult.from_error(detail, raw={"stdout": completed.stdout, "stderr": completed.stderr})
        content = str(completed.stdout or "").strip()
        return ToolResult.from_value({"path": str(source), "content": content, "adapter_profile": skill.adapter_profile})

    def pptx_thumbnail_tool(self, skill: SkillRecord, arguments: dict[str, Any] | None = None) -> ToolResult:
        return self._run_pptx_helper_script(
            skill,
            arguments,
            candidates=("scripts/thumbnail.py", "scripts/render_preview.py"),
            dependency_hint=(
                "PPTX preview helper is not available. Install a compatible preview helper "
                "inside the skill package, for example scripts/thumbnail.py."
            ),
        )

    def pptx_unpack_xml_tool(self, skill: SkillRecord, arguments: dict[str, Any] | None = None) -> ToolResult:
        args = arguments if isinstance(arguments, dict) else {}
        target_path = str(args.get("path") or "").strip()
        output_dir = str(args.get("output_dir") or "").strip()
        if not target_path or not output_dir:
            return ToolResult.from_error("path and output_dir are required")
        source = Path(target_path).expanduser().resolve()
        destination = Path(output_dir).expanduser().resolve()
        if not source.exists() or not source.is_file():
            return ToolResult.from_error(f"pptx file not found: {source}")
        destination.mkdir(parents=True, exist_ok=True)
        try:
            with ZipFile(source, "r") as archive:
                archive.extractall(destination)
        except Exception as exc:
            return ToolResult.from_error(f"failed to unpack pptx: {exc}", raw=exc)
        return ToolResult.from_value(
            {
                "path": str(source),
                "output_dir": str(destination),
                "adapter_profile": skill.adapter_profile,
            }
        )

    def _run_pptx_helper_script(
        self,
        skill: SkillRecord,
        arguments: dict[str, Any] | None,
        *,
        candidates: tuple[str, ...],
        dependency_hint: str,
    ) -> ToolResult:
        script_path = None
        for candidate in candidates:
            potential = (skill.root_path / candidate).resolve()
            if potential.exists() and potential.is_file():
                script_path = potential
                break
        if script_path is None:
            return ToolResult.from_error(dependency_hint)
        if skill.root_path.resolve() not in script_path.parents:
            return ToolResult.from_error(f"skill adapter script escapes skill directory: {script_path}")
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        payload = json.dumps(arguments if isinstance(arguments, dict) else {}, ensure_ascii=False)
        try:
            completed = subprocess.run(
                [sys.executable, str(script_path)],
                input=payload,
                capture_output=True,
                text=True,
                cwd=str(skill.root_path),
                encoding="utf-8",
                env=env,
                check=False,
            )
        except Exception as exc:
            return ToolResult.from_error(f"failed to run adapter helper: {exc}", raw=exc)
        if completed.returncode != 0:
            detail = str(completed.stderr or completed.stdout or "").strip() or "adapter helper failed"
            return ToolResult.from_error(detail, raw={"stdout": completed.stdout, "stderr": completed.stderr})
        stdout = str(completed.stdout or "").strip()
        if not stdout:
            return ToolResult.from_value({"ok": True})
        try:
            parsed = json.loads(stdout)
        except Exception:
            return ToolResult.from_value({"stdout": stdout})
        return self._tool_result_from_script_output(parsed)

    def _tool_result_from_script_output(self, value: Any, *, stderr: str = "") -> ToolResult:
        if isinstance(value, dict) and "ok" in value:
            if not bool(value.get("ok")):
                error = str(value.get("error") or value.get("message") or stderr or "skill script failed")
                return ToolResult.from_error(error, raw=value)
            structured = value.get("structured_data", value.get("result", value))
            content = value.get("content")
            if content is None:
                content = _safe_json_text(structured)
            return ToolResult(
                ok=True,
                content=_safe_json_text(content),
                structured_data=structured,
                raw=value,
            )
        return ToolResult.from_value(value)

    def build_tool_bridge(self, base_bridge: Any, resolved: ResolvedSkillSet) -> "SkillAwareToolBridge":
        return SkillAwareToolBridge(base_bridge=base_bridge, runtime=self, resolved=resolved)


class SkillAwareToolBridge:
    def __init__(self, base_bridge: Any, runtime: SkillRuntime, resolved: ResolvedSkillSet) -> None:
        self.base_bridge = base_bridge
        self.runtime = runtime
        self.resolved = resolved
        self._skill_registry = ToolRegistry()
        for tool in self._all_skill_tools():
            self._skill_registry.register(tool, overwrite=True)

    def list_registered_tools(self) -> list[Tool]:
        tools = self._filtered_base_tools()
        tools.extend(self._skill_registry.list())
        return tools

    def list_tools(self) -> list[dict[str, Any]]:
        return [tool.to_llm_schema() for tool in self.list_registered_tools()]

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        name = str(tool_name or "").strip()
        if not name:
            return ToolResult.from_error("tool name cannot be empty")
        if name.startswith("skill."):
            try:
                return self._skill_registry.invoke(name, arguments)
            except KeyError:
                return ToolResult.from_error(f"skill tool not found: {name}")
        if not self._is_tool_allowed(name):
            return ToolResult.from_error(f"tool not allowed by active skills: {name}")
        return self.base_bridge.call_tool(name, arguments)

    def _all_skill_tools(self) -> list[Tool]:
        return [
            *self.resolved.resource_tools,
            *self.resolved.adapter_tools,
            *self.resolved.script_tools,
        ]

    def _filtered_base_tools(self) -> list[Tool]:
        if self.base_bridge is None or not hasattr(self.base_bridge, "list_registered_tools"):
            return []
        try:
            raw_tools = self.base_bridge.list_registered_tools()
            tools = list(raw_tools) if raw_tools is not None else []
        except Exception:
            return []
        if not self.resolved.tool_allowlist:
            return tools
        return [tool for tool in tools if self._is_tool_allowed(tool.name)]

    def _is_tool_allowed(self, tool_name: str) -> bool:
        if not self.resolved.tool_allowlist:
            return True
        return any(tool_name_matches_pattern(tool_name, pattern) for pattern in self.resolved.tool_allowlist)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.base_bridge, name)
