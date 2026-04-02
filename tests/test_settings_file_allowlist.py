from __future__ import annotations

from contextlib import contextmanager
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import backend.app as backend_app


@contextmanager
def _workspace_tempdir():
    path = tempfile.mkdtemp()
    try:
        yield path
    finally:
        pass


class SettingsFileAllowlistTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(backend_app.app)

    def tearDown(self) -> None:
        self.client.close()

    def test_put_settings_config_normalizes_multiple_allowlist_dirs(self) -> None:
        with _workspace_tempdir() as first, _workspace_tempdir() as second:
            with mock.patch.object(backend_app, "_save_full_config") as save_mock, mock.patch.object(
                backend_app,
                "_get_mcp_bridge",
                return_value=mock.Mock(),
            ):
                resp = self.client.put(
                    "/api/settings/config",
                    json={
                        "config": {
                            "chat": {
                                "tooling": {
                                    "file_allowlist": [first, "", second, first],
                                }
                            }
                        }
                    },
                )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(
            payload["config"]["chat"]["tooling"]["file_allowlist"],
            [str(Path(first).resolve()), str(Path(second).resolve())],
        )
        save_mock.assert_called_once()

    def test_put_settings_config_keeps_root_default_when_allowlist_is_empty(self) -> None:
        with mock.patch.object(backend_app, "_save_full_config"), mock.patch.object(
            backend_app,
            "_get_mcp_bridge",
            return_value=mock.Mock(),
        ):
            resp = self.client.put(
                "/api/settings/config",
                json={"config": {"chat": {"tooling": {"file_allowlist": []}}}},
            )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["config"]["chat"]["tooling"]["file_allowlist"], [str(backend_app.ROOT_DIR.resolve())])

    def test_put_settings_config_keeps_router_fields(self) -> None:
        with mock.patch.object(backend_app, "_save_full_config"), mock.patch.object(
            backend_app,
            "_get_mcp_bridge",
            return_value=mock.Mock(),
        ):
            resp = self.client.put(
                "/api/settings/config",
                json={
                    "config": {
                        "chat": {
                            "router_enabled": True,
                            "router_llm_provider": "openai_compat",
                            "router_api_base_url": "https://example.test/v1",
                            "router_api_key": "secret",
                            "router_model": "router-model",
                        }
                    }
                },
            )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()["config"]["chat"]
        self.assertTrue(payload["router_enabled"])
        self.assertEqual(payload["router_llm_provider"], "openai_compat")
        self.assertEqual(payload["router_api_base_url"], "https://example.test/v1")
        self.assertEqual(payload["router_api_key"], "secret")
        self.assertEqual(payload["router_model"], "router-model")

    def test_put_settings_config_keeps_pet_background_fields(self) -> None:
        with mock.patch.object(backend_app, "_save_full_config"), mock.patch.object(
            backend_app,
            "_get_mcp_bridge",
            return_value=mock.Mock(),
        ):
            resp = self.client.put(
                "/api/settings/config",
                json={
                    "config": {
                        "pet": {
                            "background_enabled": True,
                            "background_image": "D:\\images\\pet-bg.png",
                            "background_overlay_opacity": 0.77,
                        }
                    }
                },
            )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()["config"]["pet"]
        self.assertTrue(payload["background_enabled"])
        self.assertEqual(payload["background_image"], "D:\\images\\pet-bg.png")
        self.assertEqual(payload["background_overlay_opacity"], 0.77)

    def test_picker_endpoint_returns_success(self) -> None:
        with mock.patch.object(
            backend_app,
            "_runtime_host_is_online",
            return_value=True,
        ), mock.patch.object(
            backend_app,
            "_write_runtime_command_with_response",
            return_value={"nonce": "nonce-success"},
        ), mock.patch.object(
            backend_app,
            "_wait_for_runtime_command_response",
            return_value={
                "nonce": "nonce-success",
                "status": "success",
                "result": {"directory": "D:\\picked"},
            },
        ):
            resp = self.client.post("/api/settings/file-allowlist/pick", json={"start_dir": "D:\\seed"})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True, "cancelled": False, "path": "D:\\picked", "detail": ""})

    def test_picker_endpoint_returns_cancelled(self) -> None:
        with mock.patch.object(
            backend_app,
            "_runtime_host_is_online",
            return_value=True,
        ), mock.patch.object(
            backend_app,
            "_write_runtime_command_with_response",
            return_value={"nonce": "nonce-cancel"},
        ), mock.patch.object(
            backend_app,
            "_wait_for_runtime_command_response",
            return_value={
                "nonce": "nonce-cancel",
                "status": "cancelled",
                "result": {"directory": ""},
            },
        ):
            resp = self.client.post("/api/settings/file-allowlist/pick", json={})

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["cancelled"])

    def test_picker_endpoint_supports_image_mode(self) -> None:
        with mock.patch.object(
            backend_app,
            "_runtime_host_is_online",
            return_value=True,
        ), mock.patch.object(
            backend_app,
            "_write_runtime_command_with_response",
            return_value={"nonce": "nonce-image"},
        ) as write_mock, mock.patch.object(
            backend_app,
            "_wait_for_runtime_command_response",
            return_value={
                "nonce": "nonce-image",
                "status": "success",
                "result": {"path": "D:\\images\\bg.png"},
            },
        ):
            resp = self.client.post("/api/settings/file-allowlist/pick", json={"kind": "image", "start_path": "D:\\seed.png"})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["path"], "D:\\images\\bg.png")
        args, kwargs = write_mock.call_args
        self.assertEqual(args[0], "pick_image_file")
        self.assertEqual(args[1]["start_path"], "D:\\seed.png")

    def test_picker_endpoint_returns_timeout_detail(self) -> None:
        with mock.patch.object(
            backend_app,
            "_runtime_host_is_online",
            return_value=True,
        ), mock.patch.object(
            backend_app,
            "_write_runtime_command_with_response",
            return_value={"nonce": "nonce-timeout"},
        ), mock.patch.object(
            backend_app,
            "_wait_for_runtime_command_response",
            return_value=None,
        ):
            resp = self.client.post("/api/settings/file-allowlist/pick", json={})

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["ok"])
        self.assertFalse(body["cancelled"])
        self.assertIn("超时", body["detail"])

    def test_picker_endpoint_accepts_start_path_alias(self) -> None:
        with mock.patch.object(
            backend_app,
            "_runtime_host_is_online",
            return_value=True,
        ), mock.patch.object(
            backend_app,
            "_write_runtime_command_with_response",
            return_value={"nonce": "nonce-success"},
        ) as write_mock, mock.patch.object(
            backend_app,
            "_wait_for_runtime_command_response",
            return_value={
                "nonce": "nonce-success",
                "status": "success",
                "result": {"directory": "D:\\picked"},
            },
        ):
            resp = self.client.post("/api/settings/file-allowlist/pick", json={"start_path": "D:\\seed"})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["path"], "D:\\picked")
        args, kwargs = write_mock.call_args
        self.assertEqual(args[0], "pick_directory")
        self.assertEqual(args[1]["start_dir"], "D:\\seed")

    def test_picker_endpoint_returns_host_offline_detail(self) -> None:
        with mock.patch.object(
            backend_app,
            "_runtime_host_is_online",
            return_value=False,
        ):
            resp = self.client.post("/api/settings/file-allowlist/pick", json={})

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["ok"])
        self.assertFalse(body["cancelled"])
        self.assertIn("桌宠宿主未连接", body["detail"])


    def test_settings_static_assets_disable_browser_cache(self) -> None:
        html_resp = self.client.get("/settings")
        js_resp = self.client.get("/settings.js")

        self.assertEqual(html_resp.status_code, 200)
        self.assertEqual(js_resp.status_code, 200)
        for resp in (html_resp, js_resp):
            self.assertIn("no-store", resp.headers.get("cache-control", "").lower())
            self.assertIn("no-cache", resp.headers.get("pragma", "").lower())
            self.assertEqual(resp.headers.get("expires"), "0")

    def test_settings_js_binds_file_allowlist_pick_handler(self) -> None:
        resp = self.client.get("/settings.js")

        self.assertEqual(resp.status_code, 200)
        body = resp.text
        self.assertIn('const fileAllowlistPickerEndpoint = "/api/settings/file-allowlist/pick";', body)
        self.assertIn('els.fileAllowlistPickBtn.addEventListener("click", () => wrapAction(pickAllowlistDirectory));', body)
        self.assertIn('els.fileAllowlistEffectiveBtn.addEventListener("click", () => wrapAction(refreshEffectiveFileAllowlist));', body)
        self.assertIn("async function pickAllowlistDirectory()", body)

    def test_settings_js_preserves_posix_paths_for_macos(self) -> None:
        resp = self.client.get("/settings.js")

        self.assertEqual(resp.status_code, 200)
        body = resp.text
        self.assertIn("function prefersWindowsPaths()", body)
        self.assertIn("function looksLikeWindowsPath(value)", body)
        self.assertIn('normalized = normalized.replace(/\\\\/g, "/");', body)

    def test_settings_js_binds_router_model_controls(self) -> None:
        resp = self.client.get("/settings.js")

        self.assertEqual(resp.status_code, 200)
        body = resp.text
        self.assertIn('chatRouterEnabled: $("chat-router-enabled")', body)
        self.assertIn('chatRouterModel: $("chat-router-model")', body)
        self.assertIn('async function fetchRouterModels()', body)
        self.assertIn('els.fetchRouterModelsBtn.addEventListener("click", () => wrapAction(fetchRouterModels));', body)

    def test_settings_assets_include_pet_background_controls(self) -> None:
        html_resp = self.client.get("/settings")
        js_resp = self.client.get("/settings.js")

        self.assertEqual(html_resp.status_code, 200)
        self.assertEqual(js_resp.status_code, 200)
        self.assertIn('id="pet-background-image"', html_resp.text)
        self.assertIn('id="pet-background-pick-btn"', html_resp.text)
        self.assertIn('petBackgroundPickBtn', js_resp.text)
        self.assertIn('petBackgroundPickerEndpoint', js_resp.text)


if __name__ == "__main__":
    unittest.main()
