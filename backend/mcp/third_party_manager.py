from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(name or "").strip())
    return cleaned.strip("._-") or "mcp_server"


class ThirdPartyMCPManager:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.base_dir = root_dir / "third_party_mcp"
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def list_manifests(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for path in self.base_dir.glob("*/manifest.json"):
            try:
                manifest = self.load_manifest(path)
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
        return manifest

    def validate_manifest(self, manifest: dict[str, Any], server_dir: Path | None = None) -> dict[str, Any]:
        data = json.loads(json.dumps(manifest))
        name = _safe_name(str(data.get("name") or ""))
        version = str(data.get("version") or "0.1.0").strip() or "0.1.0"
        enabled = bool(data.get("enabled", True))
        transport = str(data.get("transport") or "stdio").strip().lower()
        runtime = str(data.get("runtime") or "").strip().lower()
        entry = data.get("entry")
        install = data.get("install")
        env = data.get("env", {})

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
        if runtime == "python" and command not in {"python", "uv"}:
            raise ValueError("python runtime only allows entry.command=python or uv")
        if runtime == "node" and command not in {"node", "npm"}:
            raise ValueError("node runtime only allows entry.command=node or npm")
        if not isinstance(args, list) or not all(isinstance(v, str) for v in args):
            raise ValueError("manifest.entry.args must be a string array")
        if not isinstance(env, dict):
            raise ValueError("manifest.env must be an object")
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
            "entry": {"command": command, "args": args},
            "install": {
                "type": install_type,
                "requirements": str(install.get("requirements") or "requirements.txt"),
                "package_json": bool(install.get("package_json", True)),
            },
            "description": str(data.get("description") or "").strip(),
            "env": safe_env,
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

    def ensure_installed(self, manifest_path: str | Path) -> dict[str, Any]:
        manifest = self.load_manifest(manifest_path)
        runtime = manifest["runtime"]
        install_type = manifest["install"]["type"]
        server_dir = Path(manifest["server_dir"])

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

    def build_command(self, manifest: dict[str, Any]) -> list[str]:
        server_dir = Path(manifest["server_dir"])
        runtime = manifest["runtime"]
        entry = manifest["entry"]
        args = list(entry.get("args", []))
        if runtime == "python":
            return [str(self._venv_python(server_dir))] + args
        return [str(entry.get("command") or "node")] + args

    def _ensure_manifest_exists(self, server_dir: Path, preferred_name: str = "") -> Path:
        manifest_path = server_dir / "manifest.json"
        if manifest_path.exists():
            return manifest_path
        converted = self._build_manifest_from_mcp_server_json(server_dir, preferred_name)
        manifest_path.write_text(json.dumps(converted, ensure_ascii=False, indent=2), encoding="utf-8")
        return manifest_path

    def _load_or_convert_manifest_preview(self, server_dir: Path, preferred_name: str = "") -> dict[str, Any]:
        manifest_path = server_dir / "manifest.json"
        if manifest_path.exists():
            return self.load_manifest(manifest_path)
        return self._build_manifest_from_mcp_server_json(server_dir, preferred_name)

    def _build_manifest_from_mcp_server_json(self, server_dir: Path, preferred_name: str = "") -> dict[str, Any]:
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

        raw_command = str(config.get("command") or "").strip()
        raw_args = config.get("args", [])
        if not raw_command:
            raise ValueError("mcp-server.json selected server missing command")
        if not isinstance(raw_args, list) or not all(isinstance(v, str) for v in raw_args):
            raise ValueError("mcp-server.json args must be a string array")

        base_cmd = Path(raw_command).name.lower()
        runtime = "node" if ("node" in base_cmd or base_cmd == "npm") else "python"
        entry_command = "npm" if base_cmd == "npm" else ("node" if runtime == "node" else "python")
        install_type = "node_npm" if runtime == "node" else "python_uv"

        raw_env = config.get("env", {})
        safe_env: dict[str, str] = {}
        if isinstance(raw_env, dict):
            for k, v in raw_env.items():
                if isinstance(k, str):
                    safe_env[k] = str(v)

        manifest = {
            "name": _safe_name(preferred_name or key or server_dir.name),
            "version": "0.1.0",
            "enabled": True,
            "transport": "stdio",
            "runtime": runtime,
            "entry": {
                "command": entry_command,
                "args": list(raw_args),
            },
            "install": {
                "type": install_type,
                "requirements": "requirements.txt",
                "package_json": True,
            },
            "description": f"Imported from mcp-server.json (server: {key})",
            "env": safe_env,
        }
        return self.validate_manifest(manifest, server_dir)

    def _ensure_python_installed(self, server_dir: Path, manifest: dict[str, Any]) -> None:
        venv_dir = server_dir / ".venv"
        venv_python = self._venv_python(server_dir)
        run_env = self._build_run_env(server_dir, manifest)
        if not venv_python.exists():
            subprocess.run(["uv", "venv", str(venv_dir)], check=True, cwd=str(server_dir), env=run_env)

        pyproject = server_dir / "pyproject.toml"
        req_file = server_dir / str(manifest["install"].get("requirements") or "requirements.txt")
        if pyproject.exists():
            subprocess.run(
                ["uv", "sync", "--project", str(server_dir), "--python", str(venv_python)],
                check=True,
                cwd=str(server_dir),
                env=run_env,
            )
            return
        if req_file.exists():
            subprocess.run(
                ["uv", "pip", "install", "--python", str(venv_python), "-r", str(req_file)],
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
        cmd = ["npm", "ci"] if lock_file.exists() else ["npm", "install"]
        subprocess.run(cmd, check=True, cwd=str(server_dir))

    def _venv_python(self, server_dir: Path) -> Path:
        if os.name == "nt":
            return server_dir / ".venv" / "Scripts" / "python.exe"
        return server_dir / ".venv" / "bin" / "python"

    def _build_run_env(self, server_dir: Path, manifest: dict[str, Any]) -> dict[str, str]:
        env = dict(os.environ)
        extra = manifest.get("env", {})
        if isinstance(extra, dict):
            for k, v in extra.items():
                if isinstance(k, str):
                    env[k] = str(v)
        env.setdefault("UV_CACHE_DIR", str((server_dir / ".uv-cache").resolve()))
        env.setdefault("UV_TOOL_DIR", str((server_dir / ".uv-tools").resolve()))
        return env
