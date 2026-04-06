from __future__ import annotations

import json
import unittest
from unittest import mock
from pathlib import Path

from fastapi.testclient import TestClient

import backend.app as backend_app


class SettingsMcpDraftTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(backend_app.app)

    def tearDown(self) -> None:
        self.client.close()

    def test_put_settings_config_creates_pending_mcp_draft(self) -> None:
        with mock.patch.object(
            backend_app,
            "_ensure_mcp_server_from_draft",
            return_value={"name": "playwright", "manifest_path": "x", "runtime": "node"},
        ) as ensure_mock, mock.patch.object(
            backend_app,
            "_save_full_config",
        ) as save_mock, mock.patch.object(
            backend_app,
            "_get_mcp_bridge",
            return_value=mock.Mock(),
        ):
            resp = self.client.put(
                "/api/settings/config",
                json={
                    "config": {"chat": {"tooling": {"third_party": {"enabled": True, "servers": []}}}},
                    "mcp_draft": {
                        "name": "playwright",
                        "config_json": '{"mcpServers":{"playwright":{"command":"npx","args":["@playwright/mcp@latest"]}}}',
                    },
                },
            )

        self.assertEqual(resp.status_code, 200)
        ensure_mock.assert_called_once()
        save_mock.assert_called_once()

    def test_get_settings_config_exposes_basic_memory_preset_and_defaults(self) -> None:
        resp = self.client.get("/api/settings/config")
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        presets = payload["mcp_server_presets"]
        self.assertIn("basic_memory", presets)
        self.assertEqual(presets["basic_memory"]["config"]["mcpServers"]["basic_memory"]["command"], "uvx")
        self.assertEqual(presets["basic_memory"]["config"]["mcpServers"]["basic_memory"]["args"], ["basic-memory", "mcp"])

        long_term_memory = payload["config"]["chat"]["long_term_memory"]
        self.assertFalse(long_term_memory["enabled"])
        self.assertEqual(long_term_memory["project"], "ipet-default")
        self.assertTrue(long_term_memory["read_enabled"])
        self.assertTrue(long_term_memory["write_enabled"])
        self.assertTrue(long_term_memory["ask_before_save"])
        self.assertTrue(long_term_memory["prefer_topic_history"])
        self.assertTrue(long_term_memory["save_from_major_summary"])
        self.assertTrue(long_term_memory["save_on_explicit_request"])

    def test_ensure_mcp_server_from_draft_prepares_before_persisting(self) -> None:
        manager = mock.Mock()
        manager.register_server_config.return_value = Path("third_party_mcp/playwright/manifest.json")
        manager.load_manifest.return_value = {
            "name": "playwright",
            "manifest_path": "third_party_mcp/playwright/manifest.json",
            "runtime": "node",
        }

        with mock.patch.object(
            backend_app,
            "_iter_registered_mcp_server_configs",
            return_value=({"enabled": True, "servers": []}, []),
        ), mock.patch.object(
            backend_app,
            "_load_runtime_tooling_config",
            return_value={"tool_timeout_sec": 45},
        ), mock.patch.object(
            backend_app,
            "_update_third_party_config",
        ) as update_mock, mock.patch.object(
            backend_app,
            "ThirdPartyMCPManager",
            return_value=manager,
        ):
            entry = backend_app._ensure_mcp_server_from_draft(
                "playwright",
                json.dumps(
                    {
                        "mcpServers": {
                            "playwright": {"command": "npx", "args": ["@playwright/mcp@latest"]}
                        }
                    }
                ),
            )

        manager.prepare_server.assert_called_once_with(Path("third_party_mcp/playwright/manifest.json"), timeout_sec=45)
        update_mock.assert_called_once()
        self.assertEqual(entry["name"], "playwright")

    def test_ensure_mcp_server_from_draft_supports_basic_memory_uvx_manifest(self) -> None:
        manager = mock.Mock()
        manager.register_server_config.return_value = Path("third_party_mcp/basic_memory/manifest.json")
        manager.load_manifest.return_value = {
            "name": "basic_memory",
            "manifest_path": "third_party_mcp/basic_memory/manifest.json",
            "runtime": "python",
        }

        with mock.patch.object(
            backend_app,
            "_iter_registered_mcp_server_configs",
            return_value=({"enabled": True, "servers": []}, []),
        ), mock.patch.object(
            backend_app,
            "_load_runtime_tooling_config",
            return_value={"tool_timeout_sec": 45},
        ), mock.patch.object(
            backend_app,
            "_update_third_party_config",
        ) as update_mock, mock.patch.object(
            backend_app,
            "ThirdPartyMCPManager",
            return_value=manager,
        ):
            entry = backend_app._ensure_mcp_server_from_draft(
                "basic_memory",
                json.dumps(
                    {
                        "mcpServers": {
                            "basic_memory": {"command": "uvx", "args": ["basic-memory", "mcp"]}
                        }
                    }
                ),
            )

        manager.prepare_server.assert_called_once_with(Path("third_party_mcp/basic_memory/manifest.json"), timeout_sec=45)
        update_mock.assert_called_once()
        self.assertEqual(entry["name"], "basic_memory")

    def test_ensure_mcp_server_from_draft_rolls_back_on_prepare_failure(self) -> None:
        manifest_path = Path("third_party_mcp/playwright/manifest.json")
        manager = mock.Mock()
        manager.register_server_config.return_value = manifest_path
        manager.prepare_server.side_effect = RuntimeError("download failed")

        with mock.patch.object(
            backend_app,
            "_iter_registered_mcp_server_configs",
            return_value=({"enabled": True, "servers": []}, []),
        ), mock.patch.object(
            backend_app,
            "_load_runtime_tooling_config",
            return_value={"tool_timeout_sec": 45},
        ), mock.patch.object(
            backend_app,
            "_update_third_party_config",
        ) as update_mock, mock.patch.object(
            backend_app,
            "ThirdPartyMCPManager",
            return_value=manager,
        ):
            with self.assertRaisesRegex(RuntimeError, "initial package download or initialization"):
                backend_app._ensure_mcp_server_from_draft(
                    "playwright",
                    json.dumps(
                        {
                            "mcpServers": {
                                "playwright": {"command": "npx", "args": ["@playwright/mcp@latest"]}
                            }
                        }
                    ),
                )

        manager.delete_server.assert_called_once_with(manifest_path)
        update_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
