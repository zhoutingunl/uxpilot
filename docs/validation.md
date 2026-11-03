# Validation record

Date: 2026-11-03. This record distinguishes deterministic automated tests from paid-provider/public-site checks. Live runs were performed before the initial public release; no raw third-party browser recordings, private credentials or operational logs are published.

## Generic repository and URL workflow

Input repository: `https://github.com/markedjs/marked.git`, inspected revision `ba50aa95636ac8f97f46446bbfeb04592ed22216`. Demo: `https://marked.js.org/demo/`. The remote checkout completed, and the same checkout was reused read-only for a subsequent corrected run. Deployed revision was not independently established.

With no product-specific task selectors or adapter, the planner generated two tasks that the Explorer then executed:

| Task | Actual behavior | Verification/report result |
|---|---|---|
| Live Markdown preview | Waited for loading, found the input, entered headings/bold/list/quote, inspected preview; additional keyboard input updated the preview | Page health passed; business `task_outcome: not_verified` because no deterministic business assertion was configured; Judge completed |
| Switch output views | Selected HTML Source, Lexer Data, Quick Reference and inspected each | Page health passed; business `not_verified`; Judge completed |

Post-review source localization produced candidate references such as `docs/demo/index.html:90` and `docs/demo/demo.js:433`. File, line range and literal quote were validated. The causal explanations remain hypotheses: in particular, programmatic fill versus physical keyboard behavior requires separate reproduction before calling it a product defect.

An earlier run blocked CDN resources and produced a page-loading error. That run was retained as an evaluator limitation, not a confirmed product defect. Generic resource handling was corrected to allow public read-only subresources while retaining navigation restrictions, and the tasks above were rerun.

## Provider and native-video checks

- GLM-5.3 Flash directly read a generated red-to-blue video and correctly returned two random four-character codes. Codes were not supplied in prompt text or separate images.
- MiniMax-M3.1 Flash passed the same native-video probe via the non-streaming OpenAI-compatible endpoint.
- An isolated primary-credential-missing fault injection successfully fell back to real MiniMax, which read the video correctly.
- A full real page recording was read through continuous native-video chunks by MiniMax after forcing the primary unavailable, followed by a schema-valid final judgement. The final short chunk was explicitly padded and documented.
- Earlier 529 overloads, 400 video-parameter errors, GLM long-reasoning timeouts and schema retries were retained. Final protocol choices reflect those observations; provider availability is not guaranteed.

## DuckKing adapter checks

Earlier live adapter validation covered autonomous button nodding, local-first synthetic voice, direct Lingxi voice with nod+walk, microphone retry, mobile visibility and physical-capacity exhaustion. Two final desktop/local-voice tasks completed browser exploration, deterministic checks, native-video review and conditional cleanup (204). A physically full demo was reported as blocked without taking another room. Mobile action controls and model visibility were assessed separately.

These checks establish usable paths, not full-site coverage, real-user research, real-device iOS compatibility or physical speaker audibility. Synthetic microphone input used real ASR/cloud/tool execution. Fixed prompts cannot establish general speech accuracy.

## Reproducible automated suite

See [coverage.md](coverage.md) for the exact offline suite result, source fingerprint, branch/statement coverage and limits. The suite uses local browser fixtures, protocol doubles and generated media; it needs no production accounts or API keys. CI reproduces the suite and uploads detailed coverage artifacts.
