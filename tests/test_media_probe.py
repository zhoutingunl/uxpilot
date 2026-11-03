import sys

import pytest
from test_orchestration import config

from ai_ux import probe as probe_module
from ai_ux.llm import LLMError
from ai_ux.media import command, encode_video, review_video
from ai_ux.probe import ProbeResult


async def test_commands_fail_and_timeout_without_leaking_stderr():
    with pytest.raises(RuntimeError, match="exit 1") as e:
        await command(sys.executable, "-c", "import sys;print('secret',file=sys.stderr);sys.exit(1)")
    assert "secret" not in str(e.value)
    with pytest.raises(TimeoutError):
        await command(sys.executable, "-c", "import time;time.sleep(10)", timeout=0.03)


async def test_video_limits_are_explicit(tmp_path):
    source = tmp_path / "source.mp4"
    await command(
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=64x64:r=2",
        "-t",
        "2",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(source),
    )
    with pytest.raises(RuntimeError, match="duration"):
        await encode_video(source, tmp_path / "out.mp4", 1, 1000000)
    with pytest.raises(RuntimeError, match="byte limit"):
        await encode_video(source, tmp_path / "out.mp4", 10, 1)
    with pytest.raises(RuntimeError, match="byte limit"):
        await review_video(source, tmp_path / "r.mp4", [], 2, 1)
    assert (await encode_video(source, tmp_path / "ok.mp4", 10, 1000000))["fps"] == 2


async def test_probe_reads_generated_video_and_records_each_provider(tmp_path, monkeypatch):
    cfg = config(tmp_path, monkeypatch)
    codes = iter(["AB12", "CD34"])
    monkeypatch.setattr(probe_module.secrets, "token_hex", lambda _: next(codes))

    class Model:
        def __init__(self, *a, **kw):
            pass

        async def structured(self, *a, **kw):
            assert kw["video"].suffix == ".mp4" and kw["video"].stat().st_size > 0
            if kw["providers"][0].name == "minimax":
                raise LLMError("overload")
            return ProbeResult(first_color="red", last_color="blue", first_code="AB12", last_code="CD34"), {}

        async def close(self):
            pass

    monkeypatch.setattr(probe_module, "LLM", Model)
    result = await probe_module.probe(cfg, tmp_path / "probe")
    assert result["providers"][0]["passed"] and not result["providers"][1]["passed"]
    assert (tmp_path / "probe/probe.json").is_file()
