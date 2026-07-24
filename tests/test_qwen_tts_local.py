from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import HTTPException

from backend import tts
from backend.audio_routes import (
    AudioRouteDependencies,
    synthesize_qwen_tts_stream_response,
    synthesize_tts_response,
)
from backend.models import TTSRequest


class _FakeResponse:
    headers = {"content-type": "audio/wav"}
    content = b"RIFF\x00\x00\x00\x00WAVE"

    def raise_for_status(self) -> None:
        return None


class _FakeClient:
    def __init__(self) -> None:
        self.post_calls: list[dict] = []
        self.stream_calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    async def post(self, url, **kwargs):
        self.post_calls.append({"url": url, **kwargs})
        return _FakeResponse()

    def stream(self, method, url, **kwargs):
        self.stream_calls.append({"method": method, "url": url, **kwargs})
        return _FakeStreamResponse()


class _FakeStreamResponse:
    def raise_for_status(self) -> None:
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    async def aiter_bytes(self):
        yield b'{"type":"meta","sample_rate":24000}\n'
        yield b'{"type":"end"}\n'


class QwenTTSLocalTests(unittest.TestCase):
    def test_ipet_uses_the_high_quality_qwen_model_by_default(self) -> None:
        self.assertEqual(tts.QWEN_TTS_MODEL, "qwen3-tts-1.7b-8bit")

    def test_qwen_provider_is_available_when_reference_audio_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reference_audio = Path(tmp) / "ex.mp3"
            reference_audio.write_bytes(b"audio")
            with mock.patch.object(tts, "QWEN_TTS_REFERENCE_AUDIO", reference_audio):
                self.assertIn("qwen_tts_local", tts.list_supported_providers())
                self.assertTrue(tts.tts_available("qwen_tts_local"))

    def test_qwen_clone_reuses_fixed_reference_profile(self) -> None:
        fake_client = _FakeClient()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference_audio = root / "ex.mp3"
            reference_audio.write_bytes(b"audio")
            with mock.patch.object(tts, "QWEN_TTS_REFERENCE_AUDIO", reference_audio), mock.patch.object(
                tts.httpx, "AsyncClient", return_value=fake_client
            ) as async_client:
                result = asyncio.run(
                    tts.synthesize_to_audio(
                        text="你好",
                        cache_dir=root,
                        provider="qwen_tts_local",
                    )
                )

            self.assertEqual(result.media_type, "audio/wav")
            self.assertTrue(result.path.is_file())
            self.assertEqual(fake_client.post_calls[0]["data"]["input"], "你好")
            self.assertEqual(fake_client.post_calls[0]["data"]["reference_id"], "ipet_xiaozhao")
            self.assertNotIn("files", fake_client.post_calls[0])
            self.assertFalse(async_client.call_args.kwargs["trust_env"])

    def test_qwen_stream_posts_fixed_reference_id_and_relays_pcm(self) -> None:
        fake_client = _FakeClient()
        with tempfile.TemporaryDirectory() as tmp:
            reference_audio = Path(tmp) / "ex.mp3"
            reference_audio.write_bytes(b"audio")
            with mock.patch.object(tts, "QWEN_TTS_REFERENCE_AUDIO", reference_audio), mock.patch.object(
                tts.httpx, "AsyncClient", return_value=fake_client
            ):
                async def collect() -> list[bytes]:
                    return [chunk async for chunk in tts.stream_qwen_tts_local(text="你好")]

                packets = asyncio.run(collect())

        self.assertEqual(packets[0], b'{"type":"meta","sample_rate":24000}\n')
        self.assertEqual(fake_client.stream_calls[0]["method"], "POST")
        self.assertEqual(fake_client.stream_calls[0]["json"]["reference_id"], "ipet_xiaozhao")
        self.assertFalse(fake_client.stream_calls[0]["json"].get("reference_audio"))

    def test_stream_route_relays_qwen_packets(self) -> None:
        async def fake_stream(**_kwargs):
            yield b'{"type":"meta"}\n'
            yield b'{"type":"end"}\n'

        deps = AudioRouteDependencies(
            audio_cache_dir=Path("/tmp/ipet-audio"),
            tts_available=lambda _provider, _url: True,
            cleanup_old_audio=lambda _path: None,
            synthesize_to_audio=mock.AsyncMock(),
            stream_qwen_tts_local=fake_stream,
        )
        response = asyncio.run(
            synthesize_qwen_tts_stream_response(
                TTSRequest(text="你好", provider="qwen_tts_local"),
                deps=deps,
            )
        )

        async def collect() -> bytes:
            return b"".join([chunk async for chunk in response.body_iterator])

        self.assertEqual(asyncio.run(collect()), b'{"type":"meta"}\n{"type":"end"}\n')

    def test_qwen_unavailability_uses_the_requested_ui_message(self) -> None:
        deps = AudioRouteDependencies(
            audio_cache_dir=Path("/tmp/ipet-audio"),
            tts_available=lambda _provider, _url: False,
            cleanup_old_audio=lambda _path: None,
            synthesize_to_audio=mock.AsyncMock(),
        )
        with self.assertRaisesRegex(HTTPException, "TTS异常") as raised:
            asyncio.run(
                synthesize_tts_response(
                    TTSRequest(text="你好", provider="qwen_tts_local"),
                    deps=deps,
                )
            )
        self.assertEqual(raised.exception.status_code, 503)
