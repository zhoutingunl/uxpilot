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


async def encode_video(source: Path, destination: Path, max_seconds: int, max_bytes: int):
    raw = await command(
        "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(source)
    )
    duration = float(json.loads(raw)["format"]["duration"])
    if duration > max_seconds:
        raise RuntimeError("video duration exceeds limit; evidence retained without silently trimming")
    await command(
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-an",
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
        "duration_seconds": duration,
        "fps": 2,
        "bytes": destination.stat().st_size,
        "audio": "silent_browser_recording",
        "timing_note": "Contains model decision waiting; use event timestamps to distinguish product wait.",
    }


def review_intervals(events: list[dict], duration: float):
    """Keep user actions/product transitions, remove long LLM deliberation-only waits.

    Original video is always retained. Mapping makes the edited clock explicit.
    """
    intervals = [(0.0, min(8.0, duration)), (max(0, duration - 3), duration)]
    active = None
    for event in events:
        t = min(duration, event["t_ms"] / 1000)
        kind = event["kind"]
        if kind == "agent_action_start":
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


async def review_video(source: Path, destination: Path, events: list[dict], duration: float, max_bytes: int):
    mapping = review_intervals(events, duration)
    filters = []
    for i, part in enumerate(mapping):
        filters.append(
            f"[0:v]trim=start={part['session_start_s']}:end={part['session_end_s']},setpts=PTS-STARTPTS[v{i}]"
        )
    filters.append(
        "".join(f"[v{i}]" for i in range(len(mapping))) + f"concat=n={len(mapping)}:v=1:a=0,fps=2[v]"
    )
    await command(
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
        "-an",
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
    return {
        "path": destination.name,
        "segments": mapping,
        "duration_seconds": mapping[-1]["review_end_s"] if mapping else 0,
        "editing": "Removed LLM deliberation-only waiting; original session.mp4 retained. No product speedup within retained intervals.",
    }


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
