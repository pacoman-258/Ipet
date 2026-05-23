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

    def test_put_settings_config_drops_legacy_router_fields(self) -> None:
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
        self.assertNotIn("router_enabled", payload)
        self.assertNotIn("router_llm_provider", payload)
        self.assertNotIn("router_api_base_url", payload)
        self.assertNotIn("router_api_key", payload)
        self.assertNotIn("router_model", payload)

    def test_put_settings_config_keeps_hermes_model_and_tts_asr_contract(self) -> None:
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
                            "llm_provider": "openai_compat",
                            "api_base_url": "https://example.test/v1",
                            "api_key": "main-secret",
                            "model": "main-model",
                            "tts_provider": "edge_tts",
                            "tts_provider_url": "http://tts.local",
                            "voice": "zh-CN-XiaoxiaoNeural",
                            "asr": {
                                "enabled": True,
                                "provider": "funasr",
                                "api_base_url": "http://127.0.0.1:8012",
                                "push_to_talk_key": "Ctrl",
                                "interim_results": False,
                            },
                        }
                    }
                },
            )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()["config"]["chat"]
        self.assertNotIn("llm_provider", payload)
        self.assertNotIn("api_base_url", payload)
        self.assertNotIn("api_key", payload)
        self.assertEqual(payload["model"], "main-model")
        self.assertEqual(payload["tts_provider"], "edge_tts")
        self.assertEqual(payload["tts_provider_url"], "http://tts.local")
        self.assertEqual(payload["voice"], "zh-CN-XiaoxiaoNeural")
        self.assertTrue(payload["asr"]["enabled"])
        self.assertEqual(payload["asr"]["provider"], "funasr")
        self.assertEqual(payload["asr"]["api_base_url"], "http://127.0.0.1:8012")
        self.assertEqual(payload["asr"]["push_to_talk_key"], "Ctrl")
        self.assertFalse(payload["asr"]["interim_results"])

    def test_models_endpoint_is_hermes_compatibility_noop(self) -> None:
        with (
            mock.patch("backend.ollama_client.list_models", new_callable=mock.AsyncMock) as list_models_mock,
            mock.patch.object(
                backend_app,
                "_runtime_config_from_raw",
                return_value={"active": "hermes"},
            ),
        ):
            resp = self.client.post(
                "/api/models",
                json={
                    "llm_provider": "openai_compat",
                    "api_base_url": "https://example.test/v1",
                    "api_key": "secret",
                },
            )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["models"], [])
        self.assertEqual(payload["runtime"], "hermes")
        self.assertTrue(payload["deprecated"])
        list_models_mock.assert_not_called()

    def test_settings_models_local_remains_available_for_hermes_shortcuts(self) -> None:
        local_models = [{"path": "model/demo/runtime/demo.model3.json", "label": "demo"}]
        with mock.patch.object(backend_app, "_list_local_models", return_value=local_models):
            resp = self.client.get("/api/settings/models-local")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["models"], local_models)

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

    def test_settings_js_binds_single_hermes_model_controls(self) -> None:
        resp = self.client.get("/settings.js")

        self.assertEqual(resp.status_code, 200)
        body = resp.text
        self.assertIn('chatModel: $("chat-model")', body)
        self.assertIn('runtimeMainModelPresets: $("runtime-main-model-presets")', body)
        self.assertIn('runtimeSessionPresets: $("runtime-session-presets")', body)
        self.assertIn("function renderHermesRuntimeModelPresets()", body)
        self.assertIn("function renderHermesSessionPresets()", body)
        self.assertIn('els.chatModel.addEventListener("input", renderHermesRuntimeModelPresets);', body)
        self.assertIn('els.chatSessionId.addEventListener("input", renderHermesSessionPresets);', body)
        self.assertNotIn("fetchRouterModels", body)
        self.assertNotIn("fetchRouterModelsBtn", body)
        self.assertNotIn("chatRouterModel", body)
        self.assertNotIn("chatRouterEnabled", body)

    def test_settings_assets_include_pet_background_controls(self) -> None:
        html_resp = self.client.get("/settings")
        js_resp = self.client.get("/settings.js")

        self.assertEqual(html_resp.status_code, 200)
        self.assertEqual(js_resp.status_code, 200)
        self.assertIn('id="pet-background-image"', html_resp.text)
        self.assertIn('id="pet-background-pick-btn"', html_resp.text)
        self.assertIn('petBackgroundPickBtn', js_resp.text)
        self.assertIn('petBackgroundPickerEndpoint', js_resp.text)

    def test_settings_assets_include_astrbot_dashboard_password_controls(self) -> None:
        html_resp = self.client.get("/settings")
        js_resp = self.client.get("/settings.js")

        self.assertEqual(html_resp.status_code, 200)
        self.assertEqual(js_resp.status_code, 200)
        self.assertIn('id="astrbot-dashboard-username"', html_resp.text)
        self.assertIn('id="astrbot-dashboard-password"', html_resp.text)
        self.assertIn('id="astrbot-dashboard-password-clear"', html_resp.text)
        self.assertIn('id="astrbot-dashboard-password-state"', html_resp.text)
        self.assertIn('astrbotDashboardUsername: $("astrbot-dashboard-username")', js_resp.text)
        self.assertIn('dashboard_password_action: astrobotDashboardPasswordAction', js_resp.text)


if __name__ == "__main__":
    unittest.main()
