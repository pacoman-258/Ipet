from __future__ import annotations

import inspect
import re
import unittest
from unittest import mock

from fastapi import Response
from fastapi.testclient import TestClient

import backend.app as backend_app
from human_ops.approvals import ReviewableProposal
from backend import (
    asr_disabled_routes,
    audio_routes,
    brain_models_route,
    chat_stream_routes,
    health_routes,
    human_ops_decision_routes,
)


APP_ROUTE_DECORATORS = (
    (r"@app\.(?:get|post|websocket|delete)\(", "any app-level route decorator"),
    (r"@app\.post\(\s*[\"']/api/brain/models[\"']", "POST /api/brain/models"),
    (r"@app\.post\(\s*[\"']/api/asr/warmup[\"']", "POST /api/asr/warmup"),
    (r"@app\.websocket\(\s*[\"']/api/asr/stream[\"']", "WEBSOCKET /api/asr/stream"),
    (r"@app\.post\(\s*[\"']/api/tts[\"']", "POST /api/tts"),
    (r"@app\.get\(\s*[\"']/api/audio/\{file_name\}[\"']", "GET /api/audio/{file_name}"),
)


SPLIT_ROUTE_INTERFACES = (
    (brain_models_route, "create_brain_models_router", "register_brain_models_routes"),
    (asr_disabled_routes, "create_asr_disabled_router", "register_asr_disabled_routes"),
    (audio_routes, "create_audio_router", "register_audio_routes"),
    (health_routes, "create_health_router", "register_health_routes"),
    (chat_stream_routes, "create_chat_stream_router", "register_chat_stream_routes"),
    (human_ops_decision_routes, "create_human_ops_decision_router", "register_human_ops_decision_routes"),
)


SPLIT_ROUTE_DECORATORS = (
    (brain_models_route, '@router.post("/api/brain/models")'),
    (asr_disabled_routes, '@router.post("/api/asr/warmup")'),
    (asr_disabled_routes, '@router.websocket("/api/asr/stream")'),
    (audio_routes, '@router.post("/api/tts")'),
    (audio_routes, '@router.get("/api/audio/{file_name}")'),
    (health_routes, '@router.get("/api/health")'),
    (chat_stream_routes, '@router.post("/api/chat/stream")'),
    (human_ops_decision_routes, '@router.post("/api/human-ops/proposals/{proposal_id}/decision")'),
)


async def _delegated_event_stream():
    yield "event: delegated\ndata: {}\n\n"


async def _delegated_brain_turn(*_args, **_kwargs):
    return None


async def _delegated_human_ops_action(_proposal):
    return {"ok": True}


def _delegated_tts_available(_provider, _provider_url) -> bool:
    return True


class BackendRouteSplitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(backend_app.app)

    def tearDown(self) -> None:
        self.client.close()

    def test_remaining_split_routes_are_not_declared_directly_on_app(self) -> None:
        source = inspect.getsource(backend_app)

        for pattern, label in APP_ROUTE_DECORATORS:
            with self.subTest(route=label):
                self.assertIsNone(re.search(pattern, source), f"{label} should be registered by its route module")

    def test_split_route_modules_expose_register_interfaces(self) -> None:
        app_source = inspect.getsource(backend_app)

        for module, create_name, register_name in SPLIT_ROUTE_INTERFACES:
            with self.subTest(module=module.__name__):
                self.assertTrue(callable(getattr(module, create_name, None)))
                self.assertTrue(callable(getattr(module, register_name, None)))
                self.assertRegex(app_source, rf"\.{register_name}\(app(?:,|\))")

    def test_split_route_modules_own_remaining_route_decorators(self) -> None:
        for module, decorator in SPLIT_ROUTE_DECORATORS:
            with self.subTest(module=module.__name__, decorator=decorator):
                self.assertIn(decorator, inspect.getsource(module))

    def test_brain_models_route_delegates_to_split_module(self) -> None:
        with mock.patch.object(
            brain_models_route,
            "get_brain_models",
            new=mock.AsyncMock(return_value={"ok": True, "delegated": True}),
        ) as delegated:
            resp = self.client.post("/api/brain/models", json={"provider": "google_aistudio"})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True, "delegated": True})
        args, kwargs = delegated.await_args
        self.assertEqual(args[0], {"provider": "google_aistudio"})
        self.assertTrue(callable(kwargs["deps"].list_provider_models))

    def test_tts_route_delegates_to_split_module(self) -> None:
        with mock.patch.object(
            audio_routes,
            "synthesize_tts_response",
            new=mock.AsyncMock(return_value={"ok": True, "audio_url": "/api/audio/delegated.mp3"}),
        ) as delegated:
            resp = self.client.post("/api/tts", json={"text": "hello"})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True, "audio_url": "/api/audio/delegated.mp3"})
        args, kwargs = delegated.await_args
        self.assertEqual(args[0].text, "hello")
        self.assertTrue(callable(kwargs["deps"].synthesize_to_audio))
        self.assertEqual(kwargs["deps"].audio_cache_dir, backend_app.AUDIO_CACHE_DIR)

    def test_audio_route_delegates_to_split_module(self) -> None:
        with mock.patch.object(
            audio_routes,
            "audio_response",
            return_value=Response(content=b"delegated", media_type="text/plain"),
        ) as delegated:
            resp = self.client.get("/api/audio/delegated.mp3")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.text, "delegated")
        delegated.assert_called_once_with("delegated.mp3", audio_cache_dir=backend_app.AUDIO_CACHE_DIR)

    def test_health_route_delegates_to_split_module(self) -> None:
        with mock.patch.object(
            health_routes,
            "health_payload",
            return_value={"status": "ok", "delegated": True},
        ) as delegated:
            resp = self.client.get("/api/health")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok", "delegated": True})
        delegated.assert_called_once()
        self.assertTrue(callable(delegated.call_args.kwargs["deps"].normalize_private_config))

    def test_chat_stream_route_delegates_to_flow(self) -> None:
        with mock.patch.object(
            chat_stream_routes,
            "stream_chat_response",
            return_value=_delegated_event_stream(),
        ) as delegated:
            with self.client.stream("POST", "/api/chat/stream", json={"text": "hello"}) as resp:
                body = resp.read().decode("utf-8")

        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/event-stream", resp.headers["content-type"])
        self.assertIn("event: delegated", body)
        delegated.assert_called_once()
        args, _kwargs = delegated.call_args
        self.assertEqual(args[0], {"text": "hello"})
        self.assertTrue(callable(args[1].run_brain_turn))

    def test_human_ops_decision_route_delegates_to_flow(self) -> None:
        with mock.patch.object(
            human_ops_decision_routes,
            "stream_human_ops_proposal_decision",
            return_value=_delegated_event_stream(),
        ) as delegated:
            with self.client.stream(
                "POST",
                "/api/human-ops/proposals/proposal-1/decision",
                json={"approved": False},
            ) as resp:
                body = resp.read().decode("utf-8")

        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/event-stream", resp.headers["content-type"])
        self.assertIn("event: delegated", body)
        delegated.assert_called_once()
        args, _kwargs = delegated.call_args
        self.assertEqual(args[0], "proposal-1")
        self.assertEqual(args[1], {"approved": False})
        self.assertTrue(callable(args[2].perform_human_ops_action))

    def test_human_ops_decision_route_converts_missing_proposals_to_404(self) -> None:
        with mock.patch.object(
            human_ops_decision_routes,
            "stream_human_ops_proposal_decision",
            side_effect=human_ops_decision_routes.HumanOpsProposalNotFound("missing proposal"),
        ):
            resp = self.client.post(
                "/api/human-ops/proposals/missing/decision",
                json={"approved": True},
            )

        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json(), {"detail": "missing proposal"})

    def test_native_human_ops_decision_is_retired_without_prompting(self) -> None:
        proposal_id = "native-route-proposal"
        proposal = ReviewableProposal.act(
            action_type="click",
            summary="Ipet 想点击：登录",
            payload={"target_app": "Google Chrome", "x": 10, "y": 20, "label": "登录"},
        )
        backend_app.HUMAN_OPS_PENDING_PROPOSALS[proposal_id] = {
            "proposal": proposal,
            "session_id": "native-route",
            "user_text": "登录 YouTube",
            "status": "pending",
        }
        try:
            with mock.patch.object(
                backend_app,
                "_request_native_human_ops_approval",
                new=mock.AsyncMock(return_value={"approved": False, "method": "macos_dialog"}),
            ) as native_prompt:
                resp = self.client.post(
                    f"/api/human-ops/proposals/{proposal_id}/native-decision",
                    json={},
                )
        finally:
            backend_app.HUMAN_OPS_PENDING_PROPOSALS.pop(proposal_id, None)

        self.assertEqual(resp.status_code, 410)
        self.assertIn("Native approval is retired", resp.json()["detail"])
        native_prompt.assert_not_awaited()

    def test_final_split_routes_resolve_app_dependencies_per_request(self) -> None:
        with (
            mock.patch.object(backend_app, "tts_available", new=_delegated_tts_available),
            mock.patch.object(backend_app, "run_brain_turn", new=_delegated_brain_turn),
            mock.patch.object(backend_app, "_perform_human_ops_action", new=_delegated_human_ops_action),
            mock.patch.object(
                health_routes,
                "health_payload",
                return_value={"status": "ok", "delegated": True},
            ) as health_delegated,
            mock.patch.object(
                chat_stream_routes,
                "stream_chat_response",
                return_value=_delegated_event_stream(),
            ) as chat_delegated,
            mock.patch.object(
                human_ops_decision_routes,
                "stream_human_ops_proposal_decision",
                return_value=_delegated_event_stream(),
            ) as decision_delegated,
        ):
            self.client.get("/api/health")
            with self.client.stream("POST", "/api/chat/stream", json={"text": "hello"}) as resp:
                resp.read()
            with self.client.stream(
                "POST",
                "/api/human-ops/proposals/proposal-1/decision",
                json={"approved": False},
            ) as resp:
                resp.read()

        self.assertIs(health_delegated.call_args.kwargs["deps"].tts_available, _delegated_tts_available)
        self.assertIs(chat_delegated.call_args.args[1].run_brain_turn, _delegated_brain_turn)
        self.assertIs(decision_delegated.call_args.args[2].perform_human_ops_action, _delegated_human_ops_action)


if __name__ == "__main__":
    unittest.main()
