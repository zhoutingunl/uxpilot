# UX Pilot

Give UX Pilot a code repository and a live demo URL. It plans user-oriented tasks, operates the browser, records evidence, reviews native video with an independent LLM call, and suggests changes with verified source references.

```bash
uv sync --frozen
uv run playwright install chromium
uv run uxpilot review \
  --repo https://github.com/markedjs/marked.git \
  --url https://marked.js.org/demo/
```

Requires **Python 3.12+**, [uv](https://docs.astral.sh/uv/), Chrome (default), and `ffmpeg`/`ffprobe`. Install Chrome if it is not present (`uv run playwright install chrome`). Set `PLAYWRIGHT_CHROMIUM_EXECUTABLE` to a browser executable, or configure `browser.channel: null` to use Playwright's bundled Chromium. The `ai-ux` command remains an alias.

[中文说明](docs/README.zh-CN.md) · [Architecture](docs/architecture.md) · [Validation](docs/validation.md) · [Coverage](docs/coverage.md)

## Model configuration

Primary: **Zhipu `glm-5.3-Flash`**, streaming with `thinking: disabled`. Fallback: **MiniMax `MiniMax-M3.1-Flash`**, non-streaming, adaptive thinking and split reasoning. Both accept native video input; keys stay outside the repository.

| Provider | Environment variable | Default key file |
|---|---|---|
| Zhipu | `ZHIPU_API_KEY` | `~/.secrets/zhipu_api_key` |
| MiniMax | `MINIMAX_CN_API_KEY` | `~/.secrets/minimax_api_key` |

Run `uv run uxpilot doctor` to check credentials without printing them. API calls consume provider quota. `probe-video` submits a generated random-code video to both providers and checks their actual responses.

## Repository and demo review

```bash
# Local checkout, optional goal
uv run uxpilot review --repo /path/to/project --url https://demo.example.com/ \
  --goal 'Find an item and inspect its details'

# Generate an editable project profile first
uv run uxpilot review --repo https://github.com/org/project.git \
  --url https://demo.example.com/app/ --tasks 3 --plan-only

# Reuse the generated profile, add business assertions, repeat a task
uv run uxpilot --config artifacts/projects/<project-id>/project.yaml eval --repeats 3
```

- HTTPS and SSH Git URLs are cloned into the evaluation directory. Private repositories use existing non-interactive Git credentials. Local directories are read-only; no checkout, install, build, hooks, or submodule initialization is performed by the evaluator.
- Planning uses limited README/product documentation and an actual landing-page observation. Explorer receives the goal and current UI, **not source code or private assertions**. Judge runs in a fresh context. Source localization happens after UX judgement and checks path, line, and quote against the inspected source.
- Generic mode allows public HTTP(S) GET/HEAD subresources so normal CDN assets can load, while top-level navigation stays on the supplied host. Additional navigation hosts use `--allow-host`; known API/CDN hosts use `--resource-host`. Local/private literal IPs are blocked unless explicitly configured. This policy is not a network sandbox and does not implement DNS rebinding protection.
- Automatic checks cover readable pages, JavaScript errors, and horizontal overflow. They **do not prove business success**. Without configured task assertions the report says `task_outcome: not_verified`, and displays the independent Judge's assessment separately.
- Authentication, custom audio capture and domain-specific outcomes require configuration/adapters. Login barriers are reported, not bypassed. Use a test account/environment for workflows that modify persistent data.

## Reports and evidence

Each run writes an isolated directory under `artifacts/`. Outputs include:

- `report.html`, `report.md`, `report.json`: outcomes, evidence-based issues, reproduction steps, suggested changes, and regression acceptance criteria.
- `events.jsonl`, `observations/`, `screenshots/`: timestamped actions, visible UI, relevant errors, and browser evidence.
- `session.mp4`: original silent recording. `review.mp4`: compact video with model-deliberation-only waits removed. `video-timeline.json`: explicit mapping to the original clock.
- `verification.json`: deterministic checks independent of the Explorer's “done” response.
- `source-map.json`: verified source-reference candidates; not proof of root cause or deployed-version equivalence.
- `model-calls.json`, `manifest.json`: actual models, usage, failure/fallback outcomes, artifact hashes.

GLM reviews the compact MP4 directly. MiniMax's compatibility path reads continuous 4-second native-video segments and synthesizes their observations; final-segment padding is recorded. Original recordings are retained. Video is 2 fps and silent: it cannot establish speaker audibility or fast-motion accuracy.

Retry only judgement after a provider outage:

```bash
uv run uxpilot --config artifacts/projects/<project-id>/project.yaml \
  judge artifacts/projects/<project-id>/runs/<batch>/<run>
```

Old assessments are archived. A failed run is retained, never presented as an initial success. Exit codes: 0 = pipeline completed (product tasks may fail), 2 = configuration/runtime/Judge failure, 130 = interruption.

## DuckKing adapter

The original project-specific profile remains available:

```bash
uv run uxpilot --config configs/duckking.yaml eval --task first-nod --task local-voice
```

It adds independent room ownership, real ASR/cloud voice through synthetic WAV input, model-first-frame checks and actual action results. It targets a public demo and can consume Lingxi voice quota. Generic review does not call DuckKing endpoints or assume its selectors. The voice fixtures contain system-generated Mandarin, not human recordings.

## Tests

```bash
uv sync --frozen
uv run pytest --cov=ai_ux --cov-branch \
  --cov-report=term-missing --cov-report=html:artifacts/coverage/html \
  --cov-report=json:artifacts/coverage/coverage.json
uv run ruff check src tests
uv run ruff format --check src tests
uv build
```

Tests include deterministic protocol doubles and actual local browser integration. They do not call paid model services or the public demo. GitHub Actions publishes HTML/JSON/XML coverage artifacts. Live model/site validations are documented separately; no API keys, recordings from third-party pages or private operational logs are committed.

## Scope

This is a report-oriented MVP, not an unsupervised agent for arbitrary production systems. It does not open issues, modify application code, deploy, or merge. Candidate findings and severities need review; title-based deduplication is exact rather than semantic. Task time includes model reasoning, so it must not be used as product latency. Source checkout and live deployment may differ. Use explicit assertions and adapters for strict business acceptance.

MIT licensed. See [THIRD_PARTY.md](THIRD_PARTY.md) for dependencies and attribution.
