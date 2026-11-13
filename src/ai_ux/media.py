from __future__ import annotations

import asyncio
import json
from pathlib import Path


async def command(*args, timeout=90):
    process = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if process.returncode:
        raise RuntimeError(f"{args[0]} failed (exit {process.returncode})")
    return stdout


async def probe_media(path: Path):
    data = json.loads(
        await command(
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type",
            "-of",
            "json",
            str(path),
        )
    )
    return {
        "duration_seconds": float(data["format"]["duration"]),
        "has_audio": any(stream.get("codec_type") == "audio" for stream in data.get("streams", [])),
    }


async def encode_video(
    source: Path, destination: Path, max_seconds: int, max_bytes: int, *, audio_source: Path | None = None
):
    media = await probe_media(source)
    duration = media["duration_seconds"]
    if duration > max_seconds:
        raise RuntimeError("video duration exceeds limit; evidence retained without silently trimming")
    args = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(source)]
    if audio_source:
        args += ["-i", str(audio_source)]
    args += ["-map", "0:v:0"]
    audio = bool(audio_source) or media["has_audio"]
    if audio:
        args += [
            "-map",
            "1:a:0" if audio_source else "0:a:0",
            "-af",
            "apad",
            "-t",
            str(duration),
            "-c:a",
            "aac",
            "-b:a",
            "128k",
        ]
    else:
        args += ["-an"]
    await command(
        *args,
        "-vf",
        "fps=2,scale='min(960,iw)':-2",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "30",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(destination),
    )
    if destination.stat().st_size > max_bytes:
        raise RuntimeError("encoded video exceeds configured byte limit")
    return {
        "path": destination.name,
        "duration_seconds": (await probe_media(destination))["duration_seconds"],
        "fps": 2,
        "bytes": destination.stat().st_size,
        "audio": "recorded_browser_audio" if audio else "silent_browser_recording",
        "timing_note": "Contains model decision waiting; use event timestamps to distinguish product wait.",
    }


def relative_video_events(events, start_ms):
    result = []
    for event in events:
        shifted = {**event, "t_ms": max(0, event["t_ms"] - start_ms)}
        if event.get("kind") == "recorded_audio":
            for field in ["start_ms", "end_ms"]:
                shifted[field] = max(0, event[field] - start_ms)
        result.append(shifted)
    return result


def review_intervals(events: list[dict], duration: float):
    """Keep user actions/product transitions, remove long LLM deliberation-only waits.

    Original video is always retained. Mapping makes the edited clock explicit.
    """
    intervals = [(0.0, min(8.0, duration)), (max(0, duration - 3), duration)]
    active = None
    for event in events:
        t = min(duration, event["t_ms"] / 1000)
        kind = event["kind"]
        if kind == "recorded_audio":
            intervals.append(
                (max(0, event["start_ms"] / 1000 - 0.25), min(duration, event["end_ms"] / 1000 + 0.25))
            )
        elif kind == "agent_action_start":
            active = t
        elif kind == "agent_action_end" and active is not None:
            intervals.append((max(0, active - 0.5), min(duration, t + 1.5)))
            active = None
        elif kind in {
            "display_state",
            "product_action",
            "voice_event",
            "first_audio",
            "audio_input_start",
            "audio_input_end",
        }:
            intervals.append((max(0, t - 0.8), min(duration, t + 1.5)))
    merged = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if merged and start <= merged[-1][1] + 0.3:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    cursor = 0.0
    mapping = []
    for start, end in merged:
        mapping.append(
            {
                "review_start_s": round(cursor, 3),
                "review_end_s": round(cursor + end - start, 3),
                "session_start_s": round(start, 3),
                "session_end_s": round(end, 3),
            }
        )
        cursor += end - start
    return mapping


async def silent_model_video(source: Path, destination: Path):
    """The vision-only model input has exactly the same picture/timeline, without audio."""
    await command(
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-c:v",
        "copy",
        "-an",
        str(destination),
    )
    return destination


