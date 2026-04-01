from __future__ import annotations

import unittest
from unittest import mock

from backend.asr import ASRError, ASRService, FunASRNanoRuntime, FunASRRuntime, _default_asr_runtime


class _FakeRuntime:
    def __init__(self, *, available: bool = True, readiness_message: str = "") -> None:
        self._available = available
        self._readiness_message = readiness_message

    def available(self) -> bool:
        return self._available

    def availability_message(self) -> str:
        return "ASR unavailable"

    def readiness_message(self) -> str:
        return self._readiness_message

    def warmup(self) -> str:
        return self._readiness_message

    def start_session(self, *, language: str, punctuation: bool):
        return {"language": language, "punctuation": punctuation, "chunks": [], "discarded": False}

    def push_audio(self, runtime_state, pcm16_chunk: bytes) -> str:
        runtime_state["chunks"].append(bytes(pcm16_chunk))
        return f"partial-{len(runtime_state['chunks'])}"

    def stop_session(self, runtime_state) -> str:
        return f"final-{len(runtime_state['chunks'])}"

    def discard_session(self, runtime_state) -> None:
        runtime_state["discarded"] = True


class ASRServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_service_lifecycle_emits_partial_and_final(self) -> None:
        service = ASRService(runtime=_FakeRuntime())

        session = await service.start_session(key="Alt", language="zh", punctuation=True, interim_results=True)
        partial = await service.push_audio(session.session_id, b"\x00\x00\x01\x00")
        final = await service.stop_session(session.session_id)

        self.assertIsNotNone(partial)
        self.assertEqual(partial.text, "partial-1")
        self.assertFalse(partial.is_final)
        self.assertEqual(final.text, "final-1")
        self.assertTrue(final.is_final)

    async def test_service_hides_partial_when_interim_disabled(self) -> None:
        service = ASRService(runtime=_FakeRuntime())

        session = await service.start_session(key="Alt", interim_results=False)
        partial = await service.push_audio(session.session_id, b"\x00\x00")
        final = await service.stop_session(session.session_id)

        self.assertIsNone(partial)
        self.assertEqual(final.text, "final-1")

    async def test_service_raises_when_runtime_unavailable(self) -> None:
        service = ASRService(runtime=_FakeRuntime(available=False))

        with self.assertRaisesRegex(ASRError, "ASR unavailable"):
            await service.start_session(key="Alt")

    async def test_service_raises_when_runtime_not_ready(self) -> None:
        service = ASRService(runtime=_FakeRuntime(available=True, readiness_message="model warmup failed"))

        with self.assertRaisesRegex(ASRError, "model warmup failed"):
            await service.start_session(key="Alt")

    async def test_service_warmup_returns_runtime_message(self) -> None:
        service = ASRService(runtime=_FakeRuntime(available=True, readiness_message="warming"))

        self.assertEqual(await service.warmup(), "warming")

    def test_default_runtime_prefers_local_nano_model(self) -> None:
        with mock.patch.object(FunASRNanoRuntime, "available", return_value=True):
            runtime = _default_asr_runtime()

        self.assertIsInstance(runtime, FunASRNanoRuntime)

    def test_default_runtime_falls_back_when_nano_unavailable(self) -> None:
        with mock.patch.object(FunASRNanoRuntime, "available", return_value=False):
            runtime = _default_asr_runtime()

        self.assertIsInstance(runtime, FunASRRuntime)

    def test_nano_runtime_reports_not_started_before_warmup(self) -> None:
        runtime = FunASRNanoRuntime()

        with mock.patch.object(runtime, "availability_message", return_value=""):
            self.assertEqual(runtime.readiness_message(), "ASR 尚未启动，首次按住 Ctrl 将开始初始化。")

    async def test_service_returns_final_only_when_runtime_has_no_interim_text(self) -> None:
        class _NanoLikeRuntime(_FakeRuntime):
            def push_audio(self, runtime_state, pcm16_chunk: bytes) -> str:
                runtime_state["chunks"].append(bytes(pcm16_chunk))
                return ""

        service = ASRService(runtime=_NanoLikeRuntime())

        session = await service.start_session(key="Ctrl", language="zh", punctuation=True, interim_results=True)
        partial = await service.push_audio(session.session_id, b"\x00\x00\x01\x00")
        final = await service.stop_session(session.session_id)

        self.assertIsNone(partial)
        self.assertEqual(final.text, "final-1")
        self.assertTrue(final.is_final)


if __name__ == "__main__":
    unittest.main()
