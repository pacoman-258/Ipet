from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .mcp.local_server import LocalMCPServer
from .mcp.stdio_client import StdioMCPClient
from .mcp.third_party_manager import ThirdPartyMCPManager
from .tool_runtime import Tool, ToolRegistry, ToolResult


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
        self.registry = ToolRegistry()
        self._registry_snapshot = ""
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
        self.registry = ToolRegistry()
        self._registry_snapshot = self._registered_server_snapshot()
        for tool in self.local.get_tools():
            self.registry.register(tool)

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
                self._load_cached_server_tools(name, status, manifest)
            except Exception as exc:
                status["install_status"] = status["install_status"] if status["install_status"] != "not_installed" else "failed"
                status["health_status"] = "failed"
                status["error"] = str(exc)
            self.third_party_status[name] = status

    def list_tools(self) -> list[dict[str, Any]]:
        self._refresh_if_registry_changed()
        return self.registry.to_llm_schemas()

    def list_registered_tools(self) -> list[Tool]:
        self._refresh_if_registry_changed()
        return self.registry.list()

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        self._refresh_if_registry_changed()
        return self.registry.invoke(tool_name, arguments)

    def list_servers(self) -> list[dict[str, Any]]:
        self._refresh_if_registry_changed()
        ordered_names = [str(item.get("name") or "") for item in self._iter_registered_servers()]
        names = [name for name in ordered_names if name in self.third_party_status]
        names.extend([name for name in self.third_party_status if name not in names])
        return [dict(self.third_party_status[name]) for name in names]

    def health(self) -> dict[str, Any]:
        self._refresh_if_registry_changed()
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

    def get_server_config(self, name: str) -> dict[str, Any] | None:
        return self._find_server_config(name)

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
        client = StdioMCPClient(
            command=self.manager.build_command(manifest),
            cwd=Path(str(manifest.get("workdir_path") or manifest["server_dir"])),
            timeout_sec=self.timeout_sec,
            env=self.manager._build_client_env(manifest),
            protocol=str(manifest.get("protocol") or "content_length"),
        )
        client.start()
        tools = client.list_tools()
        self.manager.save_tool_cache(manifest["manifest_path"], tools)
        self.third_party_clients[name] = client
        status["health_status"] = "online"
        status["error"] = ""
        normalized_specs: list[dict[str, Any]] = []
        for tool in tools:
            normalized_tool = self._normalize_tool(name, tool)
            self.registry.register(normalized_tool, overwrite=True)
            normalized_specs.append(normalized_tool.to_llm_schema())
        status["tools"] = normalized_specs
        return client

    def _load_cached_server_tools(self, name: str, status: dict[str, Any], manifest: dict[str, Any]) -> None:
        cached_tools = self.manager.load_tool_cache(manifest["manifest_path"])
        status["install_status"] = "ready"
        status["health_status"] = "ready" if cached_tools else "not_loaded"
        status["error"] = ""
        normalized_specs: list[dict[str, Any]] = []
        for tool in cached_tools:
            normalized_tool = self._normalize_tool(name, tool)
            self.registry.register(normalized_tool, overwrite=True)
            normalized_specs.append(normalized_tool.to_llm_schema())
        status["tools"] = normalized_specs

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

    def _normalize_tool(self, server_name: str, tool: dict[str, Any]) -> Tool:
        if not isinstance(tool, dict):
            return Tool(
                name=f"{server_name}.unknown_tool",
                description=f"[{server_name}]",
                input_schema={"type": "object", "properties": {}},
                invoke=lambda _arguments, failed_server=server_name: ToolResult.from_error(
                    f"invalid tool definition from server: {failed_server}"
                ),
                source=server_name,
                metadata={"server_name": server_name, "remote_name": ""},
            )

        remote_name = ""
        description = ""
        parameters: dict[str, Any] = {"type": "object", "properties": {}}

        if "name" in tool and "function" not in tool:
            remote_name = str(tool.get("name") or "").strip()
            description = str(tool.get("description") or "").strip()
            input_schema = tool.get("inputSchema")
            if isinstance(input_schema, dict):
                parameters = input_schema
        else:
            fn = tool.get("function", {}) if isinstance(tool, dict) else {}
            remote_name = str(fn.get("name") or "").strip()
            description = str(fn.get("description") or "").strip()
            if isinstance(fn.get("parameters"), dict):
                parameters = fn.get("parameters")

        unique_name = f"{server_name}.{remote_name}" if remote_name else f"{server_name}.unknown_tool"
        visible_description = f"[{server_name}] {description}".strip()
        return Tool(
            name=unique_name,
            description=visible_description,
            input_schema=parameters,
            invoke=lambda arguments, bound_server=server_name, bound_remote=remote_name: self._call_remote_tool(
                bound_server,
                bound_remote,
                arguments,
            ),
            source=server_name,
            metadata={"server_name": server_name, "remote_name": remote_name},
        )

    def _call_remote_tool(self, server_name: str, remote_tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        client = self._ensure_server_client(server_name)
        try:
            result = client.call_tool(remote_tool_name, arguments)
        except Exception as exc:
            self._mark_server_failed(server_name, str(exc))
            return ToolResult.from_error(str(exc), raw=exc)
        return ToolResult.from_value(result)

    def _third_party_config(self) -> dict[str, Any]:
        data = self.tooling_config.get("third_party", {})
        return data if isinstance(data, dict) else {"enabled": False, "servers": []}

    def _registered_server_snapshot(self) -> str:
        items: list[dict[str, str | bool]] = []
        for item in self._iter_registered_servers():
            items.append(
                {
                    "name": str(item.get("name") or "").strip(),
                    "manifest_path": str(item.get("manifest_path") or "").strip(),
                    "enabled": bool(item.get("enabled", True)),
                }
            )
        return json.dumps(items, ensure_ascii=False, sort_keys=True)

    def _refresh_if_registry_changed(self) -> None:
        snapshot = self._registered_server_snapshot()
        if snapshot != self._registry_snapshot:
            self.reload()

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