async def review_video(source: Path, destination: Path, events: list[dict], duration: float, max_bytes: int):
    import math

    original = review_intervals(events, duration)
    # Cut on the 2 fps frame grid so A/V concat does not add per-segment padding drift.
    aligned = []
    for part in original:
        start = math.floor(part["session_start_s"] * 2) / 2
        end = min(duration, math.ceil(part["session_end_s"] * 2) / 2)
        if aligned and start <= aligned[-1][1]:
            aligned[-1][1] = max(aligned[-1][1], end)
        else:
            aligned.append([start, end])
    mapping, cursor = [], 0.0
    for start, end in aligned:
        mapping.append(
            {
                "review_start_s": cursor,
                "review_end_s": cursor + end - start,
                "session_start_s": start,
                "session_end_s": end,
            }
        )
        cursor += end - start
    audio = (await probe_media(source))["has_audio"]
    filters = []
    for i, part in enumerate(mapping):
        a, b = part["session_start_s"], part["session_end_s"]
        filters.append(f"[0:v]trim=start={a}:end={b},setpts=PTS-STARTPTS[v{i}]")
        if audio:
            filters.append(
                f"[0:a]atrim=start={a}:end={b},asetpts=PTS-STARTPTS,apad,atrim=duration={b - a}[a{i}]"
            )
    inputs = "".join(f"[v{i}]" + (f"[a{i}]" if audio else "") for i in range(len(mapping)))
    filters.append(inputs + f"concat=n={len(mapping)}:v=1:a={int(audio)}[v]" + ("[a]" if audio else ""))
    args = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[v]",
    ]
    args += ["-map", "[a]", "-c:a", "aac", "-b:a", "128k"] if audio else ["-an"]
    await command(
        *args,
        "-r",
        "2",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "30",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(destination),
    )
    if destination.stat().st_size > max_bytes:
        raise RuntimeError("review video exceeds request byte limit")
    result = {
        "path": destination.name,
        "segments": mapping,
        "duration_seconds": cursor,
        "audio": "preserved" if audio else "silent",
        "editing": "Removed LLM deliberation-only waiting; audio/video use identical cuts and audible intervals are retained.",
    }
    if audio:
        model = await silent_model_video(destination, destination.with_name("judge.mp4"))
        result["model_path"] = model.name
    return result


async def split_review_video(source: Path, directory: Path, seconds: int):
    directory.mkdir(parents=True, exist_ok=True)
    raw = await command(
        "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(source)
    )
    duration = float(json.loads(raw)["format"]["duration"])
    import math

    count = math.ceil(duration / seconds)
    if count > 24:
        raise RuntimeError("fallback video exceeds 24 chunks; increase task/media limits or shorten task")
    clips = []
    for i in range(count):
        destination = directory / f"clip-{i:03}.mp4"
        await command(
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-ss",
            str(i * seconds),
            "-i",
            str(source),
            "-t",
            str(seconds),
            "-vf",
            f"scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2,tpad=stop_mode=clone:stop_duration={seconds}",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-r",
            "2",
            str(destination),
        )
        clips.append(
            {
                "path": destination,
                "start_s": i * seconds,
                "end_s": min(duration, (i + 1) * seconds),
                "padding_seconds": max(0, (i + 1) * seconds - duration),
            }
        )
    return clips


def audio_note(video):
    capture = video.get("audio_capture") or {}
    if video.get("audio") == "recorded_browser_audio":
        kinds = {stream.get("kind") for stream in capture.get("streams", [])}
        sources = " + ".join(
            label
            for key, label in [("input", "测试麦克风输入"), ("output", "网页 Web Audio 输出")]
            if key in kinds
        )
        note = (
            "视频含同步音轨" + ("：" + sources if sources else "") + "。这是浏览器数字音频，不是扬声器实录。"
        )
        if capture.get("warnings"):
            note += " 音频采集存在缺失或限制，请查看音频证据。"
        return note
    if capture:
        return "本轮未捕获到有效声音，视频不含音轨；请结合音频采集状态核对，不能据此认定产品无声。"
    return "此视频不含音轨，不能用它核实音频效果。"
