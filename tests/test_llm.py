import json

import httpx
import pytest
from pydantic import BaseModel

from ai_ux.llm import LLM, LLMError
from ai_ux.schema import Models, Provider


class Answer(BaseModel):
    answer: str


def providers(monkeypatch):
    monkeypatch.setenv("TEST_PRIMARY_KEY", "primary-secret")
    monkeypatch.setenv("TEST_BACKUP_KEY", "backup-secret")
    return Models(
        primary=Provider(
            name="zhipu",
            model="glm-5.3-Flash",
            endpoint="https://primary.test/chat",
            key_env="TEST_PRIMARY_KEY",
        ),
        fallback=Provider(
            name="minimax",
            model="MiniMax-M3.1-Flash",
            endpoint="https://backup.test/chat",
            key_env="TEST_BACKUP_KEY",
        ),
        validation_retries=1,
    )


def sse(content, finish="stop", reasoning=None, done=True):
    packets = []
    if reasoning:
        packets.append({"choices": [{"delta": {"reasoning_content": reasoning}}]})
    packets.extend(
        [
            {"choices": [{"delta": {"content": content[:5]}}]},
            {
                "choices": [{"delta": {"content": content[5:]}, "finish_reason": finish}],
                "usage": {"total_tokens": 42},
            },
        ]
    )
    stream = "\n\n".join("data: " + json.dumps(p) for p in packets) + "\n\n"
    if done:
        stream += "data: [DONE]\n\n"
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=stream)


async def test_primary_rate_limit_falls_back_preserving_video_and_not_logging_keys(monkeypatch, tmp_path):
    config = providers(monkeypatch)
    seen = []

    def handle(request):
        payload = json.loads(request.content)
        seen.append((request.url.host, payload))
        if request.url.host == "primary.test":
            return httpx.Response(429)
        assert payload["thinking"] == {"type": "adaptive"}
        assert payload["reasoning_split"] is True
        return sse('{"answer":"red then blue"}', reasoning="must never be persisted")

    video = tmp_path / "test.mp4"
    video.write_bytes(b"video fixture")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        llm = LLM(config, client=client)
        answer, meta = await llm.structured("system", "question", Answer, video=video)
    assert answer.answer == "red then blue"
    assert meta["provider"] == "minimax"
    assert len(seen) == 2
    for _, payload in seen:
        assert payload["messages"][1]["content"][1]["type"] == "video_url"
    assert "must never" not in json.dumps(llm.calls)
    assert "secret" not in json.dumps(llm.calls)
    assert llm.calls[0]["error"] == "http_429"
    assert meta["usage"]["total_tokens"] == 42


async def test_invalid_schema_retries_are_bounded(monkeypatch):
    config = providers(monkeypatch)
    seen = []

    def handle(request):
        seen.append(request)
        return sse('{"wrong":true}')

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        llm = LLM(config, client=client)
        with pytest.raises(LLMError, match="model_chain_failed"):
            await llm.structured("system", "question", Answer)
    assert len(seen) == 4
    assert all(c["error"] == "invalid_json_schema" for c in llm.calls)


async def test_reasoning_only_and_truncated_stream_are_not_final_answers(monkeypatch):
    config = providers(monkeypatch)

    def handle(request):
        return sse("", finish="length", reasoning='{"answer":"fake success"}', done=False)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        llm = LLM(config, client=client)
        with pytest.raises(LLMError, match="truncated_or_empty"):
            await llm.structured("system", "question", Answer)


async def test_provider_error_inside_200_stream_is_failure(monkeypatch):
    config = providers(monkeypatch)

    def handle(request):
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content='data: {"error":{"http_code":529,"message":"sensitive echo"}}\n\n',
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        llm = LLM(config, client=client)
        with pytest.raises(LLMError, match="provider_error_529") as error:
            await llm.structured("s", "q", Answer)
    assert "sensitive echo" not in str(error.value)


async def test_call_budget_prevents_more_network_requests(monkeypatch):
    config = providers(monkeypatch)
    config.max_calls_per_run = 1
    seen = []

    def handle(request):
        seen.append(request)
        return sse('{"answer":"ok"}')

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        llm = LLM(config, client=client)
        await llm.structured("s", "q", Answer)
        with pytest.raises(LLMError, match="budget"):
            await llm.structured("s", "q", Answer)
    assert len(seen) == 1


async def test_transient_connection_error_retries_once_before_fallback(monkeypatch):
    config = providers(monkeypatch)
    seen = []

    def handle(request):
        seen.append(request.url.host)
        if len(seen) == 1:
            raise httpx.ConnectTimeout("not logged")
        return sse('{"answer":"ok"}')

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        llm = LLM(config, client=client)
        answer, meta = await llm.structured("s", "q", Answer)
    assert seen == ["primary.test", "primary.test"]
    assert answer.answer == "ok" and meta["provider"] == "zhipu"


async def test_minimax_nonstream_envelope_preserves_final_content(monkeypatch):
    config = providers(monkeypatch)
    config.fallback.stream = False

    def handle(request):
        if request.url.host == "primary.test":
            return httpx.Response(503)
        payload = json.loads(request.content)
        assert payload["stream"] is False
        return httpx.Response(
            200,
            json={
                "base_resp": {"status_code": 0},
                "choices": [
                    {
                        "message": {"content": '{"answer":"video read"}', "reasoning_content": "private"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"total_tokens": 12},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        llm = LLM(config, client=client)
        answer, meta = await llm.structured("s", "q", Answer)
    assert answer.answer == "video read" and meta["provider"] == "minimax"
    assert len(llm.calls) == 3
