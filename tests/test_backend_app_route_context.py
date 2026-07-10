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


ROOT_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT_DIR / "backend" / "app_route_context.py"


class BackendAppRouteContextTests(unittest.TestCase):
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
                    "backend.app_route_context must not import sibling app module",
                )

    def test_backend_app_route_dependency_context_is_thin_forward(self) -> None:
        source = textwrap.dedent(inspect.getsource(backend_app._route_dependency_context))
        tree = ast.parse(source)
        function = tree.body[0]

        self.assertIsInstance(function, ast.FunctionDef)
        self.assertEqual(len(function.body), 1, source)
        self.assertIsInstance(function.body[0], ast.Return, source)
        call = function.body[0].value
        self.assertIsInstance(call, ast.Call, source)
        self.assertIsInstance(call.func, ast.Attribute, source)
        self.assertEqual(call.func.attr, "create_route_dependency_context", source)
        self.assertIsInstance(call.func.value, ast.Name, source)
        self.assertEqual(call.func.value.id, "_app_route_context_helpers", source)
        self.assertEqual(
            {keyword.arg for keyword in call.keywords},
            {"app", "source", "topic_store", "pending_proposals", "audio_cache_dir"},
            source,
        )

    def test_context_reads_backend_app_live_patch_points_after_creation(self) -> None:
        from backend import app_route_context

        context = app_route_context.create_route_dependency_context(
            app=backend_app.app,
            source=backend_app.__dict__,
            topic_store=backend_app.TOPIC_STORE,
            pending_proposals=backend_app.HUMAN_OPS_PENDING_PROPOSALS,
            audio_cache_dir=backend_app.AUDIO_CACHE_DIR,
        )

        with mock.patch.object(backend_app, "_settings_payload", return_value={"payload": True}) as payload_mock:
            settings_deps = app_route_dependencies.create_settings_route_deps(context)
            self.assertEqual(settings_deps.settings_payload({"brain": {}}), {"payload": True})
        payload_mock.assert_called_once_with({"brain": {}})

        with mock.patch.object(
            backend_app,
            "run_brain_turn",
            new=mock.AsyncMock(return_value=mock.sentinel.completion),
        ) as run_mock:
            chat_deps = app_route_dependencies.create_chat_stream_route_deps(context)
            result = asyncio.run(chat_deps.run_brain_turn({"brain": {}}, user_text="hello"))
        self.assertIs(result, mock.sentinel.completion)
        run_mock.assert_awaited_once_with({"brain": {}}, user_text="hello")

        with mock.patch.object(
            backend_app,
            "_perform_human_ops_action",
            new=mock.AsyncMock(return_value={"ok": True}),
        ) as action_mock:
            human_ops_deps = app_route_dependencies.create_human_ops_decision_route_deps(context)
            result = asyncio.run(human_ops_deps.perform_human_ops_action(mock.sentinel.proposal))
        self.assertEqual(result, {"ok": True})
        action_mock.assert_awaited_once_with(mock.sentinel.proposal)


if __name__ == "__main__":
    unittest.main()
