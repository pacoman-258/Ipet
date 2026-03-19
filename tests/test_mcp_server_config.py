from __future__ import annotations

import unittest
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
        self.assertEqual(manifest["name"], "playwright_mcp")
        self.assertEqual(manifest["runtime"], "node")
        self.assertEqual(manifest["entry"]["command"], "npx")
        self.assertEqual(manifest["entry"]["args"], ["-y", "@playwright/mcp@latest"])
        self.assertEqual(manifest["protocol"], "jsonline")
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


if __name__ == "__main__":
    unittest.main()
