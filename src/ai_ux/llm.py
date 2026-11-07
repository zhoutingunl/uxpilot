from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from .schema import Credentials, Models, Provider

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """Deliberately excludes response bodies and credentials from messages."""


def read_key(provider: Credentials) -> str:
    key = os.environ.get(provider.key_env, "").strip()
    if key:
        return key
    if provider.key_file:
        try:
            return Path(provider.key_file).expanduser().read_text().strip()
        except OSError:
            pass
    return ""


def image_part(path: Path):
    return {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()},
    }


def video_part(path: Path, max_bytes: int):
    if path.stat().st_size > max_bytes:
        raise LLMError("video exceeds configured request byte limit")
    return {
        "type": "video_url",
        "video_url": {"url": "data:video/mp4;base64," + base64.b64encode(path.read_bytes()).decode()},
    }


def provider_error_code(data):
    error = data.get("error") or {}
    code = error.get("http_code") if isinstance(error, dict) else None
    code = code or data.get("base_resp", {}).get("status_code")
    # Never include arbitrary provider response text (it can echo credentials/payloads).
    return (
        "provider_error_" + str(code)
        if isinstance(code, (int, float)) or (isinstance(code, str) and code.isdigit())
        else "provider_error"
    )


def parse_json(content: str):
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    # Do not salvage truncated or mixed prose by guessing which object is the answer.
    return json.loads(text)


class LLM:
    def __init__(self, config: Models, on_call=None, client: httpx.AsyncClient | None = None):
        self.config = config
        self.client = client or httpx.AsyncClient(follow_redirects=False)
        self.owns_client = client is None
        self.calls: list[dict] = []
        self.on_call = on_call or (lambda _: None)
        self.unavailable: set[str] = set()

    async def close(self):
        if self.owns_client:
            await self.client.aclose()

    async def _request(self, provider: Provider, messages: list[dict]):
        key = read_key(provider)
        if not key:
            raise LLMError("credential_missing")
        payload = {
            "model": provider.model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": provider.max_tokens,
            "stream": provider.stream,
            **provider.request_options,
        }
        # These are protocol invariants, not model-controlled overrides.
        payload.update(model=provider.model, messages=messages, stream=provider.stream)
        if provider.name == "minimax" and provider.model == "MiniMax-M3.1-Flash":
            payload.update(thinking={"type": "adaptive"}, reasoning_split=True)
            payload.setdefault("reasoning_effort", "low")
        content, usage, finish, stream_done = "", {}, None, False
        try:
            async with asyncio.timeout(provider.timeout_seconds):
                async with self.client.stream(
                    "POST",
                    provider.endpoint,
                    headers={"Authorization": f"Bearer {key}"},
                    json=payload,
                    timeout=httpx.Timeout(provider.timeout_seconds, connect=15),
                ) as r:
                    if r.status_code != 200:
                        raise LLMError(f"http_{r.status_code}")
                    if "text/event-stream" not in r.headers.get("content-type", ""):
                        raw = await r.aread()
                        if len(raw) > 2_000_000:
                            raise LLMError("response_too_large")
                        data = json.loads(raw)
                        if data.get("error") or data.get("base_resp", {}).get("status_code", 0) != 0:
                            raise LLMError(provider_error_code(data))
                        choice = (data.get("choices") or [{}])[0]
                        content = choice.get("message", {}).get("content") or ""
                        finish = choice.get("finish_reason")
                        usage = data.get("usage", {})
                        stream_done = True
                    else:
                        received_bytes = 0
                        async for line in r.aiter_lines():
                            received_bytes += len(line)
                            if received_bytes > 4_000_000:
                                raise LLMError("response_too_large")
                            if not line.startswith("data:"):
                                continue
                            raw = line[5:].strip()
                            if raw == "[DONE]":
                                stream_done = True
                                break
                            data = json.loads(raw)
                            if data.get("error") or data.get("base_resp", {}).get("status_code", 0) != 0:
                                raise LLMError(provider_error_code(data))
                            usage = data.get("usage") or usage
                            for choice in data.get("choices", []):
                                delta = choice.get("delta") or {}
                                content += delta.get("content") or ""
                                finish = choice.get("finish_reason") or finish
                                # reasoning_content is deliberately neither output nor persisted.
                        if finish == "stop":
                            stream_done = True
        except (httpx.HTTPError, TimeoutError) as e:
            raise LLMError(type(e).__name__) from None
        except (json.JSONDecodeError, TypeError, AttributeError):
            raise LLMError("invalid_provider_envelope") from None
        if not stream_done or finish == "length" or not isinstance(content, str) or not content.strip():
            raise LLMError("truncated_or_empty_final_content")
        return content, usage

    async def structured(
        self,
        system: str,
        prompt: str,
        schema: type[T],
        *,
        images: list[Path] = (),
        video: Path | None = None,
        video_max_bytes: int = 18_000_000,
        purpose: str = "explore",
        providers: list[Provider] | None = None,
    ) -> tuple[T, dict]:
        parts = [{"type": "text", "text": prompt}]
        parts.extend(image_part(path) for path in images)
        if video:
            parts.append(video_part(video, video_max_bytes))
        messages = [
            {
                "role": "system",
                "content": system
                + "\n只输出满足下面 JSON Schema 的 JSON 对象，不要 Markdown、推理过程或额外文字。\n"
                + json.dumps(schema.model_json_schema(), ensure_ascii=False),
            },
            {"role": "user", "content": parts},
        ]
        errors = []
        for provider in providers or [self.config.primary, self.config.fallback]:
            if provider.name in self.unavailable:
                continue
            validation_failures = 0
            transport_failures = 0
            for attempt in range(self.config.validation_retries + self.config.transport_retries + 1):
                if len(self.calls) >= self.config.max_calls_per_run:
                    raise LLMError("model_call_budget_exhausted")
                record = {
                    "call_id": uuid.uuid4().hex,
                    "provider": provider.name,
                    "model": provider.model,
                    "purpose": purpose,
                    "attempt": attempt + 1,
                    "video": bool(video),
                    "images": len(images),
                }
                started = time.monotonic()
                try:
                    raw, usage = await self._request(provider, messages)
                    record["usage"] = usage
                    result = schema.model_validate(parse_json(raw))
                    record.update(status="ok", usage=usage)
                    return result, record
                except (ValidationError, json.JSONDecodeError):
                    record.update(status="error", error="invalid_json_schema")
                    # Keep correction independent of untrusted output; never echo private reasoning.
                    messages = messages[:2] + [
                        {
                            "role": "user",
                            "content": "上次最终输出没有通过 JSON 校验。重新输出完整 JSON，所有字段严格符合 Schema；不要解释。",
                        }
                    ]
                    errors.append(f"{provider.name}:invalid_json_schema")
                    validation_failures += 1
                    if validation_failures > self.config.validation_retries:
                        break
                except LLMError as e:
                    record.update(status="error", error=str(e))
                    errors.append(f"{provider.name}:{e}")
                    if str(e) == "credential_missing":
                        self.unavailable.add(provider.name)
                    transient = str(e) in {
                        "ConnectTimeout",
                        "ConnectError",
                        "ReadError",
                        "RemoteProtocolError",
                        "http_502",
                        "http_503",
                        "http_504",
                    }
                    if transient and transport_failures < self.config.transport_retries:
                        transport_failures += 1
                        await asyncio.sleep(1)
                        continue
                    break
                finally:
                    record["duration_ms"] = round((time.monotonic() - started) * 1000)
                    self.calls.append(record)
                    self.on_call(record)
        raise LLMError("model_chain_failed: " + "; ".join(errors))
