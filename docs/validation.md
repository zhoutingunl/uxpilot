# Validation record

Initial validation: 2026-11-03; multilingual/voice follow-up: 2026-11-07. This record distinguishes deterministic automated tests from paid-provider/public-site checks. No raw third-party browser recordings, private credentials or operational logs are published.

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

### Multilingual and browser-playback follow-up (2026-11-07)

Target: `https://duck.qbox.net/`. Live batch `20261107T024755Z-ce7eff` used the DuckKing adapter and GLM-5.3-Flash for Explorer/Judge. All four tasks completed deterministic verification and native-video review; each isolated room's conditional cleanup returned HTTP 204.

| Task | Observed result |
|---|---|
| `language-en` | Switched from Chinese to English through the language menu; document language and visible Home/voice controls passed; English and the allocated room survived reload |
| `language-fr` | Switched to French; document language and visible Accueil/voice controls passed; French and the allocated room survived reload |
| `cloud-voice-en` | Synthetic microphone input was transcribed as “Please nod your head.”; `nod` completed; English reply text and 55 PCM frames (105,600 bytes, 16 kHz) arrived; browser playback completed without drops |
| `cloud-voice-fr` | Synthetic input was transcribed as “Fais oui de la tête, s'il te plaît.”; `nod` completed; French reply text and 49 PCM frames (94,080 bytes, 16 kHz) arrived; browser playback completed without drops |

Both voice tasks passed all eight configured checks: document language, requested voice-session language, current-turn user transcript, actual action result, assistant transcript, non-silent response PCM, non-aborted turn completion and browser-reported playback completion. The evaluator did not send audio to GLM or MiniMax. The returned audio's language/content was not independently checked by ASR or human listening; the English/French reply assessment uses product-provided text. Tests cover one fixed synthetic command per language, not a speech-accuracy benchmark or every localized page. Judge findings, including Chinese diagnostic phrases in English/French logs, remain review candidates.

Chinese follow-up batch `20261107T025157Z-9384ca` passed local-first recognition, `nod`, local completion feedback and independent video review. Its first cloud task failed verification: Explorer enabled local-first, repeatedly attempted a disabled mode selector and eventually used local recognition. Completed local motions were **not** accepted as cloud coverage. That failed run is retained. After adding immediate rejection of observed disabled controls and guidance to leave a session before changing locked settings, cloud retry batch `20261107T025559Z-556b16` passed all nine deterministic checks: Chinese cloud transcription, both `nod` and `walk`, cloud answer text, non-silent PCM and completed browser playback (94 frames, 180,480 bytes, 16 kHz), plus model visibility, session language and turn completion.

The inspected local DuckKing revision was `a083daa4711ec17be8caa289823a8b769f0250e0`; the live service reported deployed revision `66d0b02bdbd36f341ac77959ccbd96a57ea6d6be`. These revisions differ, so source-localization candidates do not establish the deployed root cause. No DuckKing code or deployment was changed by these tests.

Local regression coverage includes language-independent room startup in real Chromium, rejection of malformed/sent/silent response audio, exclusion of previous-turn greetings, suspended/incomplete/dropped playback, complete fixture timing windows, and authenticated same-origin room cleanup without persisting the visitor cookie.

### MiniMax M Plan speech capability checks (2026-11-07)

After updating the local subscription credential, the M Plan quota query and text baseline returned success. Probe batch `20261107T031521Z` called the documented `https://api.minimax.cn` endpoints with the updated subscription Key:

- `speech-2.8-hd`, `/v1/t2a_v2`: generated Mandarin, English and French WAVs using the same system voice (`male-qn-qingse`). Each was a valid mono 32 kHz file of approximately four seconds.
- `asr-1.0`, `/v1/speech_to_text`: transcribed all three generated files without receiving their reference text. English and French matched after punctuation/case normalization. The Mandarin synthesis input began with “你好”; ASR returned “您好”, while both action commands were preserved. This small sample demonstrates usable calls, not a language-wide accuracy or naturalness benchmark.
- `MiniMax-M3.1-Flash`, `/v1/chat/completions`: a six-digit random spoken challenge was supplied as pure audio and as a four-second video with an audio track and a solid blue image. Both requests returned HTTP 200 but no recognized digits; final answers said the audio was inaccessible. A silent-video control also returned no digits. The same audio was correctly recognized by `asr-1.0`. A separate visual question returned “Blue”, confirming video-image input worked. The configured `api.minimaxi.com` chat endpoint reproduced the pure-audio/video-audio results and passed the visual control.

