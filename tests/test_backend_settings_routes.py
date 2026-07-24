from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

import backend.app as backend_app


class BackendSettingsRoutesTests(unittest.TestCase):
    def test_settings_asset_route_delegates_to_registered_dependencies(self) -> None:
        from backend import settings_routes

        page_response = mock.Mock(return_value=Response(content="delegated settings", media_type="text/plain"))
        deps = settings_routes.SettingsRouteDependencies(
            settings_page_response=page_response,
            settings_css_response=mock.Mock(return_value=Response(content="css")),
            settings_js_response=mock.Mock(return_value=Response(content="js")),
            settings_form_js_response=mock.Mock(return_value=Response(content="form")),
            settings_model_picker_js_response=mock.Mock(return_value=Response(content="picker")),
            settings_payload=mock.Mock(return_value={"config": {}}),
            normalize_private_config=mock.Mock(return_value={}),
            apply_settings_update=mock.Mock(return_value={}),
            save_config=mock.Mock(),
        )
        app = FastAPI()
        settings_routes.register_settings_routes(app, deps)

        with TestClient(app) as client:
            resp = client.get("/settings")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.text, "delegated settings")
        page_response.assert_called_once_with()

    def test_settings_config_routes_delegate_to_registered_dependencies(self) -> None:
        from backend import settings_routes

        current_config = {"brain": {"model_name": "current"}}
        updated_config = {"brain": {"model_name": "updated"}}
        settings_payload = mock.Mock(
            side_effect=[
                {"config": {"source": "get"}},
                {"config": {"source": "put"}},
            ]
        )
        normalize_private_config = mock.Mock(return_value=current_config)
        apply_settings_update = mock.Mock(return_value=updated_config)
        save_config = mock.Mock()
        list_persona_prompts = mock.Mock(return_value=[{"name": "friendly.md", "content": "友好"}])
        list_chrome_profiles = mock.Mock(
            return_value=[{"directory": "Profile 1", "display_name": "工作", "active": True}]
        )
        deps = settings_routes.SettingsRouteDependencies(
            settings_page_response=mock.Mock(return_value=Response(content="settings")),
            settings_css_response=mock.Mock(return_value=Response(content="css")),
            settings_js_response=mock.Mock(return_value=Response(content="js")),
            settings_form_js_response=mock.Mock(return_value=Response(content="form")),
            settings_model_picker_js_response=mock.Mock(return_value=Response(content="picker")),
            settings_payload=settings_payload,
            normalize_private_config=normalize_private_config,
            apply_settings_update=apply_settings_update,
            save_config=save_config,
            list_persona_prompts=list_persona_prompts,
            list_chrome_profiles=list_chrome_profiles,
        )
        app = FastAPI()
        settings_routes.register_settings_routes(app, deps)

        with TestClient(app) as client:
            get_resp = client.get("/api/settings/config")
            prompts_resp = client.get("/api/settings/persona-prompts")
            profiles_resp = client.get("/api/settings/chrome-profiles")
            put_resp = client.put(
                "/api/settings/config",
                json={"config": {"brain": {"model_name": "updated"}}},
            )
            bad_resp = client.put("/api/settings/config", json={"config": []})

        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(get_resp.json(), {"config": {"source": "get"}})
        self.assertEqual(prompts_resp.json(), {"prompts": [{"name": "friendly.md", "content": "友好"}]})
        self.assertEqual(
            profiles_resp.json(),
            {
                "profiles": [{"directory": "Profile 1", "display_name": "工作", "active": True}],
                "error": "",
            },
        )
        self.assertEqual(put_resp.status_code, 200)
        self.assertEqual(put_resp.json(), {"config": {"source": "put"}})
        self.assertEqual(bad_resp.status_code, 400)
        self.assertEqual(bad_resp.json()["detail"], "config must be an object.")

        settings_payload.assert_has_calls([mock.call(), mock.call(updated_config)])
        normalize_private_config.assert_called_once_with()
        apply_settings_update.assert_called_once_with(
            {"brain": {"model_name": "updated"}},
            current=current_config,
        )
        save_config.assert_called_once_with(updated_config)
        list_persona_prompts.assert_called_once_with()
        list_chrome_profiles.assert_called_once_with()

    def test_settings_live2d_routes_list_models_and_wait_for_preview_result(self) -> None:
        from backend import settings_routes

        list_local_models = mock.Mock(return_value=[{"path": "model/pet/pet.model3.json"}])
        preview_action = mock.AsyncMock(return_value={"model_path": "model/pet/pet.model3.json"})
        deps = settings_routes.SettingsRouteDependencies(
            settings_page_response=mock.Mock(return_value=Response(content="settings")),
            settings_css_response=mock.Mock(return_value=Response(content="css")),
            settings_js_response=mock.Mock(return_value=Response(content="js")),
            settings_form_js_response=mock.Mock(return_value=Response(content="form")),
            settings_model_picker_js_response=mock.Mock(return_value=Response(content="picker")),
            settings_payload=mock.Mock(return_value={"config": {}}),
            normalize_private_config=mock.Mock(return_value={}),
            apply_settings_update=mock.Mock(return_value={}),
            save_config=mock.Mock(),
            list_local_models=list_local_models,
            preview_action=preview_action,
        )
        app = FastAPI()
        settings_routes.register_settings_routes(app, deps)

        with TestClient(app) as client:
            models_resp = client.get("/api/settings/models-local")
            preview_resp = client.post(
                "/api/settings/preview-action",
                json={"type": "load_model", "model_path": "model/pet/pet.model3.json"},
            )

        self.assertEqual(models_resp.status_code, 200)
        self.assertEqual(models_resp.json(), {"models": [{"path": "model/pet/pet.model3.json"}]})
        self.assertEqual(preview_resp.status_code, 200)
        self.assertEqual(
            preview_resp.json(),
            {"ok": True, "result": {"model_path": "model/pet/pet.model3.json"}},
        )
        list_local_models.assert_called_once_with()
        preview_action.assert_awaited_once_with(
            {"type": "load_model", "model_path": "model/pet/pet.model3.json"}
        )

    def test_accessibility_index_routes_expose_status_and_manual_refresh(self) -> None:
        from backend import settings_routes

        status = mock.AsyncMock(return_value={"app_count": 1, "element_count": 420})
        refresh = mock.AsyncMock(return_value={"updated_count": 2, "app_count": 2})
        deps = settings_routes.SettingsRouteDependencies(
            settings_page_response=mock.Mock(return_value=Response(content="settings")),
            settings_css_response=mock.Mock(return_value=Response(content="css")),
            settings_js_response=mock.Mock(return_value=Response(content="js")),
            settings_form_js_response=mock.Mock(return_value=Response(content="form")),
            settings_model_picker_js_response=mock.Mock(return_value=Response(content="picker")),
            settings_payload=mock.Mock(return_value={"config": {}}),
            normalize_private_config=mock.Mock(return_value={}),
            apply_settings_update=mock.Mock(return_value={}),
            save_config=mock.Mock(),
            accessibility_index_status=status,
            refresh_accessibility_index=refresh,
        )
        app = FastAPI()
        settings_routes.register_settings_routes(app, deps)

        with TestClient(app) as client:
            status_response = client.get("/api/settings/accessibility-index")
            refresh_response = client.post("/api/settings/accessibility-index/refresh")

        self.assertEqual(
            status_response.json(),
            {"ok": True, "app_count": 1, "element_count": 420},
        )
        self.assertEqual(
            refresh_response.json(),
            {"ok": True, "updated_count": 2, "app_count": 2},
        )
        status.assert_awaited_once_with()
        refresh.assert_awaited_once_with()

    def test_app_source_registers_settings_router_instead_of_inline_decorators(self) -> None:
        source = Path(backend_app.__file__).read_text(encoding="utf-8")

        self.assertIn("settings_routes", source)
        self.assertIn("register_settings_routes(app", source)
        self.assertNotIn('@app.get("/settings")', source)
        self.assertNotIn('@app.get("/settings.css")', source)
        self.assertNotIn('@app.get("/settings.js")', source)
        self.assertNotIn('@app.get("/settings_form.js")', source)
        self.assertNotIn('@app.get("/settings_model_picker.js")', source)
        self.assertNotIn('@app.get("/api/settings/config")', source)
        self.assertNotIn('@app.put("/api/settings/config")', source)


if __name__ == "__main__":
    unittest.main()
