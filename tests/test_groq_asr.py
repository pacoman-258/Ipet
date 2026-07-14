from __future__ import annotations

import io
import unittest
import wave
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from backend.asr import ASRError, DEFAULT_GROQ_ASR_TIMEOUT_SECONDS, GroqWhisperRuntime, _groq_http_proxy
from backend import settings_config
from backend.settings_defaults import NEO_DEFAULTS


class _FakeResponse:
    status_code = 200
    is_error = False

    def json(self):
        return {"text": "你好，Ipet。"}


class _ForbiddenResponse:
    status_code = 403
    is_error = True

    def json(self):
        return {"error": {"message": "Forbidden"}}


class _FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class GroqASRTests(unittest.TestCase):
    def test_groq_proxy_prefers_http_proxy_over_socks_proxy(self) -> None:
        self.assertEqual(
            _groq_http_proxy(
                {
                    "ALL_PROXY": "socks5://127.0.0.1:10808",
                    "HTTPS_PROXY": "http://127.0.0.1:10808",
                }
            ),
            "http://127.0.0.1:10808",
        )

    def test_runtime_buffers_pcm_and_returns_final_transcript(self) -> None:
        fake_client = _FakeClient(_FakeResponse())
        runtime = GroqWhisperRuntime(api_key="test-key")
        state = runtime.start_session(language="zh", punctuation=True)

        self.assertEqual(runtime.push_audio(state, b"\x00\x00" * 160), "")
        with mock.patch("backend.asr.httpx.Client", return_value=fake_client) as client_mock:
            self.assertEqual(runtime.stop_session(state), "你好，Ipet。")

        self.assertFalse(client_mock.call_args.kwargs["trust_env"])

        self.assertEqual(len(fake_client.calls), 1)
        url, kwargs = fake_client.calls[0]
        self.assertEqual(url, "https://api.groq.com/openai/v1/audio/transcriptions")
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer test-key"})
        self.assertEqual(kwargs["data"]["model"], "whisper-large-v3-turbo")
        self.assertEqual(DEFAULT_GROQ_ASR_TIMEOUT_SECONDS, 20.0)
        filename, audio, media_type = kwargs["files"]["file"]
        self.assertEqual((filename, media_type), ("ipet-input.wav", "audio/wav"))
        with wave.open(io.BytesIO(audio), "rb") as wav_file:
            self.assertEqual(wav_file.getframerate(), 16000)
            self.assertEqual(wav_file.getnchannels(), 1)
            self.assertEqual(wav_file.readframes(160), b"\x00\x00" * 160)

    def test_runtime_requires_api_key(self) -> None:
        runtime = GroqWhisperRuntime(api_key="")

        self.assertFalse(runtime.available())
        self.assertIn("API Key", runtime.availability_message())

    def test_runtime_explains_forbidden_api_access(self) -> None:
        fake_client = _FakeClient(_ForbiddenResponse())
        runtime = GroqWhisperRuntime(api_key="test-key")
        state = runtime.start_session(language="zh", punctuation=True)
        runtime.push_audio(state, b"\x00\x00" * 160)

        with mock.patch("backend.asr.httpx.Client", return_value=fake_client):
            with self.assertRaisesRegex(ASRError, "403 Forbidden"):
                runtime.stop_session(state)

    def test_asr_secret_is_preserved_but_not_returned_to_settings_page(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "pet_config.json"
            raw = {"chat": {"asr": {"api_key": "gsk-secret-value"}}}
            private = settings_config.normalize_private_config(
                raw,
                config_path=config_path,
                defaults=NEO_DEFAULTS,
                allowed_keys=set(NEO_DEFAULTS),
            )
            public = settings_config.public_config(private)

            self.assertNotIn("api_key", public["chat"]["asr"])
            self.assertEqual(public["chat"]["asr"]["api_key_preview"], "gs***ue")

            updated = settings_config.apply_settings_update(
                {"chat": {"asr": {"provider": "groq"}}},
                current=private,
                config_path=config_path,
                defaults=NEO_DEFAULTS,
                allowed_keys=set(NEO_DEFAULTS),
            )
            self.assertEqual(updated["chat"]["asr"]["api_key"], "gsk-secret-value")
            self.assertEqual(updated["chat"]["asr"]["provider"], "groq")


if __name__ == "__main__":
    unittest.main()