The model API accepting a request envelope is not evidence that it processed its audio. These checks establish the observed behavior of the tested endpoints and payloads. MiniMax's separate speech services provide the ASR/TTS capabilities; the evaluator's chat model integration still does not perform native speech recognition.

The same ASR service additionally transcribed the archived DuckKing Mandarin, English and French response PCM after conversion to WAV. Core reply content matched the product transcripts in all three samples. Literal normalized equality did not pass: Chinese “完成啦” became “完成了”; the duck's “嘎” was rendered as “kaka” in English and “gars” in French. These are retained as transcription differences, not automatically classified as product defects. This follow-up is separate from the original runs; their historical `audio.json` was not rewritten to imply ASR had run during evaluation. Automatic response-ASR integration remains future work.

Official references: [M Plan](https://www.minimax.cn/m-plan), [Speech 2.8 synthesis API](https://platform.minimax.cn/docs/api-reference/speech-t2a-http), [ASR model, languages and M Plan access](https://platform.minimax.cn/docs/guides/speech-to-text), [M3.1 chat input modalities](https://platform.minimax.cn/docs/api-reference/text-openai-api). The advertised Speech 2.8 webpage slug is not an API model ID: the documented IDs are `speech-2.8-hd` and `speech-2.8-turbo`; recognition uses `asr-1.0`.

## Integrated multilingual regression (2026-11-07)

The implementation now includes opt-in MiniMax TTS input generation and independent response ASR in `run_task`, together with the DuckKing-only `check-locales` command. Speech calls are recorded as actual model calls; provider usage that is not token-based remains distinct from token counts.

Live interface batch `locales-20261107T034425Z-99728f` passed **140 cases / 878 checks** across all ten pages, desktop 1440×1050 and mobile 390×844. Each page covered Chinese, English and French menu selection, reload persistence, cross-page language/room persistence, key navigation copy, selected-language state, language-button visibility, horizontal overflow, and cloud-mode restrictions where the voice panel exists. In-place switches preserved document identity and the canvas node where present. Screenshots and recordings were retained. This verifies key UI behavior, not every translation, camera pose or every 3D scene.

An earlier matrix retained failures caused by checking the default voice-mode markup before its capability request finished. The evaluator now waits for the disconnected mode control to become ready. The same delayed-initialization behavior is covered by a real-browser local regression fixture; the full corrected matrix was rerun rather than relabeling the old result.

Live speech batch `20261107T033815Z-7978ba` passed all configured checks and independent video review for `cloud-voice`, `cloud-voice-en` and `cloud-voice-fr`. Each used a freshly generated `speech-2.8-hd` microphone WAV and independently transcribed the actual Lingxi response through `asr-1.0`. Chinese completed both nod and walk; English and French completed nod. All response keyword checks, turn completion and browser playback checks passed. Reports retain the actual WAVs, ASR text and service-call records. ASR punctuation/particles are not treated as exact semantic equivalence or proof of physical speaker audibility.

Both the inspected DuckKing source snapshot and the service's reported live revision were `a083daa4711ec17be8caa289823a8b769f0250e0`. Judge candidates included Chinese diagnostic text in English/French logs and overlapping loading/connection messages; these remain review candidates, with source quotes checked against that snapshot.

The merged implementation passed 141 offline tests, Ruff checks/format validation and wheel/sdist builds. Measured combined statement/branch coverage was 82.54%, above the repository's 80% CI threshold. Tests use local browsers and HTTP doubles rather than paid providers or the public demo.

## Reproducible automated suite

See [coverage.md](coverage.md) for the exact offline suite result, source fingerprint, branch/statement coverage and limits. The suite uses local browser fixtures, protocol doubles and generated media; it needs no production accounts or API keys. CI reproduces the suite and uploads detailed coverage artifacts.
