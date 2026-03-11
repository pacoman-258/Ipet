from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .mcp.local_server import LocalMCPServer
from .mcp.stdio_client import StdioMCPClient
from .mcp.third_party_manager import ThirdPartyMCPManager


class MCPBridge:
    def __init__(self, tooling_config: dict[str, Any], root_dir: Path) -> None:
        self.tooling_config = tooling_config or {}
        self.root_dir = root_dir
        allowlist = self.tooling_config.get("file_allowlist") or [str(root_dir)]
        net_allow = self.tooling_config.get("network_allow_domains") or []
        self.timeout_sec = int(self.tooling_config.get("tool_timeout_sec", 180))
        self.local = LocalMCPServer(file_allowlist=list(allowlist), network_allow_domains=list(net_allow))
        self.manager = ThirdPartyMCPManager(root_dir)
        self.third_party_clients: dict[str, StdioMCPClient] = {}
        self.third_party_status: dict[str, dict[str, Any]] = {}
        self.tool_routes: dict[str, str] = {}
        self.reload()

    def stop(self) -> None:
        for client in self.third_party_clients.values():
            try:
                client.stop()
            except Exception:
                pass
        self.third_party_clients = {}

    def reload(self) -> None:
        self.stop()
        self.third_party_status = {}
        self.tool_routes = {}

        third_cfg = self._third_party_config()
        for server_cfg in self._iter_registered_servers():
            name = str(server_cfg.get("name") or "").strip()
            if not name:
                continue
            status = self._base_server_status(server_cfg)
            try:
                manifest = self.manager.load_manifest(server_cfg["manifest_path"])
                self._populate_server_runtime(status, manifest)
                if not bool(third_cfg.get("enabled", False)) or not status["enabled"]:
                    status["health_status"] = "disabled"
                    self.third_party_status[name] = status
                    continue
                self._start_server_client(name, status, manifest)
            except Exception as exc:
                status["install_status"] = status["install_status"] if status["install_status"] != "not_installed" else "failed"
                status["health_status"] = "failed"
                status["error"] = str(exc)
            self.third_party_status[name] = status

    def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        seen: set[str] = set()
        for status in self.list_servers():
            if status.get("health_status") != "online":
                continue
            for tool in status.get("tools", []):
                name = str(tool.get("function", {}).get("name") or "")
                if name and name not in seen:
                    tools.append(tool)
                    seen.add(name)
        for tool in self.local.list_tools():
            name = str(tool.get("function", {}).get("name") or "")
            if name and name not in seen:
                tools.append(tool)
                seen.add(name)
        return tools

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        server_name = self.tool_routes.get(tool_name)
        if server_name:
            client = self._ensure_server_client(server_name)
            try:
                return client.call_tool(tool_name, arguments)
            except Exception as exc:
                self._mark_server_failed(server_name, str(exc))
                raise
        return self.local.call(tool_name, arguments)

    def list_servers(self) -> list[dict[str, Any]]:
        ordered_names = [str(item.get("name") or "") for item in self._iter_registered_servers()]
        names = [name for name in ordered_names if name in self.third_party_status]
        names.extend([name for name in self.third_party_status if name not in names])
        return [dict(self.third_party_status[name]) for name in names]

    def health(self) -> dict[str, Any]:
        servers = self.list_servers()
        online = sum(1 for item in servers if item.get("health_status") == "online")
        return {"enabled": bool(self._third_party_config().get("enabled", False)), "servers": servers, "online": online}

    def install_from_git(self, repo_url: str, name: str = "") -> dict[str, Any]:
        manifest_path = self.manager.install_from_git(repo_url, name)
        manifest = self.manager.load_manifest(manifest_path)
        return {
            "name": manifest["name"],
            "manifest_path": manifest["manifest_path"],
            "runtime": manifest["runtime"],
        }

    def register_local(self, source_path: str, name: str = "") -> dict[str, Any]:
        manifest_path = self.manager.register_local_directory(source_path, name)
        manifest = self.manager.load_manifest(manifest_path)
        return {
            "name": manifest["name"],
            "manifest_path": manifest["manifest_path"],
            "runtime": manifest["runtime"],
        }

    def toggle_server(self, name: str, enabled: bool) -> dict[str, Any]:
        server = self._find_server_config(name)
        if not server:
            raise FileNotFoundError(f"server not found: {name}")
        manifest = self.manager.set_enabled(server["manifest_path"], enabled)
        return {
            "name": manifest["name"],
            "enabled": manifest["enabled"],
            "manifest_path": server["manifest_path"],
        }

    def discover_third_party_manifests(self) -> list[dict[str, Any]]:
        return self.manager.list_manifests()

    def _base_server_status(self, server_cfg: dict[str, Any]) -> dict[str, Any]:
        name = str(server_cfg.get("name") or "").strip()
        return {
            "name": name,
            "enabled": bool(server_cfg.get("enabled", True)),
            "version": "",
            "runtime": "",
            "source_type": str(server_cfg.get("source_type", "local")),
            "source": str(server_cfg.get("source", "")),
            "manifest_path": str(server_cfg.get("manifest_path", "")),
            "install_status": "not_installed",
            "health_status": "offline",
            "tools": [],
            "error": "",
        }

    def _populate_server_runtime(self, status: dict[str, Any], manifest: dict[str, Any]) -> None:
        status.update(
            {
                "version": str(manifest.get("version", "")),
                "runtime": str(manifest.get("runtime", "")),
                "manifest_path": str(manifest.get("manifest_path", "")),
                "enabled": bool(manifest.get("enabled", status.get("enabled", True))),
            }
        )

    def _start_server_client(self, name: str, status: dict[str, Any], manifest: dict[str, Any]) -> StdioMCPClient:
        install_info = self.manager.ensure_installed(manifest["manifest_path"])
        status["install_status"] = str(install_info.get("install_status", "ready"))
        env = manifest.get("env")
        env_map = None
        if isinstance(env, dict):
            env_map = dict(os.environ)
            for k, v in env.items():
                if isinstance(k, str):
                    env_map[k] = str(v)
        client = StdioMCPClient(
            command=self.manager.build_command(manifest),
            cwd=Path(manifest["server_dir"]),
            timeout_sec=self.timeout_sec,
            env=env_map,
        )
        client.start()
        tools = client.list_tools()
        self.third_party_clients[name] = client
        status["health_status"] = "online"
        status["error"] = ""
        status["tools"] = [self._normalize_tool_spec(name, tool) for tool in tools]
        for tool in status["tools"]:
            tool_name = tool.get("function", {}).get("name")
            if isinstance(tool_name, str) and tool_name:
                self.tool_routes[tool_name] = name
        return client

    def _ensure_server_client(self, server_name: str) -> StdioMCPClient:
        status = self.third_party_status.get(server_name)
        if not isinstance(status, dict):
            raise FileNotFoundError(f"third-party mcp server not found: {server_name}")
        if status.get("health_status") == "disabled":
            raise RuntimeError(f"third-party mcp server is disabled: {server_name}")

        client = self.third_party_clients.get(server_name)
        if client and client.is_healthy():
            return client

        self._drop_server_client(server_name)

        server_cfg = self._find_server_config(server_name)
        if not server_cfg:
            status["health_status"] = "failed"
            status["error"] = f"server config missing: {server_name}"
            raise FileNotFoundError(status["error"])

        try:
            manifest = self.manager.load_manifest(server_cfg["manifest_path"])
            self._populate_server_runtime(status, manifest)
            return self._start_server_client(server_name, status, manifest)
        except Exception as exc:
            status["install_status"] = status["install_status"] if status["install_status"] != "not_installed" else "failed"
            status["health_status"] = "failed"
            status["error"] = str(exc)
            raise

    def _drop_server_client(self, server_name: str) -> None:
        client = self.third_party_clients.pop(server_name, None)
        if client is not None:
            try:
                client.stop()
            except Exception:
                pass

    def _mark_server_failed(self, server_name: str, error: str) -> None:
        self._drop_server_client(server_name)
        status = self.third_party_status.get(server_name)
        if isinstance(status, dict):
            status["health_status"] = "failed"
            status["error"] = str(error)

    def _normalize_tool_spec(self, server_name: str, tool: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(tool, dict):
            return {"type": "function", "function": {"name": "", "description": f"[{server_name}]", "parameters": {"type": "object"}}}

        # Standard MCP tools/list shape: {name, description, inputSchema, ...}
        if "name" in tool and "function" not in tool:
            name = str(tool.get("name") or "").strip()
            description = str(tool.get("description") or "").strip()
            input_schema = tool.get("inputSchema")
            parameters = input_schema if isinstance(input_schema, dict) else {"type": "object", "properties": {}}
            return {
                "type": "function",
                "function": {
                    "name": name,
                    "description": f"[{server_name}] {description}".strip(),
                    "parameters": parameters,
                },
            }

        # Already OpenAI-style tool schema
        fn = tool.get("function", {}) if isinstance(tool, dict) else {}
        name = str(fn.get("name") or "").strip()
        description = str(fn.get("description") or "").strip()
        parameters = fn.get("parameters")
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": f"[{server_name}] {description}".strip(),
                "parameters": parameters if isinstance(parameters, dict) else {"type": "object", "properties": {}},
            },
        }

    def _third_party_config(self) -> dict[str, Any]:
        data = self.tooling_config.get("third_party", {})
        return data if isinstance(data, dict) else {"enabled": False, "servers": []}

    def _iter_registered_servers(self) -> list[dict[str, Any]]:
        third_cfg = self._third_party_config()
        configured = third_cfg.get("servers", [])
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        if isinstance(configured, list):
            for item in configured:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                manifest_path = str(item.get("manifest_path") or "").strip()
                if not name or not manifest_path:
                    continue
                out.append(dict(item))
                seen.add(name)
        for manifest in self.manager.list_manifests():
            name = str(manifest.get("name") or "").strip()
            if not name or name in seen:
                continue
            out.append(
                {
                    "name": name,
                    "enabled": bool(manifest.get("enabled", False)),
                    "source_type": "local",
                    "source": str(manifest.get("server_dir", "")),
                    "manifest_path": str(manifest.get("manifest_path", "")),
                    "runtime": str(manifest.get("runtime", "")),
                }
            )
        return out

    def _find_server_config(self, name: str) -> dict[str, Any] | None:
        target = str(name or "").strip()
        for item in self._iter_registered_servers():
            if str(item.get("name") or "").strip() == target:
                return item
        return None
