"""Bounded capture of DuckKing's digital Web Audio output and authorized microphone input."""

from __future__ import annotations

import asyncio
import base64
import json
import math
import struct
from datetime import datetime
from pathlib import Path

from .media import command


class BrowserAudioRecorder:
    def __init__(self, evidence, *, input_enabled=False, max_seconds=600, max_bytes=256_000_000):
        self.evidence = evidence
        self.input_enabled = input_enabled
        self.max_seconds, self.max_bytes = max_seconds, max_bytes
        self.epoch_ms = datetime.fromisoformat(evidence.wall_started).timestamp() * 1000
        self.streams = {}
        self.warnings = set()
        self.bytes_received = 0
        self.page = None
        self.closed = False
        self.start_offset_ms = 0

    async def install(self, context):
        binding = "__uxAudioPacket"
        await context.expose_binding(binding, self.receive)
        script = (Path(__file__).parent / "recording/browser-audio.js").read_text()
        settings = {"binding": binding, "input": self.input_enabled, "maxBytes": self.max_bytes}
        await context.add_init_script(script=script.replace("__UX_AUDIO_SETTINGS__", json.dumps(settings)))

    def receive(self, source, packet):
        if (
            self.closed
            or self.page is None
            or source.get("page") != self.page
            or source.get("frame") != self.page.main_frame
        ):
            return
        if not isinstance(packet, dict):
            return
        if packet.get("type") == "warning":
            code = packet.get("code")
            if code in {
                "audio_capture_backpressure",
                "audio_capture_byte_limit",
                "audio_output_tap_failed",
                "audio_input_tap_failed",
                "html_media_audio_not_captured",
            }:
                self.warnings.add(code)
            return
        if packet.get("type") != "pcm":
            return
        try:
            kind, rate, channels = packet["kind"], packet["rate"], packet["channels"]
            if kind not in {"input", "output"} or (kind == "input" and not self.input_enabled):
                raise ValueError()
            if type(rate) is not int or not 8000 <= rate <= 192000 or channels not in {1, 2}:
                raise ValueError()
            encoded = packet["pcm"]
            if not isinstance(encoded, str) or len(encoded) > 40000:
                raise ValueError()
            raw = base64.b64decode(encoded, validate=True)
            if not raw or len(raw) % (2 * channels) or len(raw) > 16384:
                raise ValueError()
            start_ms = float(packet["epoch_ms"]) - self.epoch_ms
            if (
                not math.isfinite(start_ms)
                or not -2000 <= start_ms <= (self.max_seconds + 5) * 1000 + self.start_offset_ms
            ):
                raise ValueError()
            frames = len(raw) // (2 * channels)
            if start_ms < 0:
                skip = min(frames, round(-start_ms * rate / 1000))
                raw = raw[skip * channels * 2 :]
                frames -= skip
                start_ms = 0
            if not raw:
                return
            self.bytes_received += len(raw)
            if self.bytes_received > self.max_bytes:
                self.warnings.add("audio_capture_byte_limit")
                return
            key = str(packet["id"])[:100]
            if key not in self.streams:
                if len(self.streams) >= 8:
                    self.warnings.add("audio_capture_stream_limit")
                    return
                folder = self.evidence.root / "audio-capture"
                folder.mkdir(exist_ok=True)
                path = folder / f"track-{len(self.streams):02}.wav"
                file = path.open("w+b")
                file.write(b"\0" * 44)
                self.streams[key] = {
                    "kind": kind,
                    "clock": packet.get("clock", "unknown")
                    if packet.get("clock", "unknown")
                    in {"audio_context_output_timestamp", "audio_context_render_clock", "unknown"}
                    else "unknown",
                    "rate": rate,
                    "channels": channels,
                    "path": path,
                    "file": file,
                    "frames": 0,
                    "bytes": 0,
                    "intervals": [],
                    "uncertainty_ms": 0,
                }
            stream = self.streams[key]
            if (stream["rate"], stream["channels"], stream["kind"]) != (rate, channels, kind):
                raise ValueError()
            offset = round(start_ms * rate / 1000)
            final_size = (offset + frames) * channels * 2
            sizes = sum(s["frames"] * s["channels"] * 2 for s in self.streams.values())
            if (
                sizes - stream["frames"] * channels * 2 + max(final_size, stream["frames"] * channels * 2)
                > self.max_bytes
            ):
                self.warnings.add("audio_capture_byte_limit")
                return
            stream["file"].seek(44 + offset * channels * 2)
            stream["file"].write(raw)
            stream["frames"] = max(stream["frames"], offset + frames)
            stream["bytes"] += len(raw)
            end_ms = start_ms + frames * 1000 / rate
            spans = stream["intervals"]
            if spans and start_ms <= spans[-1][1] + 120:
                spans[-1][1] = max(spans[-1][1], end_ms)
            else:
                spans.append([start_ms, end_ms])
            stream["uncertainty_ms"] = max(stream["uncertainty_ms"], 2 * frames * 1000 / rate)
        except (ValueError, TypeError, KeyError):
            self.warnings.add("invalid_audio_packet")
        except OSError:
            self.warnings.add("audio_capture_write_failed")

    async def begin_input(self):
        if self.page:
            await self.page.evaluate("window.__uxAudioRecorder?.beginInput()")

    async def end_input(self):
        if self.page and not self.page.is_closed():
            await self.page.evaluate("window.__uxAudioRecorder?.endInput()")

    async def stop(self):
        if self.closed:
            return
        try:
            if self.page and not self.page.is_closed():
                stopped = await asyncio.wait_for(self.page.evaluate("window.__uxAudioRecorder?.stop()"), 5)
                if stopped is None:
                    self.warnings.add("audio_capture_unavailable")
        except Exception:
            self.warnings.add("audio_capture_stop_failed")
        self.closed = True
        for stream in self.streams.values():
            size = stream["frames"] * stream["channels"] * 2
            header = struct.pack(
                "<4sI4s4sIHHIIHH4sI",
                b"RIFF",
                36 + size,
                b"WAVE",
                b"fmt ",
                16,
                1,
                stream["channels"],
                stream["rate"],
                stream["rate"] * stream["channels"] * 2,
                stream["channels"] * 2,
                16,
                b"data",
                size,
            )
            file = stream.pop("file")
            try:
                file.seek(0)
                file.write(header)
                file.truncate(44 + size)
            except OSError:
                self.warnings.add("audio_capture_write_failed")
            finally:
                try:
                    file.close()
                except OSError:
                    self.warnings.add("audio_capture_write_failed")
            for start, end in stream["intervals"]:
                self.evidence.event(
                    "recorded_audio", source=stream["kind"], start_ms=round(start), end_ms=round(end)
                )
        self.evidence.write("audio-capture.json", self.metadata())

    def metadata(self):
        return {
            "status": "partial"
            if self.warnings
            else "recorded"
            if any(s["bytes"] for s in self.streams.values())
            else "silent",
            "capture": "browser_web_audio_and_authorized_microphone",
            "input_requested": self.input_enabled,
            "limitations": [
                "Digital Web Audio capture; not a physical speaker/microphone acoustic recording.",
                "HTML media and speechSynthesis outside Web Audio are not captured.",
            ],
            "warnings": sorted(self.warnings),
            "bytes_received": self.bytes_received,
            "clock_uncertainty_ms": max((s["uncertainty_ms"] for s in self.streams.values()), default=0),
            "streams": [
                {
                    k: (str(v.relative_to(self.evidence.root)) if k == "path" else v)
                    for k, v in s.items()
                    if k != "file"
                }
                for s in self.streams.values()
                if s["bytes"]
            ],
        }

    async def mix(self, video_start_ms, duration):
        streams = [s for s in self.streams.values() if s["bytes"] and s["frames"]]
        if not streams:
            return None
        destination = self.evidence.root / ".mixed-audio.wav"
        args = ["ffmpeg", "-y", "-loglevel", "error"]
        filters = []
        for i, stream in enumerate(streams):
            args += ["-i", str(stream["path"])]
            filters.append(
                f"[{i}:a]atrim=start={video_start_ms / 1000},asetpts=PTS-STARTPTS,aresample=48000,apad,atrim=duration={duration}[a{i}]"
            )
        count = len(streams)
        filters.append(
            "".join(f"[a{i}]" for i in range(count))
            + f"amix=inputs={count}:duration=longest:dropout_transition=0,volume={count},alimiter=limit=0.95:level=0:attack=1:release=20[out]"
        )
        await command(
            *args,
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[out]",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(destination),
            timeout=120,
        )
        return destination
