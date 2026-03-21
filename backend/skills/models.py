from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.tool_runtime import Tool


@dataclass(frozen=True)
class SkillDiscoveryCandidate:
    source_type: str
    package_root: Path
    discovery_root: Path


@dataclass(frozen=True)
class SkillResourceEntry:
    path: str
    category: str
    absolute_path: Path
    size_bytes: int = 0


@dataclass(frozen=True)
class SkillScriptDefinition:
    name: str
    description: str
    path: str
    absolute_path: Path
    input_schema: dict[str, Any]
    timeout_sec: int
    runner: str = "json_stdin"

    @property
    def tool_name(self) -> str:
        return self.name


@dataclass(frozen=True)
class SkillManifest:
    version: str = "1.0.0"
    tool_allowlist: tuple[str, ...] = ()
    scripts: tuple[SkillScriptDefinition, ...] = ()


@dataclass(frozen=True)
class SkillRecord:
    skill_id: str
    name: str
    description: str
    source_type: str
    root_path: Path
    skill_md_path: Path
    prompt_body: str
    aliases: tuple[str, ...] = ()
    manifest: SkillManifest = field(default_factory=SkillManifest)
    has_manifest: bool = False
    ok: bool = True
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    references: tuple[str, ...] = ()
    assets: tuple[str, ...] = ()
    resources: tuple[SkillResourceEntry, ...] = ()
    display_name: str = ""
    short_description: str = ""
    has_openai_metadata: bool = False
    source_repo: str = ""
    source_ref: str = ""
    source_subdir: str = ""
    compatibility_mode: str = "native"
    adapter_profile: str = ""
    platform: str = "native"
    package_root: Path = field(default_factory=Path)
    discovery_root: Path = field(default_factory=Path)
    capabilities: tuple[str, ...] = ()
    site_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def valid(self) -> bool:
        return self.ok

    def to_summary(self, *, default_active: bool = False) -> dict[str, Any]:
        return {
            "id": self.skill_id,
            "aliases": list(self.aliases),
            "name": self.display_name or self.name,
            "description": self.short_description or self.description,
            "source": self.source_type,
            "source_type": self.source_type,
            "path": str(self.root_path),
            "valid": self.ok,
            "error": "; ".join(self.errors),
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "default_active": bool(default_active),
            "has_openai_metadata": self.has_openai_metadata,
            "has_manifest": self.has_manifest,
            "tool_allowlist": list(self.manifest.tool_allowlist),
            "scripts": [
                {
                    "name": item.name,
                    "description": item.description,
                    "path": item.path,
                    "timeout_sec": item.timeout_sec,
                    "tool_name": f"skill.{self.skill_id}.{item.name}",
                    "runner": item.runner,
                }
                for item in self.manifest.scripts
            ],
            "script_count": len(self.manifest.scripts),
            "allowlist_count": len(self.manifest.tool_allowlist),
            "references": list(self.references),
            "assets": list(self.assets),
            "resource_count": len(self.resources),
            "source_repo": self.source_repo,
            "source_ref": self.source_ref,
            "source_subdir": self.source_subdir,
            "compatibility_mode": self.compatibility_mode,
            "adapter_profile": self.adapter_profile,
            "platform": self.platform,
            "package_root": str(self.package_root or self.root_path),
            "discovery_root": str(self.discovery_root or self.root_path),
            "capabilities": list(self.capabilities),
            "site_metadata": dict(self.site_metadata),
        }


@dataclass(frozen=True)
class SkillImportResult:
    skill: SkillRecord
    target_path: Path
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResolvedSkillSet:
    skill_ids: tuple[str, ...] = ()
    skills: tuple[SkillRecord, ...] = ()
    prompt_text: str = ""
    tool_allowlist: tuple[str, ...] = ()
    resource_tools: tuple[Tool, ...] = ()
    adapter_tools: tuple[Tool, ...] = ()
    script_tools: tuple[Tool, ...] = ()
    defaulted: bool = False
