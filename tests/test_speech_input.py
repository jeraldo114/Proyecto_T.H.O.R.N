"""Reconocimiento simulado: privacidad, limites, workers y cancelacion sin hardware."""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import zipfile

from core.speech_input import LocalSpeechInput, MAX_PCM_BYTES
from tests.test_assistant import MemoryAudit
from tools.install_speech_model import NAME, validate_archive


class SpeechTests(unittest.IsolatedAsyncioTestCase):
    async def make_engine(self, *, loader: object = lambda path: object(),
                          decoder: object = lambda model, pcm: "prueba local") -> LocalSpeechInput:
        engine = LocalSpeechInput(MemoryAudit(), enabled=True, loader=loader, decoder=decoder)
        await engine.start()
        self.addAsyncCleanup(engine.stop)
        return engine

    async def test_disabled_and_invalid_configuration_do_not_load(self) -> None:
        loader = Mock(side_effect=AssertionError("no model I/O"))
        for flag, state in (("0", "disabled"), ("wrong", "invalid_configuration")):
            with patch.dict("os.environ", {"THORN_MICROPHONE_ENABLED": flag}):
                engine = LocalSpeechInput(MemoryAudit(), loader=loader)
            await engine.start()
            self.assertEqual(engine.status["state"], state)
            self.assertFalse(engine.status["available"])
            await engine.stop()
        loader.assert_not_called()

    async def test_model_failures_degrade_without_private_error_messages(self) -> None:
        for error, state in ((FileNotFoundError("private-path"), "model_missing"),
                             (ImportError("private-path"), "engine_missing"),
                             (RuntimeError("private-path"), "model_invalid")):
            engine = await self.make_engine(loader=Mock(side_effect=error))
            self.assertEqual(engine.status["state"], state)
            self.assertNotIn("private-path", json.dumps(engine.status))

    async def test_native_calls_leave_event_loop_and_audit_only_metadata(self) -> None:
        native_threads: list[int] = []
        def load(path: Path) -> object:
            native_threads.append(threading.get_ident())
            return object()
        def decode(model: object, pcm: bytes) -> str:
            native_threads.append(threading.get_ident())
            return "texto privado de prueba"
        engine = await self.make_engine(loader=load, decoder=decode)
        result = await engine.transcribe(b"\0" * 6400, local_consent=True, request_id="test")
        self.assertEqual(result["text"], "texto privado de prueba")
        self.assertTrue(result["has_speech"])
        self.assertFalse(result["cloud"])
        self.assertTrue(all(identity != threading.get_ident() for identity in native_threads))
        self.assertNotIn("texto privado", json.dumps(engine.audit.rows))
        self.assertEqual([row["status"] for row in engine.audit.rows], ["requested", "transcribed"])

    async def test_audio_and_consent_fail_before_decoding(self) -> None:
        decode = Mock(side_effect=AssertionError("no decode"))
        engine = await self.make_engine(decoder=decode)
        for consent in (False, "true", 1, None):
            with self.assertRaisesRegex(ValueError, "local_consent_required"):
                await engine.transcribe(b"\0" * 6400, local_consent=consent, request_id="test")
        for pcm in (b"", b"\0" * 3201, b"\0" * (MAX_PCM_BYTES + 2), "not-bytes"):
            with self.assertRaisesRegex(ValueError, "invalid_audio"):
                await engine.transcribe(pcm, local_consent=True, request_id="test")
        decode.assert_not_called()
        self.assertFalse(engine.audit.rows)

    async def test_silence_is_not_invented_text(self) -> None:
        engine = await self.make_engine(decoder=lambda model, pcm: "")
        result = await engine.transcribe(b"\0" * 6400, local_consent=True, request_id="test")
        self.assertEqual(result["text"], "")
        self.assertFalse(result["has_speech"])

    async def test_bad_native_results_are_redacted(self) -> None:
        for decode in (lambda model, pcm: "x" * 2001, Mock(side_effect=RuntimeError("private detail"))):
            engine = await self.make_engine(decoder=decode)
            with self.assertRaisesRegex(RuntimeError, "^microphone_transcription_failed$"):
                await engine.transcribe(b"\0" * 6400, local_consent=True, request_id="test")
            self.assertFalse(engine.status["busy"])

    async def test_cancelled_request_waits_for_native_worker_before_releasing_lock(self) -> None:
        started, release = threading.Event(), threading.Event()
        def decode(model: object, pcm: bytes) -> str:
            started.set()
            release.wait(5)
            return "local"
        engine = await self.make_engine(decoder=decode)
        task = asyncio.create_task(engine.transcribe(b"\0" * 6400, local_consent=True, request_id="first"))
        while not started.is_set():
            await asyncio.sleep(0.001)
        try:
            task.cancel()
            await asyncio.sleep(0.01)
            self.assertFalse(task.done())
            with self.assertRaisesRegex(RuntimeError, "microphone_busy"):
                await engine.transcribe(b"\0" * 6400, local_consent=True, request_id="second")
        finally:
            release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(engine.status["busy"])
        self.assertEqual(engine.audit.rows[-1]["status"], "cancelled")

    async def test_shutdown_releases_native_model_in_worker_and_rejects_new_work(self) -> None:
        released: list[int] = []
        class Model:
            def __del__(self) -> None:
                released.append(threading.get_ident())
        engine = await self.make_engine(loader=lambda path: Model())
        await engine.stop()
        self.assertEqual(len(released), 1)
        self.assertNotEqual(released[0], threading.get_ident())
        with self.assertRaisesRegex(RuntimeError, "microphone_closed"):
            await engine.transcribe(b"\0" * 6400, local_consent=True, request_id="test")

    async def test_cancelled_loading_releases_late_model_outside_event_loop(self) -> None:
        started, release = threading.Event(), threading.Event()
        destroyed: list[int] = []
        class Model:
            def __del__(self) -> None:
                destroyed.append(threading.get_ident())
        def load(path: Path) -> object:
            started.set(); release.wait(5)
            return Model()
        engine = LocalSpeechInput(MemoryAudit(), enabled=True, loader=load)
        task = asyncio.create_task(engine.start())
        while not started.is_set():
            await asyncio.sleep(0.001)
        task.cancel(); release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await engine.stop()
        self.assertEqual(len(destroyed), 1)
        self.assertNotEqual(destroyed[0], threading.get_ident())


