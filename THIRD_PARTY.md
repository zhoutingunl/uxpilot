# Third-party components

UX Pilot uses dependencies via package managers; their source is not vendored.

| Component | Use | Upstream license |
|---|---|---|
| Playwright | Browser automation and recording | Apache-2.0 |
| Pydantic | Configuration/output contracts | MIT |
| HTTPX | Provider requests | BSD-3-Clause |
| PyYAML | Project profiles | MIT |
| pytest / pytest-asyncio / pytest-cov / coverage.py / Ruff | Development and validation | See each installed distribution |
| ffmpeg / ffprobe | Local video processing | Build-dependent LGPL/GPL; executable not bundled |

The model protocol configuration was checked against the user's `ai_gateway` integration; no gateway operational code or credentials are included. DuckKing-specific adapter behavior targets [zhoutingunl/DuckKing](https://github.com/zhoutingunl/DuckKing), whose application source is not included.

The optional audio fixtures were synthesized locally with macOS Tingting for command-input tests. They contain no recorded human voice. They are sample input, not model/vendor responses. Replace them with fixtures whose terms fit your deployment if redistributing audio separately.

Live compatibility validation used [Marked](https://github.com/markedjs/marked) (MIT) and its public demo. No Marked source or live recording is distributed here. Provider/model access is governed by the applicable provider terms and is not granted by this project's license.
