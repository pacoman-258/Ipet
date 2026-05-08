from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Any

from ..storage_paths import hermes_mcp_dir, legacy_third_party_mcp_dir
from .stdio_client import StdioMCPClient


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(name or "").strip())
    return cleaned.strip("._-") or "mcp_server"


def _common_exec_search_dirs(server_dir: Path | None = None) -> list[str]:
    home = Path.home()
    candidates: list[Path] = []
    if server_dir is not None:
        if os.name == "nt":
            candidates.append(server_dir / ".venv" / "Scripts")
        else:
            candidates.append(server_dir / ".venv" / "bin")
    if os.name == "nt":
        candidates.append(home / "AppData" / "Roaming" / "Python" / "Scripts")
    else:
        candidates.extend(
            [
                home / ".local" / "bin",
                home / ".cargo" / "bin",
                Path("/opt/homebrew/bin"),
                Path("/opt/homebrew/sbin"),
                Path("/usr/local/bin"),
                Path("/usr/local/sbin"),
            ]
        )

    seen: set[str] = set()
    resolved: list[str] = []
    for candidate in candidates:
        text = str(candidate)
        if not text or text in seen or not candidate.exists():
            continue
        seen.add(text)
        resolved.append(text)
    return resolved


def _prepend_exec_search_path(env: dict[str, str], server_dir: Path | None = None) -> str:
    current = [item for item in str(env.get("PATH") or "").split(os.pathsep) if item]
    prefixes: list[str] = []
    seen = set(current)
    for candidate in _common_exec_search_dirs(server_dir):
        if candidate in seen:
            continue
        seen.add(candidate)
        prefixes.append(candidate)
    env["PATH"] = os.pathsep.join(prefixes + current)
    return env["PATH"]


IGNORED_DISCOVERY_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".uv-cache",
    ".uv-tools",
    "dist",
    "build",
}


