from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend import tts
import backend.app as backend_app


class _FakeResponse:
    headers = {"content-type": "audio/mpeg"}
    content = b"ID3fish-audio"

    def raise_for_status(self) -> None:
        return None


class _FakeClient:
    def __init__(self) -> None:
        self.post_calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    async def post(self, url, **kwargs):
        self.post_calls.append({"url": url, **kwargs})
        return _FakeResponse()


class FishAudioTTSTests(unittest.TestCase):
    def test_provider_requires_api_key_and_voice_model_id(self) -> None:
        with mock.patch.dict(os.environ, {tts.FISH_AUDIO_API_KEY_ENV: "env-key"}, clear=False):
            self.assertTrue(tts.tts_available("fish_audio", reference_id="voice-model-id"))
            self.assertFalse(tts.tts_available("fish_audio", reference_id=""))

        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(tts.tts_available("fish_audio", reference_id="voice-model-id"))

    def test_synthesis_posts_reference_id_and_maps_rate_to_fish_prosody(self) -> None:
        fake_client = _FakeClient()
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(tts.httpx, "AsyncClient", return_value=fake_client):
                result = asyncio.run(
                    tts.synthesize_to_audio(
                        text="你好",
                        cache_dir=Path(tmp),
                        provider="fish_audio",
                        api_key="secret-key",
                        reference_id="voice-model-id",
                        model="s2-pro",
                        rate="-10%",
                    )
                )

            request = fake_client.post_calls[0]
            self.assertEqual(request["url"], tts.FISH_AUDIO_API_URL)
            self.assertEqual(request["headers"]["Authorization"], "Bearer secret-key")
            self.assertEqual(request["headers"]["model"], "s2-pro")
            self.assertEqual(request["json"]["text"], "你好")
            self.assertEqual(request["json"]["reference_id"], "voice-model-id")
            self.assertEqual(request["json"]["format"], "mp3")
            self.assertEqual(request["json"]["prosody"]["speed"], 0.9)
            self.assertEqual(result.media_type, "audio/mpeg")
            self.assertTrue(result.path.is_file())

    def test_route_injects_private_fish_audio_settings(self) -> None:
        synthesize = mock.AsyncMock(return_value=mock.sentinel.audio)
        with (
            mock.patch.object(
                backend_app,
                "_normalize_private_config",
                return_value={
                    "chat": {
                        "tts_api_key": "private-key",
                        "tts_voice_id": "voice-model-id",
                        "tts_model": "s2-pro",
                    }
                },
            ),
            mock.patch.object(backend_app, "synthesize_to_audio", new=synthesize),
        ):
            result = asyncio.run(
                backend_app._synthesize_to_audio_for_route(
                    text="你好",
                    cache_dir=Path("/tmp/ipet-fish-audio"),
                    provider="fish_audio",
                )
            )

        self.assertIs(result, mock.sentinel.audio)
        synthesize.assert_awaited_once_with(
            text="你好",
            cache_dir=Path("/tmp/ipet-fish-audio"),
            provider="fish_audio",
            api_key="private-key",
            reference_id="voice-model-id",
            model="s2-pro",
        )


if __name__ == "__main__":
    unittest.main()
