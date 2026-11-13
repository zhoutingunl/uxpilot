from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Credentials(StrictModel):
    key_env: str
    key_file: str | None = None


class Provider(Credentials):
    name: str
    model: str
    endpoint: str
    timeout_seconds: int = Field(default=120, ge=5, le=600)
    max_tokens: int = Field(default=4096, ge=256, le=16384)
    request_options: dict = Field(default_factory=dict)
    stream: bool = True
    video_chunk_seconds: int | None = Field(default=None, ge=2, le=60)


class Speech(Credentials):
    key_env: str = "MINIMAX_CN_API_KEY"
    key_file: str | None = "~/.secrets/minimax_api_key"
    base_url: str = "https://api.minimax.cn"
    tts_model: str = "speech-2.8-hd"
    asr_model: str = "asr-1.0"
    voice_id: str = "male-qn-qingse"
    timeout_seconds: int = Field(default=90, ge=5, le=240)
    max_audio_bytes: int = Field(default=8_000_000, ge=1000, le=50_000_000)
    transcribe_responses: bool = True

    @model_validator(mode="after")
    def trusted_endpoint(self):
        from urllib.parse import urlsplit

        url = urlsplit(self.base_url)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("speech base_url must be credential-free HTTPS")
        return self


class Models(StrictModel):
    primary: Provider
    fallback: Provider
    validation_retries: int = Field(default=1, ge=0, le=2)
    max_calls_per_run: int = Field(default=35, ge=1, le=200)
    transport_retries: int = Field(default=1, ge=0, le=2)


class Browser(StrictModel):
    channel: str | None = "chrome"
    executable_path: str | None = None
    headless: bool = True
    width: int = Field(default=1440, ge=320, le=2560)
    height: int = Field(default=1050, ge=480, le=1600)
    locale: str = "zh-CN"
    action_timeout_ms: int = Field(default=7000, ge=1000, le=30000)
    video: bool = True
    record_audio: bool = True
    audio_max_bytes: int = Field(default=256_000_000, ge=100_000, le=512_000_000)
    video_max_seconds: int = Field(default=180, ge=5, le=600)
    video_max_bytes: int = Field(default=18_000_000, ge=1000, le=50_000_000)
    redact_selectors: list[str] = Field(default_factory=lambda: ["input[type=password]", "#activation"])


class App(StrictModel):
    name: str
    base_url: str
    allowed_hosts: list[str]
    environment: Literal["local", "preview", "production_evaluation"] = "preview"
    production_evaluation_authorized: bool = False
    adapter: Literal["generic", "duckking"] = "generic"
    source_repo: str | None = None
    resource_hosts: list[str] = Field(default_factory=list)
    product_brief: str | None = None
    resource_policy: Literal["strict", "public_readonly"] = "strict"

    @model_validator(mode="after")
    def production_scope(self):
        if self.environment == "production_evaluation" and not self.production_evaluation_authorized:
            raise ValueError("production_evaluation requires explicit authorization in configuration")
        return self


class Assertion(StrictModel):
    id: str
    kind: Literal[
        "model_ready",
        "page_loaded",
        "no_page_errors",
        "no_horizontal_overflow",
        "element_visible",
        "url_contains",
        "action_completed",
        "text",
        "voice_transcript",
        "audio_received",
        "audio_played",
        "voice_turn_completed",
        "voice_session_language",
        "page_language",
        "response_speech",
        "model_before_action",
        "viewport_overlap",
    ]
    selector: str | None = None
    contains: str | None = None
    action_name: str | None = None
    source: str | None = None
    role: str | None = None
    other_selector: str | None = None
    required: bool = True
    scope: Literal["task", "page_health"] = "task"

    @model_validator(mode="after")
    def required_fields(self):
        if self.kind == "action_completed" and not self.action_name:
            raise ValueError("action_completed requires action_name")
        if self.kind in {"text", "viewport_overlap", "element_visible"} and not self.selector:
            raise ValueError(f"{self.kind} requires selector")
        if self.kind == "viewport_overlap" and not self.other_selector:
            raise ValueError("viewport_overlap requires other_selector")
        if (
            self.kind
            in {"text", "url_contains", "page_language", "voice_session_language", "response_speech"}
            and not self.contains
        ):
            raise ValueError(f"{self.kind} requires contains")
        return self


class AudioFixture(StrictModel):
    source: Literal["file", "minimax_tts"] = "file"
    path: str | None = None
    transcript: str
    language: Literal["zh", "en", "fr"] = "zh"
    loop_ms: int = Field(default=12000, ge=2000, le=30000)
    start_ms: int = Field(default=1500, ge=0, le=25000)
    hold_ms: int = Field(default=6200, ge=300, le=14500)
    input_kind: Literal["synthetic_audio", "authorized_human_audio"] = "synthetic_audio"

    @model_validator(mode="after")
    def within_cycle(self):
        if self.source == "file" and not self.path:
            raise ValueError("file audio requires path")
        if self.source == "minimax_tts" and self.input_kind != "synthetic_audio":
            raise ValueError("generated audio must be synthetic_audio")
        if self.start_ms + self.hold_ms >= self.loop_ms:
            raise ValueError("audio hold must end before loop restarts")
        return self