class ThirdPartyMCPManager:
    def __init__(
        self,
        root_dir: Path,
        base_dir: Path | None = None,
        legacy_base_dir: Path | None = None,
    ) -> None:
        self.root_dir = Path(root_dir)
        self.base_dir = Path(base_dir or hermes_mcp_dir(self.root_dir))
        self.legacy_base_dir = None
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def list_manifests(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        for base_dir in self._manifest_roots():
            for path in base_dir.glob("*/manifest.json"):
                try:
                    manifest = self.load_manifest(path)
                    name = str(manifest.get("name") or "").strip()
                    if name and name in seen_names:
                        continue
                    if name:
                        seen_names.add(name)
                    out.append(manifest)
                except Exception:
                    continue
        return out

    def load_manifest(self, manifest_path: str | Path) -> dict[str, Any]:
        path = Path(manifest_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("manifest must be a JSON object")
        manifest = self.validate_manifest(data, path.parent)
        manifest["manifest_path"] = str(path.resolve())
        manifest["server_dir"] = str(path.parent.resolve())
        manifest["workdir_path"] = str(self._manifest_workdir_path(manifest).resolve())
        manifest["storage_scope"] = self._storage_scope_for_path(path)
        return manifest

    def validate_manifest(self, manifest: dict[str, Any], server_dir: Path | None = None) -> dict[str, Any]:
        data = json.loads(json.dumps(manifest))
        name = _safe_name(str(data.get("name") or ""))
        version = str(data.get("version") or "0.1.0").strip() or "0.1.0"
        enabled = bool(data.get("enabled", True))
        transport = str(data.get("transport") or "stdio").strip().lower()
        runtime = str(data.get("runtime") or "").strip().lower()
        workdir = str(data.get("workdir") or ".").strip() or "."
        entry = data.get("entry")
        install = data.get("install")
        env = data.get("env", {})
        protocol = str(data.get("protocol") or "").strip().lower()

        if transport != "stdio":
            raise ValueError("only stdio transport is supported")
        if runtime not in {"python", "node"}:
            raise ValueError("runtime must be python or node")

        if entry is None:
            if "command" in data or "args" in data:
                entry = {
                    "command": data.get("command", "python" if runtime == "python" else "node"),
                    "args": data.get("args", []),
                }
            else:
                raise ValueError("manifest.entry is required")
        if not isinstance(entry, dict):
            raise ValueError("manifest.entry must be an object")

        command = str(entry.get("command") or "").strip().lower()
        args = entry.get("args", [])
        if runtime == "python" and command not in {"python", "python3", "uv", "uvx"}:
            raise ValueError("python runtime only allows entry.command=python/python3/uv/uvx")
        if runtime == "node" and command not in {"node", "npm", "npx", "pnpm", "bunx"}:
            raise ValueError("node runtime only allows entry.command=node/npm/npx/pnpm/bunx")
        if not isinstance(args, list) or not all(isinstance(v, str) for v in args):
            raise ValueError("manifest.entry.args must be a string array")
        normalized_args = list(args)
        if runtime == "node" and command == "npx" and not any(item in {"-y", "--yes"} for item in normalized_args):
            normalized_args = ["-y"] + normalized_args
        if not protocol:
            protocol = "jsonline" if self._looks_like_jsonline_mcp(command, normalized_args) else "content_length"
        if not isinstance(env, dict):
            raise ValueError("manifest.env must be an object")
        if protocol not in {"content_length", "jsonline"}:
            raise ValueError("manifest.protocol must be content_length or jsonline")
        safe_env: dict[str, str] = {}
        for k, v in env.items():
            if not isinstance(k, str):
                continue
            safe_env[k] = str(v)

        if install is None:
            install = {"type": "python_uv" if runtime == "python" else "node_npm"}
        if not isinstance(install, dict):
            raise ValueError("manifest.install must be an object")

        install_type = str(install.get("type") or "none").strip().lower()
        allowed_install_types = {"python": {"python_uv", "none"}, "node": {"node_npm", "none"}}
        if install_type not in allowed_install_types[runtime]:
            raise ValueError(f"invalid install.type for runtime {runtime}")

        normalized = {
            "name": name,
            "version": version,
            "enabled": enabled,
            "transport": transport,
            "runtime": runtime,
            "workdir": workdir.replace("\\", "/"),
            "entry": {"command": command, "args": normalized_args},
            "install": {
                "type": install_type,
                "requirements": str(install.get("requirements") or "requirements.txt"),
                "package_json": bool(install.get("package_json", True)),
            },
            "description": str(data.get("description") or "").strip(),
            "env": safe_env,
            "protocol": protocol,
        }
        if server_dir is not None:
            normalized["server_dir"] = str(server_dir.resolve())
        return normalized

    def register_manifest(self, name: str, manifest: dict[str, Any]) -> Path:
        safe_name = _safe_name(name or manifest.get("name") or "")
        target = self.base_dir / safe_name
        target.mkdir(parents=True, exist_ok=True)
        normalized = self.validate_manifest(manifest, target)
        path = target / "manifest.json"
        path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def register_local_directory(self, source_dir: str | Path, name: str = "") -> Path:
        src = Path(source_dir).resolve()
        if not src.exists() or not src.is_dir():
            raise FileNotFoundError(str(src))
        seed_manifest = self._load_or_convert_manifest_preview(src, name)
        safe_name = _safe_name(name or seed_manifest.get("name") or src.name)
        target = self.base_dir / safe_name
        if target.exists():
            raise FileExistsError(str(target))
        shutil.copytree(
            src,
            target,
            ignore=shutil.ignore_patterns(
                ".git",
                ".venv",
                ".uv-cache",
                ".uv-tools",
                "__pycache__",
                ".tmp",
                "downloads",
            ),
        )
        manifest_path = self._ensure_manifest_exists(target, safe_name)
        copied_manifest = self.load_manifest(manifest_path)
        (target / "manifest.json").write_text(
            json.dumps(copied_manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return target / "manifest.json"

    def register_server_config(self, config_payload: dict[str, Any], name: str = "") -> Path:
        if not isinstance(config_payload, dict):
            raise ValueError("mcp server config must be a JSON object")

        preferred_name = _safe_name(str(name or "").strip())
        server_name = preferred_name
        config = config_payload

        if "mcpServers" in config_payload:
            servers = config_payload.get("mcpServers")
            if not isinstance(servers, dict) or not servers:
                raise ValueError("mcpServers must be a non-empty object")
            if preferred_name and preferred_name in servers:
                server_name = preferred_name
            elif len(servers) == 1:
                server_name = str(next(iter(servers.keys())))
            else:
                raise ValueError("multiple mcpServers found, please provide a matching name")
            config = servers.get(server_name)
            if not isinstance(config, dict):
                raise ValueError(f"invalid MCP server config: {server_name}")
        elif preferred_name:
            server_name = preferred_name

        if not server_name:
            command = str(config.get("command") or "").strip()
            server_name = _safe_name(self._guess_name_from_command(Path(command).name.lower(), list(config.get("args", []))) or "mcp_server")

        target = self.base_dir / _safe_name(server_name)
        if target.exists():
            raise FileExistsError(str(target))
        target.mkdir(parents=True, exist_ok=False)
        manifest = self._manifest_from_mcp_server_config(
            key=server_name,
            config=config,
            server_dir=target,
            preferred_name=server_name,
            manifest_root=target,
            description="Created from frontend MCP server config",
        )
        manifest_path = target / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return manifest_path

    def install_from_git(self, repo_url: str, name: str = "") -> Path:
        repo = str(repo_url or "").strip()
        if not repo:
            raise ValueError("repo_url cannot be empty")
        safe_name = _safe_name(name or Path(repo.rstrip("/")).stem)
        target = self.base_dir / safe_name
        if target.exists():
            raise FileExistsError(str(target))
        subprocess.run(["git", "clone", "--depth", "1", repo, str(target)], check=True, cwd=str(self.root_dir))
        manifest_path = self._ensure_manifest_exists(target, safe_name)
        manifest = self.load_manifest(manifest_path)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return manifest_path

    def set_enabled(self, manifest_path: str | Path, enabled: bool) -> dict[str, Any]:
        path = Path(manifest_path)
        manifest = self.load_manifest(path)
        manifest["enabled"] = bool(enabled)
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return manifest

    def delete_server(self, manifest_path: str | Path) -> None:
        manifest = self.load_manifest(manifest_path)
        server_dir = Path(str(manifest.get("server_dir") or "")).resolve()
        managed_roots = [item.resolve() for item in self._manifest_roots()]
        if not any(root != server_dir and root in server_dir.parents for root in managed_roots):
            raise ValueError(f"refusing to delete non-managed server directory: {server_dir}")
        shutil.rmtree(server_dir, ignore_errors=False)

    def ensure_installed(self, manifest_path: str | Path) -> dict[str, Any]:
        manifest = self.load_manifest(manifest_path)
        runtime = manifest["runtime"]
        install_type = manifest["install"]["type"]
        server_dir = self._manifest_workdir_path(manifest)

        if install_type == "none":
            return {
                "install_status": "ready",
                "runtime": runtime,
                "venv_python": str(self._venv_python(server_dir)) if runtime == "python" else "",
            }

        if runtime == "python":
            self._ensure_python_installed(server_dir, manifest)
            return {
                "install_status": "ready",
                "runtime": runtime,
                "venv_python": str(self._venv_python(server_dir)),
            }

        self._ensure_node_installed(server_dir)
        return {"install_status": "ready", "runtime": runtime}

    def prepare_server(self, manifest_path: str | Path, timeout_sec: int = 180) -> dict[str, Any]:
        manifest = self.load_manifest(manifest_path)
        manifest = self._materialize_npx_manifest(manifest_path, manifest, timeout_sec=max(1, int(timeout_sec)))
        install_info = self.ensure_installed(manifest_path)
        client = StdioMCPClient(
            command=self.build_command(manifest),
            cwd=Path(str(manifest.get("workdir_path") or manifest["server_dir"])),
            timeout_sec=max(1, int(timeout_sec)),
            env=self._build_client_env(manifest),
            protocol=str(manifest.get("protocol") or "content_length"),
        )
        try:
            client.start()
            tools = client.list_tools()
            self.save_tool_cache(manifest_path, tools if isinstance(tools, list) else [])
        finally:
            client.stop()
        return {
            **install_info,
            "prepared": True,
            "tools": tools if isinstance(tools, list) else [],
            "tool_count": len(tools) if isinstance(tools, list) else 0,
        }

    def load_tool_cache(self, manifest_path: str | Path) -> list[dict[str, Any]]:
        cache_path = self._tool_cache_path(manifest_path)
        if not cache_path.exists():
            return []
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        return data if isinstance(data, list) else []

    def save_tool_cache(self, manifest_path: str | Path, tools: list[dict[str, Any]]) -> Path:
        cache_path = self._tool_cache_path(manifest_path)
        cache_path.write_text(json.dumps(tools, ensure_ascii=False, indent=2), encoding="utf-8")
        return cache_path

    def build_command(self, manifest: dict[str, Any]) -> list[str]:
        server_dir = self._manifest_workdir_path(manifest)
        runtime = manifest["runtime"]
        entry = manifest["entry"]
        command = str(entry.get("command") or "").strip().lower()
        args = list(entry.get("args", []))
        if runtime == "python":
            if command in {"python", "python3"}:
                return [str(self._venv_python(server_dir))] + args
            return [self._resolve_spawn_command(command, server_dir)] + args
        return [self._resolve_spawn_command(command or "node", server_dir)] + args

    def _resolve_spawn_command(self, command: str, server_dir: Path | None = None) -> str:
        text = str(command or "").strip()
        if not text:
            return text
        search_env = dict(os.environ)
        _prepend_exec_search_path(search_env, server_dir)
        candidates = [text]
        if os.name == "nt" and "." not in Path(text).name:
            candidates.extend([f"{text}.cmd", f"{text}.exe", f"{text}.bat"])
        for candidate in candidates:
            resolved = shutil.which(candidate, path=search_env.get("PATH"))
            if resolved:
                return resolved
        return text

    def _ensure_manifest_exists(self, server_dir: Path, preferred_name: str = "") -> Path:
        manifest_path = server_dir / "manifest.json"
        if manifest_path.exists():
            return manifest_path
        converted = self._discover_or_convert_manifest(server_dir, preferred_name)
        manifest_path.write_text(json.dumps(converted, ensure_ascii=False, indent=2), encoding="utf-8")
        return manifest_path

    def _load_or_convert_manifest_preview(self, server_dir: Path, preferred_name: str = "") -> dict[str, Any]:
        manifest_path = server_dir / "manifest.json"
        if manifest_path.exists():
            return self.load_manifest(manifest_path)
        return self._discover_or_convert_manifest(server_dir, preferred_name)

    def _discover_or_convert_manifest(self, server_dir: Path, preferred_name: str = "") -> dict[str, Any]:
        try:
            source_path = self._discover_manifest_source(server_dir)
        except FileNotFoundError:
            return self._infer_manifest_from_repo(server_dir, preferred_name)
        if source_path.name == "manifest.json":
            data = json.loads(source_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("manifest must be a JSON object")
            if preferred_name:
                data["name"] = preferred_name
            if source_path.parent != server_dir:
                data["workdir"] = source_path.parent.resolve().relative_to(server_dir.resolve()).as_posix()
            return self.validate_manifest(data, server_dir)
        return self._build_manifest_from_mcp_server_json(source_path.parent, preferred_name, server_dir)

    def _discover_manifest_source(self, server_dir: Path) -> Path:
        direct_candidates = [server_dir / "manifest.json", server_dir / "mcp-server.json"]
        for candidate in direct_candidates:
            if candidate.exists():
                return candidate

        recursive_candidates: list[Path] = []
        for pattern in ("manifest.json", "mcp-server.json"):
            for candidate in server_dir.rglob(pattern):
                parts = set(candidate.relative_to(server_dir).parts[:-1])
                if parts & IGNORED_DISCOVERY_DIRS:
                    continue
                recursive_candidates.append(candidate)

        if not recursive_candidates:
            raise FileNotFoundError(f"manifest.json or mcp-server.json not found under {server_dir}")

        recursive_candidates.sort(
            key=lambda path: (
                len(path.relative_to(server_dir).parts),
                0 if path.name == "manifest.json" else 1,
                str(path).lower(),
            )
        )
        return recursive_candidates[0]

    def _infer_manifest_from_repo(self, server_dir: Path, preferred_name: str = "") -> dict[str, Any]:
        for builder in (
            self._infer_manifest_from_readme,
            self._infer_manifest_from_package_json,
            self._infer_manifest_from_pyproject,
        ):
            manifest = builder(server_dir, preferred_name)
            if manifest is not None:
                return manifest
        raise FileNotFoundError(f"manifest.json or mcp-server.json not found, and no MCP launch command could be inferred under {server_dir}")

    def _iter_repo_files(self, server_dir: Path, names: tuple[str, ...]) -> list[Path]:
        direct = [server_dir / name for name in names if (server_dir / name).exists()]
        recursive: list[Path] = []
        for name in names:
            for candidate in server_dir.rglob(name):
                parts = set(candidate.relative_to(server_dir).parts[:-1])
                if parts & IGNORED_DISCOVERY_DIRS:
                    continue
                recursive.append(candidate)
        ordered = list(dict.fromkeys(direct + recursive))
        ordered.sort(key=lambda path: (len(path.relative_to(server_dir).parts), str(path).lower()))
        return ordered

    def _iter_fenced_code_blocks(self, text: str) -> list[tuple[str, str]]:
        blocks: list[tuple[str, str]] = []
        for match in re.finditer(r"```([^\n`]*)\n(.*?)```", text, re.DOTALL):
            raw_lang = str(match.group(1) or "").strip().lower()
            body = str(match.group(2) or "").strip()
            lang = raw_lang.split()[0] if raw_lang else ""
            if body:
                blocks.append((lang, body))
        return blocks

    def _manifest_from_mcp_server_config(
        self,
        key: str,
        config: dict[str, Any],
        server_dir: Path,
        preferred_name: str = "",
        manifest_root: Path | None = None,
        description: str = "",
    ) -> dict[str, Any]:
        raw_command = str(config.get("command") or "").strip()
        raw_args = config.get("args", [])
        if not raw_command:
            raise ValueError("selected MCP server missing command")
        if not isinstance(raw_args, list) or not all(isinstance(v, str) for v in raw_args):
            raise ValueError("selected MCP server args must be a string array")

        base_cmd = Path(raw_command).name.lower()
        runtime = "node" if base_cmd in {"node", "npm", "npx", "pnpm", "bunx"} else "python"
        install_type = self._infer_install_type(runtime, base_cmd, list(raw_args))

        raw_env = config.get("env", {})
        safe_env: dict[str, str] = {}
        if isinstance(raw_env, dict):
            for env_key, env_value in raw_env.items():
                if isinstance(env_key, str):
                    safe_env[env_key] = str(env_value)

        manifest = {
            "name": _safe_name(preferred_name or key or server_dir.name),
            "version": "0.1.0",
            "enabled": True,
            "transport": "stdio",
            "runtime": runtime,
            "workdir": ".",
            "entry": {"command": base_cmd, "args": list(raw_args)},
            "install": {
                "type": install_type,
                "requirements": "requirements.txt",
                "package_json": True,
            },
            "description": description or f"Inferred MCP server ({key})",
            "env": safe_env,
            "protocol": "jsonline" if self._looks_like_jsonline_mcp(base_cmd, list(raw_args)) else "content_length",
        }
        root = manifest_root or server_dir
        if server_dir != root:
            manifest["workdir"] = server_dir.resolve().relative_to(root.resolve()).as_posix()
        return self.validate_manifest(manifest, root)

    def _build_manifest_from_mcp_server_json(
        self,
        server_dir: Path,
        preferred_name: str = "",
        manifest_root: Path | None = None,
    ) -> dict[str, Any]:
        mcp_server_path = server_dir / "mcp-server.json"
        if not mcp_server_path.exists():
            raise FileNotFoundError(f"manifest.json or mcp-server.json not found: {server_dir}")
        data = json.loads(mcp_server_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("mcp-server.json must be a JSON object")
        servers = data.get("mcpServers")
        if not isinstance(servers, dict) or not servers:
            raise ValueError("mcp-server.json missing mcpServers")

        key = ""
        preferred = str(preferred_name or "").strip()
        if preferred and preferred in servers:
            key = preferred
        else:
            key = next(iter(servers.keys()))
        config = servers.get(key)
        if not isinstance(config, dict):
            raise ValueError("mcp-server.json selected server config is invalid")
        return self._manifest_from_mcp_server_config(
            key=key,
            config=config,
            server_dir=server_dir,
            preferred_name=preferred_name,
            manifest_root=manifest_root,
            description=f"Imported from mcp-server.json (server: {key})",
        )

    def _infer_manifest_from_readme(self, server_dir: Path, preferred_name: str = "") -> dict[str, Any] | None:
        for readme_path in self._iter_repo_files(server_dir, ("README.md", "README.MD", "readme.md", "README.txt")):
            try:
                text = readme_path.read_text(encoding="utf-8")
            except Exception:
                text = readme_path.read_text(encoding="utf-8", errors="ignore")
            for lang, body in self._iter_fenced_code_blocks(text):
                manifest = self._manifest_from_readme_block(server_dir, readme_path.parent, lang, body, preferred_name)
                if manifest is not None:
                    return manifest
        return None

    def _manifest_from_readme_block(
        self,
        server_root: Path,
        block_dir: Path,
        lang: str,
        body: str,
        preferred_name: str = "",
    ) -> dict[str, Any] | None:
        if lang in {"json", "jsonc", "javascript", "js", ""}:
            try:
                data = json.loads(body)
            except Exception:
                data = None
            if isinstance(data, dict):
                servers = data.get("mcpServers")
                if isinstance(servers, dict) and servers:
                    key = preferred_name if preferred_name and preferred_name in servers else next(iter(servers.keys()))
                    config = servers.get(key)
                    if isinstance(config, dict):
                        return self._manifest_from_mcp_server_config(
                            key=key,
                            config=config,
                            server_dir=block_dir,
                            preferred_name=preferred_name or key,
                            manifest_root=server_root,
                            description=f"Inferred from {block_dir.name}/README code block",
                        )

        for raw_line in body.splitlines():
            manifest = self._manifest_from_command_line(raw_line, server_root, block_dir, preferred_name)
            if manifest is not None:
                return manifest
        return None

    def _manifest_from_command_line(
        self,
        raw_line: str,
        server_root: Path,
        block_dir: Path,
        preferred_name: str = "",
    ) -> dict[str, Any] | None:
        line = str(raw_line or "").strip()
        if not line or "mcp" not in line.lower():
            return None
        try:
            tokens = shlex.split(line, posix=True)
        except Exception:
            return None
        if not tokens:
            return None

        while tokens and "=" in tokens[0] and not tokens[0].startswith(("/", ".", "-")):
            maybe_key, _, maybe_value = tokens[0].partition("=")
            if maybe_key and maybe_value:
                tokens = tokens[1:]
                continue
            break
        if not tokens:
            return None

        command = Path(tokens[0]).name.lower()
        args = tokens[1:]
        if command not in {"npx", "npm", "node", "pnpm", "bunx", "uvx", "uv", "python", "python3"}:
            return None
        if command == "pnpm" and not args:
            return None
        if command == "npm" and args[:1] not in (["exec"], ["run"], ["start"]):
            return None
        if command == "uv" and args[:1] != ["run"]:
            return None
        runtime = "node" if command in {"npx", "npm", "node", "pnpm", "bunx"} else "python"
        name = _safe_name(preferred_name or self._guess_name_from_command(command, args) or block_dir.name)
        manifest = {
            "name": name,
            "version": "0.1.0",
            "enabled": True,
            "transport": "stdio",
            "runtime": runtime,
            "workdir": block_dir.resolve().relative_to(server_root.resolve()).as_posix() if block_dir != server_root else ".",
            "entry": {"command": command, "args": args},
            "install": {
                "type": self._infer_install_type(runtime, command, args),
                "requirements": "requirements.txt",
                "package_json": True,
            },
            "description": f"Inferred from {block_dir.name}/README command",
            "env": {},
        }
        return self.validate_manifest(manifest, server_root)

    def _guess_name_from_command(self, command: str, args: list[str]) -> str:
        non_option_args = [item for item in args if not str(item).startswith("-")]
        if command in {"npx", "bunx", "uvx"} and non_option_args:
            return Path(non_option_args[0]).name
        if command == "pnpm" and len(non_option_args) >= 2 and non_option_args[0] == "dlx":
            return Path(non_option_args[1]).name
        if command in {"python", "python3"} and len(non_option_args) >= 2 and non_option_args[0] == "-m":
            return non_option_args[1]
        if command == "uv" and len(non_option_args) >= 2 and non_option_args[0] == "run":
            return non_option_args[1]
        if non_option_args:
            return Path(non_option_args[0]).stem
        return ""

    def _infer_install_type(self, runtime: str, command: str, args: list[str]) -> str:
        if runtime == "node":
            if command in {"npx", "bunx"}:
                return "none"
            if command == "pnpm" and args[:1] == ["dlx"]:
                return "none"
            return "node_npm"
        if command == "uvx":
            return "none"
        return "python_uv"

    def _infer_manifest_from_package_json(self, server_dir: Path, preferred_name: str = "") -> dict[str, Any] | None:
        for package_path in self._iter_repo_files(server_dir, ("package.json",)):
            try:
                data = json.loads(package_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            package_dir = package_path.parent
            bin_field = data.get("bin")
            local_entry = self._resolve_package_bin(package_dir, bin_field)
            if local_entry is not None:
                name = _safe_name(preferred_name or str(data.get("name") or "") or package_dir.name)
                manifest = {
                    "name": name,
                    "version": str(data.get("version") or "0.1.0"),
                    "enabled": True,
                    "transport": "stdio",
                    "runtime": "node",
                    "workdir": package_dir.resolve().relative_to(server_dir.resolve()).as_posix() if package_dir != server_dir else ".",
                    "entry": {"command": "node", "args": [local_entry]},
                    "install": {"type": "node_npm", "requirements": "requirements.txt", "package_json": True},
                    "description": str(data.get("description") or "Inferred from package.json"),
                    "env": {},
                }
                return self.validate_manifest(manifest, server_dir)

            package_name = str(data.get("name") or "").strip()
            if package_name and self._looks_like_mcp_package(package_name, data):
                name = _safe_name(preferred_name or package_name or package_dir.name)
                manifest = {
                    "name": name,
                    "version": str(data.get("version") or "0.1.0"),
                    "enabled": True,
                    "transport": "stdio",
                    "runtime": "node",
                    "workdir": package_dir.resolve().relative_to(server_dir.resolve()).as_posix() if package_dir != server_dir else ".",
                    "entry": {"command": "npx", "args": ["-y", f"{package_name}@latest"]},
                    "install": {"type": "none", "requirements": "requirements.txt", "package_json": True},
                    "description": str(data.get("description") or "Inferred from package.json package name"),
                    "env": {},
                }
                return self.validate_manifest(manifest, server_dir)
        return None

    def _resolve_package_bin(self, package_dir: Path, bin_field: Any) -> str | None:
        candidate = ""
        if isinstance(bin_field, str):
            candidate = bin_field
        elif isinstance(bin_field, dict) and bin_field:
            first = next(iter(bin_field.values()))
            if isinstance(first, str):
                candidate = first
        candidate = str(candidate or "").strip()
        if not candidate:
            return None
        path = (package_dir / candidate).resolve()
        if not path.exists() or not path.is_file():
            return None
        return path.relative_to(package_dir.resolve()).as_posix()

    def _looks_like_mcp_package(self, package_name: str, package_data: dict[str, Any]) -> bool:
        if "mcp" in package_name.lower():
            return True
        description = str(package_data.get("description") or "").lower()
        if "mcp" in description:
            return True
        keywords = package_data.get("keywords", [])
        return isinstance(keywords, list) and any("mcp" in str(item).lower() for item in keywords)

    def _infer_manifest_from_pyproject(self, server_dir: Path, preferred_name: str = "") -> dict[str, Any] | None:
        for pyproject_path in self._iter_repo_files(server_dir, ("pyproject.toml",)):
            try:
                data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            project = data.get("project", {})
            if not isinstance(project, dict):
                continue
            project_dir = pyproject_path.parent
            scripts = project.get("scripts", {})
            if isinstance(scripts, dict):
                for script_name in scripts:
                    if "mcp" not in str(script_name).lower():
                        continue
                    manifest = {
                        "name": _safe_name(preferred_name or script_name or project.get("name") or project_dir.name),
                        "version": str(project.get("version") or "0.1.0"),
                        "enabled": True,
                        "transport": "stdio",
                        "runtime": "python",
                        "workdir": project_dir.resolve().relative_to(server_dir.resolve()).as_posix() if project_dir != server_dir else ".",
                        "entry": {"command": "uv", "args": ["run", str(script_name)]},
                        "install": {"type": "python_uv", "requirements": "requirements.txt", "package_json": True},
                        "description": str(project.get("description") or "Inferred from pyproject.toml script"),
                        "env": {},
                    }
                    return self.validate_manifest(manifest, server_dir)

            project_name = str(project.get("name") or "").strip()
            if project_name and "mcp" in project_name.lower():
                manifest = {
                    "name": _safe_name(preferred_name or project_name or project_dir.name),
                    "version": str(project.get("version") or "0.1.0"),
                    "enabled": True,
                    "transport": "stdio",
                    "runtime": "python",
                    "workdir": project_dir.resolve().relative_to(server_dir.resolve()).as_posix() if project_dir != server_dir else ".",
                    "entry": {"command": "uvx", "args": [project_name]},
                    "install": {"type": "none", "requirements": "requirements.txt", "package_json": True},
                    "description": str(project.get("description") or "Inferred from pyproject.toml project name"),
                    "env": {},
                }
                return self.validate_manifest(manifest, server_dir)
        return None

    def _ensure_python_installed(self, server_dir: Path, manifest: dict[str, Any]) -> None:
        venv_dir = server_dir / ".venv"
        venv_python = self._venv_python(server_dir)
        run_env = self._build_run_env(server_dir, manifest)
        uv_command = self._resolve_spawn_command("uv", server_dir)
        if not venv_python.exists():
            subprocess.run([uv_command, "venv", str(venv_dir)], check=True, cwd=str(server_dir), env=run_env)

        pyproject = server_dir / "pyproject.toml"
        req_file = server_dir / str(manifest["install"].get("requirements") or "requirements.txt")
        if pyproject.exists():
            subprocess.run(
                [uv_command, "sync", "--project", str(server_dir), "--python", str(venv_python)],
                check=True,
                cwd=str(server_dir),
                env=run_env,
            )
            return
        if req_file.exists():
            subprocess.run(
                [uv_command, "pip", "install", "--python", str(venv_python), "-r", str(req_file)],
                check=True,
                cwd=str(server_dir),
                env=run_env,
            )
            return
        raise FileNotFoundError(f"no pyproject.toml or requirements file found in {server_dir}")

    def _ensure_node_installed(self, server_dir: Path) -> None:
        package_json = server_dir / "package.json"
        if not package_json.exists():
            raise FileNotFoundError(f"package.json not found in {server_dir}")
        lock_file = server_dir / "package-lock.json"
        npm_command = self._resolve_spawn_command("npm", server_dir)
        cmd = [npm_command, "ci"] if lock_file.exists() else [npm_command, "install"]
        env = dict(os.environ)
        _prepend_exec_search_path(env, server_dir)
        subprocess.run(cmd, check=True, cwd=str(server_dir), env=env)

    def _venv_python(self, server_dir: Path) -> Path:
        if os.name == "nt":
            return server_dir / ".venv" / "Scripts" / "python.exe"
        return server_dir / ".venv" / "bin" / "python"

    def _manifest_workdir_path(self, manifest: dict[str, Any]) -> Path:
        server_dir = Path(str(manifest.get("server_dir") or "")).resolve()
        workdir_text = str(manifest.get("workdir") or ".").strip() or "."
        workdir = Path(workdir_text)
        candidate = workdir.resolve() if workdir.is_absolute() else (server_dir / workdir).resolve()
        if not candidate.exists() or not candidate.is_dir():
            raise FileNotFoundError(f"manifest workdir not found: {candidate}")
        return candidate

    def _build_run_env(self, server_dir: Path, manifest: dict[str, Any]) -> dict[str, str]:
        env = dict(os.environ)
        _prepend_exec_search_path(env, server_dir)
        extra = manifest.get("env", {})
        if isinstance(extra, dict):
            for k, v in extra.items():
                if isinstance(k, str):
                    env[k] = str(v)
        env.setdefault("UV_CACHE_DIR", str((server_dir / ".uv-cache").resolve()))
        env.setdefault("UV_TOOL_DIR", str((server_dir / ".uv-tools").resolve()))
        self._apply_node_runtime_env(env, server_dir, manifest)
        return env

    def _build_client_env(self, manifest: dict[str, Any]) -> dict[str, str]:
        env = dict(os.environ)
        server_dir = self._manifest_workdir_path(manifest)
        _prepend_exec_search_path(env, server_dir)
        extra = manifest.get("env", {})
        if isinstance(extra, dict):
            for k, v in extra.items():
                if isinstance(k, str):
                    env[k] = str(v)
        self._apply_node_runtime_env(env, server_dir, manifest)
        return env

    def _tool_cache_path(self, manifest_path: str | Path) -> Path:
        path = Path(manifest_path)
        return path.parent / ".tool_cache.json"

    def _manifest_roots(self) -> list[Path]:
        dirs: list[Path] = []
        seen: set[Path] = set()
        for item in (self.base_dir, self.legacy_base_dir):
            if item is None:
                continue
            resolved = item.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            dirs.append(item)
        return dirs

    def _storage_scope_for_path(self, manifest_path: Path) -> str:
        path = manifest_path.resolve()
        base_dir = self.base_dir.resolve()
        if path == base_dir or base_dir in path.parents:
            return "managed"
        if self.legacy_base_dir is not None:
            legacy_dir = self.legacy_base_dir.resolve()
            if path == legacy_dir or legacy_dir in path.parents:
                return "legacy"
        return "external"

    def _apply_node_runtime_env(self, env: dict[str, str], server_dir: Path, manifest: dict[str, Any]) -> None:
        runtime = str(manifest.get("runtime") or "").strip().lower()
        if runtime != "node":
            return
        npm_cache_dir = (server_dir / ".npm-cache").resolve()
        npm_prefix_dir = (server_dir / ".npm-prefix").resolve()
        npm_cache_dir.mkdir(parents=True, exist_ok=True)
        npm_prefix_dir.mkdir(parents=True, exist_ok=True)
        env.setdefault("npm_config_cache", str(npm_cache_dir))
        env.setdefault("NPM_CONFIG_CACHE", str(npm_cache_dir))
        env.setdefault("npm_config_prefix", str(npm_prefix_dir))
        env.setdefault("NPM_CONFIG_PREFIX", str(npm_prefix_dir))

    def _materialize_npx_manifest(
        self,
        manifest_path: str | Path,
        manifest: dict[str, Any],
        timeout_sec: int = 180,
    ) -> dict[str, Any]:
        entry = manifest.get("entry", {})
        command = str(entry.get("command") or "").strip().lower()
        if manifest.get("runtime") != "node" or command != "npx":
            return manifest

        package_spec, runtime_args = self._extract_direct_npx_package_args(list(entry.get("args", [])))
        if not package_spec:
            return manifest

        server_dir = self._manifest_workdir_path(manifest)
        npm_command = self._resolve_spawn_command("npm", server_dir)
        try:
            install_run = subprocess.run(
                [npm_command, "install", "--no-save", package_spec],
                check=True,
                cwd=str(server_dir),
                env=self._build_client_env(manifest),
                timeout=max(30, int(timeout_sec)),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )
        except subprocess.TimeoutExpired as exc:
            stderr_text = str(exc.stderr or "").strip()
            stdout_text = str(exc.stdout or "").strip()
            detail = stderr_text or stdout_text or f"timed out after {max(30, int(timeout_sec))} seconds"
            raise TimeoutError(f"npm install timed out for {package_spec}: {detail}") from exc
        except subprocess.CalledProcessError as exc:
            stderr_text = str(exc.stderr or "").strip()
            stdout_text = str(exc.stdout or "").strip()
            detail = stderr_text or stdout_text or f"exit code {exc.returncode}"
            raise RuntimeError(f"npm install failed for {package_spec}: {detail}") from exc
        if install_run.stdout:
            _ = install_run.stdout

        package_name = self._package_name_from_spec(package_spec)
        package_dir = server_dir / "node_modules"
        for part in package_name.split("/"):
            package_dir = package_dir / part
        package_json_path = package_dir / "package.json"
        if not package_json_path.exists():
            raise FileNotFoundError(f"installed npx package missing package.json: {package_json_path}")
        package_data = json.loads(package_json_path.read_text(encoding="utf-8"))
        bin_field = package_data.get("bin")
        bin_rel = self._resolve_package_bin(package_dir, bin_field)
        if not bin_rel:
            raise ValueError(f"installed npx package does not expose a runnable bin entry: {package_name}")
        detected_protocol = self._detect_installed_node_protocol(
            server_dir,
            package_dir,
            bin_rel,
            fallback=str(manifest.get("protocol") or "content_length"),
        )

        updated = json.loads(json.dumps(manifest))
        updated["entry"] = {"command": "node", "args": [str((package_dir / bin_rel).resolve().relative_to(server_dir.resolve()).as_posix())] + runtime_args}
        updated["install"]["type"] = "none"
        updated["protocol"] = detected_protocol
        normalized = self.validate_manifest(updated, Path(str(manifest.get("server_dir") or server_dir)))
        manifest_file = Path(manifest_path)
        manifest_file.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.load_manifest(manifest_file)

    def _extract_direct_npx_package_args(self, args: list[str]) -> tuple[str, list[str]]:
        if not isinstance(args, list):
            return "", []
        normalized = [str(item) for item in args]
        package_spec = ""
        runtime_args: list[str] = []
        for idx, item in enumerate(normalized):
            if item in {"-y", "--yes"}:
                continue
            if item.startswith("-"):
                return "", []
            package_spec = item
            runtime_args = normalized[idx + 1 :]
            break
        return package_spec, runtime_args

    def _package_name_from_spec(self, package_spec: str) -> str:
        text = str(package_spec or "").strip()
        if not text:
            return text
        if text.startswith("@"):
            second_at = text.rfind("@")
            if second_at > 0:
                slash = text.find("/")
                if slash != -1 and second_at > slash:
                    return text[:second_at]
            return text
        if "@" in text:
            return text.split("@", 1)[0]
        return text

    def _detect_installed_node_protocol(
        self,
        server_dir: Path,
        package_dir: Path,
        bin_rel: str,
        fallback: str = "content_length",
    ) -> str:
        entry_path = (package_dir / str(bin_rel or "")).resolve()
        candidate_paths = [
            server_dir / "node_modules" / "@modelcontextprotocol" / "sdk" / "dist" / "esm" / "shared" / "stdio.js",
            server_dir / "node_modules" / "@modelcontextprotocol" / "sdk" / "dist" / "cjs" / "shared" / "stdio.js",
            package_dir / "node_modules" / "@modelcontextprotocol" / "sdk" / "dist" / "esm" / "shared" / "stdio.js",
            package_dir / "node_modules" / "@modelcontextprotocol" / "sdk" / "dist" / "cjs" / "shared" / "stdio.js",
            entry_path,
        ]
        for candidate in candidate_paths:
            protocol = self._detect_protocol_from_source_file(candidate)
            if protocol:
                return protocol
        return str(fallback or "content_length").strip().lower() or "content_length"

    def _detect_protocol_from_source_file(self, source_path: Path) -> str:
        if not source_path.exists() or not source_path.is_file():
            return ""
        try:
            text = source_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return ""
        compact = "".join(text.split())
        if "Content-Length:" in text or "content-length" in text.lower():
            return "content_length"
        if "indexOf('\\n')" in compact and "JSON.stringify(" in text and "+'\\n'" in compact:
            return "jsonline"
        if 'indexOf("\\n")' in compact and "JSON.stringify(" in text and '+"\\n"' in compact:
            return "jsonline"
        return ""

    def _looks_like_jsonline_mcp(self, command: str, args: list[str]) -> bool:
        base = str(command or "").strip().lower()
        if base != "npx":
            return False
        package_spec, _runtime_args = self._extract_direct_npx_package_args(args)
        return self._package_name_from_spec(package_spec).lower() == "@playwright/mcp"