class ModelInstallerTests(unittest.TestCase):
    def test_zip_paths_and_links_cannot_escape_model_directory(self) -> None:
        for name in ("../escape", f"{NAME}/../escape", f"{NAME}/C:/escape", "/escape", "another-model/file"):
            content = io.BytesIO()
            with zipfile.ZipFile(content, "w") as archive:
                archive.writestr(name, b"test")
            with tempfile.TemporaryDirectory() as directory, zipfile.ZipFile(content) as archive:
                with self.assertRaises(ValueError):
                    validate_archive(archive, Path(directory))
        content = io.BytesIO()
        with zipfile.ZipFile(content, "w") as archive:
            info = zipfile.ZipInfo(f"{NAME}/link"); info.external_attr = 0o120777 << 16
            archive.writestr(info, "../escape")
        with tempfile.TemporaryDirectory() as directory, zipfile.ZipFile(content) as archive:
            with self.assertRaises(ValueError):
                validate_archive(archive, Path(directory))

    def test_original_model_directory_is_accepted(self) -> None:
        content = io.BytesIO()
        with zipfile.ZipFile(content, "w") as archive:
            archive.writestr(f"{NAME}/am/final.mdl", b"test")
        with tempfile.TemporaryDirectory() as directory, zipfile.ZipFile(content) as archive:
            validate_archive(archive, Path(directory))
