from __future__ import annotations

import unittest
import json
from pathlib import Path
import shutil
import uuid

from backend.mcp.third_party_manager import ThirdPartyMCPManager


class MCPServerConfigTests(unittest.TestCase):
    def _workspace_temp_root(self) -> Path:
        root = Path.cwd() / f".tmp_mcp_test_{uuid.uuid4().hex}"
        root.mkdir(parents=True, exist_ok=False)
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        return root

    def test_register_server_config_from_mcp_servers_shape(self) -> None:
        root = self._workspace_temp_root()
        manager = ThirdPartyMCPManager(root)
        manifest_path = manager.register_server_config(
            {
                "mcpServers": {
                    "playwright_mcp": {
                        "command": "npx",
                        "args": ["@playwright/mcp@latest"],
                    }
                }
            }
        )
        manifest = manager.load_manifest(manifest_path)
        self.assertEqual(manifest_path.parent.parent, root / "Hermes" / "mcp")
        self.assertEqual(manifest["name"], "playwright_mcp")
        self.assertEqual(manifest["runtime"], "node")
        self.assertEqual(manifest["entry"]["command"], "npx")
        self.assertEqual(manifest["entry"]["args"], ["-y", "@playwright/mcp@latest"])
        self.assertEqual(manifest["protocol"], "jsonline")
        self.assertEqual(manifest["install"]["type"], "none")

    def test_register_server_config_from_uvx_basic_memory_shape(self) -> None:
        root = self._workspace_temp_root()
        manager = ThirdPartyMCPManager(root)
        manifest_path = manager.register_server_config(
            {
                "mcpServers": {
                    "basic_memory": {
                        "command": "uvx",
                        "args": ["basic-memory", "mcp"],
                    }
                }
            }
        )
        manifest = manager.load_manifest(manifest_path)
        self.assertEqual(manifest["name"], "basic_memory")
        self.assertEqual(manifest["runtime"], "python")
        self.assertEqual(manifest["entry"]["command"], "uvx")
        self.assertEqual(manifest["entry"]["args"], ["basic-memory", "mcp"])
        self.assertEqual(manifest["install"]["type"], "none")

    def test_delete_server_removes_managed_directory(self) -> None:
        root = self._workspace_temp_root()
        manager = ThirdPartyMCPManager(root)
        manifest_path = manager.register_server_config(
            {
                "mcpServers": {
                    "playwright_mcp": {
                        "command": "npx",
                        "args": ["@playwright/mcp@latest"],
                    }
                }
            }
        )
        server_dir = manifest_path.parent
        self.assertTrue(server_dir.exists())
        manager.delete_server(manifest_path)
        self.assertFalse(server_dir.exists())

    def test_list_manifests_ignores_legacy_directory(self) -> None:
        root = self._workspace_temp_root()
        manager = ThirdPartyMCPManager(root)
        legacy_server = root / "third_party_mcp" / "legacy_server"
        legacy_server.mkdir(parents=True, exist_ok=True)
        legacy_server.joinpath("manifest.json").write_text(
            json.dumps(
                {
                    "name": "legacy_server",
                    "runtime": "node",
                    "transport": "stdio",
                    "entry": {"command": "npx", "args": ["legacy-mcp"]},
                    "install": {"type": "none"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        manifests = manager.list_manifests()

        self.assertEqual(manifests, [])

    def test_register_server_config_writes_to_hermes_and_ignores_legacy(self) -> None:
        root = self._workspace_temp_root()
        hermes_dir = root / "Hermes" / "mcp"
        legacy_dir = root / "third_party_mcp"
        manager = ThirdPartyMCPManager(root, base_dir=hermes_dir, legacy_base_dir=legacy_dir)
        legacy_manifest_dir = legacy_dir / "legacy_server"
        legacy_manifest_dir.mkdir(parents=True)
        (legacy_manifest_dir / "manifest.json").write_text(
            """{
  "name": "legacy_server",
  "version": "0.1.0",
  "enabled": true,
  "transport": "stdio",
  "runtime": "node",
  "entry": {"command": "npx", "args": ["@example/mcp"]},
  "install": {"type": "none"}
}""",
            encoding="utf-8",
        )

        manifest_path = manager.register_server_config(
            {
                "mcpServers": {
                    "hermes_server": {
                        "command": "npx",
                        "args": ["@playwright/mcp@latest"],
                    }
                }
            }
        )
        manifests = {item["name"]: item for item in manager.list_manifests()}

        self.assertEqual(manifest_path.parent.parent, hermes_dir)
        self.assertEqual(manifests["hermes_server"]["storage_scope"], "managed")
        self.assertNotIn("legacy_server", manifests)


if __name__ == "__main__":
    unittest.main()
