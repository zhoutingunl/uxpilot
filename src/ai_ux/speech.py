"""Separate MiniMax TTS/ASR services; chat/video models never receive audio."""

from __future__ import annotations

import asyncio
import json
import time
import wave

import httpx

from .llm import read_key
from .media import command
from .schema import Assertion, Speech, Task
from .verifier import check_events


class SpeechError(RuntimeError):
    """Only stable error codes, never provider bodies or credentials."""


class SpeechClient:
    def __init__(self, config: Speech, evidence, client: httpx.AsyncClient | None = None):
        self.config, self.evidence = config, evidence
        self.client = client or httpx.AsyncClient(follow_redirects=False)
        self.owns_client = client is None
        self.calls = []

    async def close(self):
        if self.owns_client:
            await self.client.aclose()

    async def request(self, purpose, endpoint, *, payload=None, audio=None, language=None):
        record = {
            "provider": "minimax",
            "purpose": "speech_" + purpose,
            "model": self.config.tts_model if purpose == "tts" else self.config.asr_model,
        }
        started = time.monotonic()
        try:
            if len(self.calls) >= 2:
                raise SpeechError("speech_call_budget_exhausted")
            key = read_key(self.config)
            if not key:
                raise SpeechError("speech_credential_missing")
            headers = {"Authorization": "Bearer " + key}
            if language:
                headers["language"] = language
            kwargs = {"json": payload}
            if audio is not None:
                if not audio.is_file() or audio.stat().st_size > self.config.max_audio_bytes:
                    raise SpeechError("speech_audio_missing_or_oversized")
                kwargs = {
                    "data": {"model": self.config.asr_model, "response_format": "json"},
                    "files": {"file": ("response.wav", audio.read_bytes(), "audio/wav")},
                }
            async with asyncio.timeout(self.config.timeout_seconds):
                async with self.client.stream(
                    "POST",
                    self.config.base_url.rstrip("/") + endpoint,
                    headers=headers,
                    timeout=self.config.timeout_seconds,
                    **kwargs,
                ) as response:
                    if response.status_code != 200:
                        raise SpeechError(f"speech_http_{response.status_code}")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > self.config.max_audio_bytes * 2 + 100_000:
                            raise SpeechError("speech_response_too_large")
            data = json.loads(body)
            code = (data.get("base_resp") or {}).get("status_code", 0)
            if data.get("error") or code != 0:
                raise SpeechError(f"speech_provider_{code}" if type(code) is int else "speech_provider_error")
            if not isinstance(data, dict):
                raise SpeechError("speech_invalid_response")
            usage = {}
            extra = data.get("extra_info") or {}
            if type(extra.get("usage_characters")) is int:
                usage["usage_characters"] = extra["usage_characters"]
            if isinstance(data.get("duration"), (int, float)):
                usage["audio_duration_seconds"] = data["duration"]
            record.update(status="ok", usage=usage)
            return data
        except SpeechError as error:
            record.update(status="error", error=str(error))
            raise
        except (httpx.HTTPError, TimeoutError, ValueError, TypeError, AttributeError) as error:
            record.update(status="error", error="speech_" + type(error).__name__)
            raise SpeechError(record["error"]) from None
        finally:
            record["duration_ms"] = round((time.monotonic() - started) * 1000)
            self.calls.append(record)
            self.evidence.event("speech_call", **record)
            self.evidence.event("model_call", **record)
            self.evidence.write("speech-calls.json", self.calls)

    async def prepare(self, task: Task):
        if not task.audio or task.audio.source != "minimax_tts":
            return task
        audio = task.audio
        data = await self.request(
            "tts",
            "/v1/t2a_v2",
            payload={
                "model": self.config.tts_model,
                "text": audio.transcript,
                "stream": False,
                "voice_setting": {"voice_id": self.config.voice_id, "speed": 1, "vol": 1, "pitch": 0},
                "audio_setting": {"sample_rate": 32000, "format": "wav", "channel": 1},
                "language_boost": {"zh": "Chinese", "en": "English", "fr": "French"}[audio.language],
                "output_format": "hex",
            },
        )
        try:
            raw = bytes.fromhex(data["data"]["audio"])
            if not raw or len(raw) > self.config.max_audio_bytes:
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise SpeechError("tts_invalid_audio") from None
        original = self.evidence.root / "input-speech.wav"
        original.write_bytes(raw)
        try:
            with wave.open(str(original)) as wav:
                duration_ms = wav.getnframes() * 1000 / wav.getframerate()
                if wav.getnchannels() != 1 or duration_ms <= 0 or duration_ms + 1000 > audio.hold_ms:
                    raise ValueError
        except (wave.Error, EOFError, ValueError):
            raise SpeechError("tts_audio_does_not_fit_hold_window") from None
        loop = self.evidence.root / "microphone.wav"
        await command(
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(original),
            "-af",
            f"adelay={audio.start_ms + 500},apad=whole_dur={audio.loop_ms / 1000}",
            "-t",
            str(audio.loop_ms / 1000),
            "-ar",
            "48000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(loop),
        )
        self.evidence.write(
            "input-speech.json",
            {
                "model": self.config.tts_model,
                "voice_id": self.config.voice_id,
                "language": audio.language,
                "text": audio.transcript,
                "duration_ms": round(duration_ms),
                "input_kind": "synthetic_audio",
                "microphone_file": loop.name,
            },
        )
        task = task.model_copy(deep=True)
        task.audio.path = str(loop.resolve())
        return task

    async def review_response(self, runtime, task):
        result = {
            "status": "not_received",
            "model": self.config.asr_model,
            "language": task.response_language,
        }
        try:
            if not runtime.response_audio_bytes:
                return result
            # Allow the browser's last playback-status report to arrive before
            # snapshotting. A partial stream cannot prove a complete response.
            for _ in range(20):
                if check_events(
                    Assertion(id="complete", kind="voice_turn_completed"), self.evidence.events
                ) and check_events(Assertion(id="played", kind="audio_played"), self.evidence.events):
                    break
                await runtime.page.wait_for_timeout(250)
            else:
                result["status"] = "incomplete_playback"
                return result
            clip = runtime.export_response_audio()
            if clip is None:
                result["status"] = "invalid_or_incomplete_audio"
                return result
            data = await self.request(
                "asr", "/v1/speech_to_text", audio=clip, language=task.response_language
            )
            text = data.get("text")
            if not isinstance(text, str) or not text.strip() or len(text) > 16000:
                raise SpeechError("asr_empty_or_invalid_transcript")
            result.update(status="completed", transcript=text, audio_file=clip.name)
            self.evidence.event("response_transcript", **result)
            return result
        except SpeechError as error:
            result.update(status="error", error=str(error))
            return result
        finally:
            self.evidence.write("speech-review.json", result)
