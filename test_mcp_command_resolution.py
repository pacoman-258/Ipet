from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock
import shutil
import uuid

from backend.mcp.stdio_client import StdioMCPClient
from backend.mcp.third_party_manager import ThirdPartyMCPManager


class MCPCommandResolutionTests(unittest.TestCase):
    def _workspace_temp_root(self) -> Path:
        root = Path.cwd() / f".tmp_mcp_cmd_{uuid.uuid4().hex}"
        root.mkdir(parents=True, exist_ok=False)
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        return root

    def test_build_command_prefers_resolved_windows_launcher(self) -> None:
        root = self._workspace_temp_root()
        manager = ThirdPartyMCPManager(root)
        manifest_path = manager.register_server_config(
            {
                "mcpServers": {
                    "playwright": {
                        "command": "npx",
                        "args": ["@playwright/mcp@latest"],
                    }
                }
            }
        )
        manifest = manager.load_manifest(manifest_path)
        with mock.patch("backend.mcp.third_party_manager.os.name", "nt"), mock.patch(
            "backend.mcp.third_party_manager.shutil.which",
            side_effect=lambda value: "C:\\Program Files\\nodejs\\npx.cmd" if value == "npx.cmd" else None,
        ):
            command = manager.build_command(manifest)
        self.assertEqual(command[0], "C:\\Program Files\\nodejs\\npx.cmd")
        self.assertEqual(command[1:], ["-y", "@playwright/mcp@latest"])
        self.assertEqual(manifest["protocol"], "jsonline")

    def test_register_server_config_injects_npx_yes_flag(self) -> None:
        root = self._workspace_temp_root()
        manager = ThirdPartyMCPManager(root)
        manifest_path = manager.register_server_config(
            {
                "mcpServers": {
                    "playwright": {
                        "command": "npx",
                        "args": ["@playwright/mcp@latest"],
                    }
                }
            }
        )
        manifest = manager.load_manifest(manifest_path)
        self.assertEqual(manifest["entry"]["command"], "npx")
        self.assertEqual(manifest["entry"]["args"], ["-y", "@playwright/mcp@latest"])
        self.assertEqual(manifest["protocol"], "jsonline")

    def test_stdio_client_reports_missing_executable_clearly(self) -> None:
        client = StdioMCPClient(command=["missing-npx"], cwd=Path.cwd())
        with mock.patch("backend.mcp.stdio_client.subprocess.Popen", side_effect=FileNotFoundError(2, "missing")):
            with self.assertRaises(FileNotFoundError) as ctx:
                client.start()
        self.assertIn("executable was not found", str(ctx.exception))

    def test_build_client_env_sets_project_local_npm_dirs_for_node_runtime(self) -> None:
        root = self._workspace_temp_root()
        manager = ThirdPartyMCPManager(root)
        manifest_path = manager.register_server_config(
            {
                "mcpServers": {
                    "playwright": {
                        "command": "npx",
                        "args": ["@playwright/mcp@latest"],
                    }
                }
            }
        )
        manifest = manager.load_manifest(manifest_path)
        env = manager._build_client_env(manifest)
        self.assertTrue(env["npm_config_cache"].endswith(".npm-cache"))
        self.assertTrue(env["npm_config_prefix"].endswith(".npm-prefix"))
        self.assertTrue(Path(env["npm_config_cache"]).exists())
        self.assertTrue(Path(env["npm_config_prefix"]).exists())

    def test_prepare_server_materializes_direct_npx_package_to_node_entry(self) -> None:
        root = self._workspace_temp_root()
        manager = ThirdPartyMCPManager(root)
        manifest_path = manager.register_server_config(
            {
                "mcpServers": {
                    "playwright": {
                        "command": "npx",
                        "args": ["@playwright/mcp@latest", "--headless"],
                    }
                }
            }
        )
        server_dir = manifest_path.parent
        package_dir = server_dir / "node_modules" / "@playwright" / "mcp"
        package_dir.mkdir(parents=True, exist_ok=True)
        (package_dir / "package.json").write_text(
            '{"name":"@playwright/mcp","bin":{"playwright-mcp":"cli.js"}}',
            encoding="utf-8",
        )
        (package_dir / "cli.js").write_text("console.log('ok')", encoding="utf-8")
        manifest = manager.load_manifest(manifest_path)
        with mock.patch("backend.mcp.third_party_manager.subprocess.run") as run_mock:
            materialized = manager._materialize_npx_manifest(manifest_path, manifest)
        run_mock.assert_called_once()
        self.assertEqual(materialized["entry"]["command"], "node")
        self.assertEqual(materialized["entry"]["args"][0], "node_modules/@playwright/mcp/cli.js")
        self.assertEqual(materialized["entry"]["args"][1:], ["--headless"])

    def test_materialized_npx_manifest_detects_jsonline_sdk_transport(self) -> None:
        root = self._workspace_temp_root()
        manager = ThirdPartyMCPManager(root)
        manifest_path = manager.register_server_config(
            {
                "mcpServers": {
                    "bilibili-search": {
                        "command": "npx",
                        "args": ["bilibili-mcp-js"],
                    }
                }
            }
        )
        server_dir = manifest_path.parent
        package_dir = server_dir / "node_modules" / "bilibili-mcp-js"
        package_dir.mkdir(parents=True, exist_ok=True)
        (package_dir / "package.json").write_text(
            '{"name":"bilibili-mcp-js","bin":{"bilibili-mcp":"dist/index.js"}}',
            encoding="utf-8",
        )
        dist_dir = package_dir / "dist"
        dist_dir.mkdir(parents=True, exist_ok=True)
        (dist_dir / "index.js").write_text(
            'import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";',
            encoding="utf-8",
        )
        sdk_dir = server_dir / "node_modules" / "@modelcontextprotocol" / "sdk" / "dist" / "esm" / "shared"
        sdk_dir.mkdir(parents=True, exist_ok=True)
        (sdk_dir / "stdio.js").write_text(
            "export function serializeMessage(message) { return JSON.stringify(message) + '\\n'; }\n"
            "export class ReadBuffer { readMessage() { return this._buffer.indexOf('\\n'); } }\n",
            encoding="utf-8",
        )
        manifest = manager.load_manifest(manifest_path)
        with mock.patch("backend.mcp.third_party_manager.subprocess.run") as run_mock:
            materialized = manager._materialize_npx_manifest(manifest_path, manifest)
        run_mock.assert_called_once()
        self.assertEqual(materialized["entry"]["command"], "node")
        self.assertEqual(materialized["protocol"], "jsonline")


if __name__ == "__main__":
    unittest.main()
