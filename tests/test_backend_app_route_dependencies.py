from __future__ import annotations

import ast
import asyncio
import inspect
import textwrap
import unittest
from pathlib import Path
from unittest import mock

import backend.app as backend_app
from backend import app_route_dependencies
from backend.chat_topics import DEFAULT_TOPIC_TITLE as CHAT_TOPICS_DEFAULT_TOPIC_TITLE


ROOT_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT_DIR / "backend" / "app_route_dependencies.py"


class BackendAppRouteDependenciesTests(unittest.TestCase):
    def test_module_does_not_import_backend_app(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = {alias.name for alias in node.names}
                self.assertNotIn("backend.app", imported)
            elif isinstance(node, ast.ImportFrom):
                self.assertNotEqual(node.module, "backend.app")
                self.assertFalse(
                    node.level == 1 and node.module is None and any(alias.name == "app" for alias in node.names),
                    "backend.app_route_dependencies must not import sibling app module",
                )

    def test_backend_app_keeps_default_topic_title_compat_export(self) -> None:
        self.assertEqual(backend_app.DEFAULT_TOPIC_TITLE, CHAT_TOPICS_DEFAULT_TOPIC_TITLE)

    def test_backend_app_route_dependency_wrappers_are_thin(self) -> None:
        wrapper_names = {
            "_chat_topics_route_deps": "create_chat_topics_route_deps",
            "_settings_route_deps": "create_settings_route_deps",
            "_brain_models_route_deps": "create_brain_models_route_deps",
            "_audio_route_deps": "create_audio_route_deps",
            "_health_route_deps": "create_health_route_deps",
            "_chat_stream_route_deps": "create_chat_stream_route_deps",
            "_human_ops_decision_route_deps": "create_human_ops_decision_route_deps",
        }

        for wrapper_name, helper_name in wrapper_names.items():
            with self.subTest(wrapper=wrapper_name):
                source = textwrap.dedent(inspect.getsource(getattr(backend_app, wrapper_name)))
                tree = ast.parse(source)
                function = tree.body[0]
                self.assertIsInstance(function, ast.FunctionDef)
                self.assertEqual(len(function.body), 1, source)
                self.assertIsInstance(function.body[0], ast.Return, source)
                call = function.body[0].value
                self.assertIsInstance(call, ast.Call, source)
                self.assertIsInstance(call.func, ast.Attribute, source)
                self.assertEqual(call.func.attr, helper_name, source)
                self.assertIsInstance(call.func.value, ast.Name, source)
                self.assertEqual(call.func.value.id, "_app_route_dependency_helpers", source)
                self.assertEqual(len(call.args), 1, source)
                inner_call = call.args[0]
                self.assertIsInstance(inner_call, ast.Call, source)
                self.assertIsInstance(inner_call.func, ast.Name, source)
                self.assertEqual(inner_call.func.id, "_route_dependency_context", source)

    def test_settings_route_deps_still_call_backend_app_patch_points(self) -> None:
        deps = backend_app._settings_route_deps()

        with mock.patch.object(backend_app, "_normalize_private_config", return_value={"current": True}) as normalize_mock:
            self.assertEqual(deps.normalize_private_config(), {"current": True})
        normalize_mock.assert_called_once_with()

        with mock.patch.object(backend_app, "_apply_settings_update", return_value={"updated": True}) as apply_mock:
            self.assertEqual(deps.apply_settings_update({"brain": {}}, current={"existing": True}), {"updated": True})
        apply_mock.assert_called_once_with({"brain": {}}, current={"existing": True})

        with mock.patch.object(backend_app, "_save_config") as save_mock:
            deps.save_config({"brain": {"model_name": "neo"}})
        save_mock.assert_called_once_with({"brain": {"model_name": "neo"}})

        with mock.patch.object(backend_app, "_settings_payload", return_value={"payload": True}) as payload_mock:
            self.assertEqual(deps.settings_payload({"brain": {}}), {"payload": True})
        payload_mock.assert_called_once_with({"brain": {}})

    def test_brain_audio_health_and_chat_topics_deps_use_live_backend_state(self) -> None:
        topic_store = backend_app.TopicStore(Path("/private/tmp") / "route-deps-topics")
        chat_topics_deps = backend_app._chat_topics_route_deps()
        with mock.patch.object(backend_app, "TOPIC_STORE", topic_store):
            self.assertIs(chat_topics_deps.current_topic_store(), topic_store)

        brain_deps = backend_app._brain_models_route_deps()
        with mock.patch.object(
            backend_app,
            "list_provider_models",
            new=mock.AsyncMock(return_value=[mock.sentinel.model]),
        ) as list_mock:
            result = asyncio.run(brain_deps.list_provider_models({"provider": "test"}))
        self.assertEqual(result, [mock.sentinel.model])
        list_mock.assert_awaited_once_with({"provider": "test"})

        audio_deps = backend_app._audio_route_deps()
        with mock.patch.object(
            backend_app,
            "_synthesize_to_audio_for_route",
            new=mock.AsyncMock(return_value=mock.sentinel.audio),
        ) as synthesize_mock:
            result = asyncio.run(audio_deps.synthesize_to_audio(text="hello"))
        self.assertIs(result, mock.sentinel.audio)
        synthesize_mock.assert_awaited_once_with(text="hello")

        with mock.patch.object(backend_app, "tts_available", return_value=True) as tts_mock:
            health_deps = backend_app._health_route_deps()
            self.assertTrue(health_deps.tts_available("provider", "url"))
        tts_mock.assert_called_once_with("provider", "url")

    def test_chat_stream_and_human_ops_deps_expose_key_callables(self) -> None:
        with mock.patch.object(
            backend_app,
            "run_brain_turn",
            new=mock.AsyncMock(return_value=mock.sentinel.completion),
        ) as run_mock:
            chat_stream_deps = backend_app._chat_stream_route_deps()
            result = asyncio.run(chat_stream_deps.run_brain_turn({"brain": {}}, user_text="hello"))
        self.assertIs(result, mock.sentinel.completion)
        run_mock.assert_awaited_once_with({"brain": {}}, user_text="hello")

        with mock.patch.object(
            backend_app,
            "_perform_human_ops_observe",
            new=mock.AsyncMock(return_value={"ok": True}),
        ) as observe_mock:
            chat_stream_deps = backend_app._chat_stream_route_deps()
            result = asyncio.run(chat_stream_deps.perform_human_ops_observe(mock.sentinel.decision, {"observe": True}))
        self.assertEqual(result, {"ok": True})
        observe_mock.assert_awaited_once_with(mock.sentinel.decision, {"observe": True})

        with mock.patch.object(
            backend_app,
            "_perform_human_ops_action",
            new=mock.AsyncMock(return_value={"ok": True}),
        ) as action_mock:
            human_ops_deps = backend_app._human_ops_decision_route_deps()
            result = asyncio.run(human_ops_deps.perform_human_ops_action(mock.sentinel.proposal))
        self.assertEqual(result, {"ok": True})
        action_mock.assert_awaited_once_with(mock.sentinel.proposal)

        self.assertTrue(callable(chat_stream_deps.normalize_private_config))
        self.assertTrue(callable(human_ops_deps.normalize_private_config))
        self.assertTrue(callable(human_ops_deps.proposal_event_payload))


if __name__ == "__main__":
    unittest.main()
