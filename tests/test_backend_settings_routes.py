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
        )
        app = FastAPI()
        settings_routes.register_settings_routes(app, deps)

        with TestClient(app) as client:
            get_resp = client.get("/api/settings/config")
            put_resp = client.put(
                "/api/settings/config",
                json={"config": {"brain": {"model_name": "updated"}}},
            )
            bad_resp = client.put("/api/settings/config", json={"config": []})

        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(get_resp.json(), {"config": {"source": "get"}})
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