class Task(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,79}$")
    goal: str
    persona: str = "第一次使用产品的普通用户"
    start_path: str = "/"
    max_steps: int = Field(default=12, ge=1, le=40)
    timeout_seconds: int = Field(default=300, ge=15, le=1800)
    repeats: int = Field(default=1, ge=1, le=5)
    browser: Browser | None = None
    audio: AudioFixture | None = None
    response_language: Literal["zh", "en", "fr"] | None = None
    assertions: list[Assertion] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    expected_outcome: str | None = None

    def public(self):
        """Private selectors, assertions and source path must not reach Explorer."""
        return {
            "id": self.id,
            "goal": self.goal,
            "persona": self.persona,
            "constraints": self.constraints,
            "voice_fixture": (
                {"transcript": self.audio.transcript, "input_kind": self.audio.input_kind}
                if self.audio
                else None
            ),
        }


class Config(StrictModel):
    app: App
    models: Models
    browser: Browser = Field(default_factory=Browser)
    speech: Speech | None = None
    output_dir: str = "artifacts/agent"
    tasks: list[Task]

    @model_validator(mode="after")
    def unique_tasks(self):
        if len({t.id for t in self.tasks}) != len(self.tasks):
            raise ValueError("task ids must be unique")
        if not self.tasks:
            raise ValueError("at least one task required")
        for task in self.tasks:
            if task.audio and task.audio.source == "minimax_tts" and not self.speech:
                raise ValueError("minimax_tts requires speech configuration")
            if any(a.kind == "response_speech" for a in task.assertions) and (
                not self.speech or not self.speech.transcribe_responses or not task.audio
            ):
                raise ValueError("response_speech requires audio and enabled speech transcription")
        return self


def load_config(path: Path) -> Config:
    config = Config.model_validate(yaml.safe_load(path.read_text()))
    from .policy import Policy

    Policy(config.app).check_url(config.app.base_url)
    for task in config.tasks:
        Policy(config.app).check_url(task.start_path)
        if config.app.adapter == "duckking":
            from urllib.parse import parse_qs, urlsplit

            if parse_qs(urlsplit(task.start_path).query).get("room") or parse_qs(
                urlsplit(config.app.base_url).query
            ).get("room"):
                raise ValueError("DuckKing start URLs must not select a pre-existing room")
        if task.audio and task.audio.source == "file":
            task.audio.path = str((path.parent / task.audio.path).resolve())
            if not Path(task.audio.path).is_file():
                raise ValueError(f"audio fixture missing for task {task.id}")
    config.output_dir = str((path.parent / config.output_dir).resolve())
    if config.app.source_repo:
        source = Path(config.app.source_repo).expanduser()
        config.app.source_repo = str((path.parent / source).resolve())
        if not Path(config.app.source_repo).is_dir():
            raise ValueError(
                "source_repo must be a local directory; use ai-ux review --repo for a remote URL"
            )
    return config


class Action(StrictModel):
    kind: Literal["click", "fill", "select", "press", "scroll", "wait", "speak", "navigate", "reload", "done"]
    target: str | None = None
    text: str | None = Field(default=None, max_length=2000)
    key: str | None = None
    url: str | None = None
    seconds: float = Field(default=1, ge=0, le=10)
    direction: Literal["up", "down"] = "down"
    pixels: int = Field(default=550, ge=100, le=1200)
    rationale: str = Field(max_length=500)

    @model_validator(mode="after")
    def arguments(self):
        if self.kind in {"click", "fill", "select", "speak"} and not self.target:
            raise ValueError(f"{self.kind} requires observed target id")
        if self.kind in {"fill", "select"} and self.text is None:
            raise ValueError(f"{self.kind} requires text")
        if self.kind == "navigate" and not self.url:
            raise ValueError("navigate requires observed URL")
        if self.kind == "press" and self.key not in {
            "Tab",
            "Shift+Tab",
            "Enter",
            "Space",
            "Escape",
            "ArrowDown",
            "ArrowUp",
            "ArrowLeft",
            "ArrowRight",
        }:
            raise ValueError("unsupported key")
        return self


class Finding(StrictModel):
    category: Literal[
        "task_completion",
        "discoverability",
        "efficiency",
        "feedback",
        "error_recovery",
        "consistency",
        "performance",
        "accessibility",
        "visual_observability",
    ]
    severity: Literal["P0", "P1", "P2", "P3"]
    title: str = Field(min_length=4, max_length=150)
    observations: list[str] = Field(min_length=1, max_length=8)
    inference: str
    recommendation: str
    reproduction_steps: list[str] = Field(min_length=1, max_length=10)
    evidence_refs: list[str] = Field(min_length=1, max_length=15)
    confidence: Literal["high", "medium", "low"]
    acceptance: list[str] = Field(min_length=1, max_length=8)
    video_timestamps: list[str] = Field(default_factory=list, max_length=10)
    status: Literal["candidate", "needs_review"] = "needs_review"


class Judgement(StrictModel):
    summary: str
    task_assessment: Literal["passed", "partial", "failed", "inconclusive"]
    strengths: list[str] = Field(default_factory=list, max_length=10)
    issues: list[Finding] = Field(default_factory=list, max_length=12)
    limitations: list[str] = Field(default_factory=list, max_length=12)
    video_observations: list[str] = Field(default_factory=list, max_length=12)
