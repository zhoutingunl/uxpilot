# Test coverage report

Generated: 2026-11-03T10:09:16.008410+00:00. Python 3.12.8 on Darwin; coverage.py 7.17.0.

**65 tests; 0 failures; 0 errors; 0 skipped.**

- Statements: 1415 / 1641 = 86.23%
- Branches: 384 / 522 = 73.56%
- coverage.py combined statement/branch coverage: 83.17% (CI floor: 80%).

Source fingerprint (sorted Python path + content): `57ee73d628fe580c41dbd544a0f09fabd95fd32f09e9439ce6fe98d830781960`.

| Module | Statements hit / total | Branches hit / total | Combined |
|---|---:|---:|---:|
| src/ai_ux/__init__.py | 1 / 1 | 0 / 0 | 100.0% |
| src/ai_ux/agent.py | 212 / 250 | 49 / 62 | 83.7% |
| src/ai_ux/browser.py | 232 / 319 | 88 / 130 | 71.3% |
| src/ai_ux/cli.py | 79 / 80 | 17 / 18 | 98.0% |
| src/ai_ux/defaults.py | 2 / 3 | 0 / 0 | 66.7% |
| src/ai_ux/discovery.py | 75 / 76 | 5 / 6 | 97.6% |
| src/ai_ux/evidence.py | 52 / 54 | 3 / 4 | 94.8% |
| src/ai_ux/llm.py | 134 / 151 | 41 / 54 | 85.4% |
| src/ai_ux/media.py | 73 / 75 | 29 / 32 | 95.3% |
| src/ai_ux/policy.py | 64 / 66 | 28 / 30 | 95.8% |
| src/ai_ux/probe.py | 41 / 42 | 5 / 6 | 95.8% |
| src/ai_ux/report.py | 69 / 87 | 28 / 44 | 74.0% |
| src/ai_ux/repository.py | 147 / 175 | 48 / 70 | 79.6% |
| src/ai_ux/schema.py | 158 / 176 | 21 / 38 | 83.6% |
| src/ai_ux/source_map.py | 47 / 55 | 11 / 14 | 84.1% |
| src/ai_ux/verifier.py | 29 / 31 | 11 / 14 | 88.9% |

## Scope

The denominator includes every Python module under `src/ai_ux`; no production modules are omitted. The suite includes unit tests, deterministic HTTP/model protocol doubles, generated-video tests, and real local Playwright browser integration. Paid APIs and public sites are not called by pytest.

Coverage is not a correctness or UX-quality score. Live provider behavior, cloud voice, CAPTCHA/authentication, real mobile devices, production resource contention and many timing races remain outside this offline suite. Browser cleanup, voice-specific branches and operating-system/network failure cases are only partially exercised. See the machine-readable snapshot for missing lines and branches.

[Portable JSON summary](coverage/summary.json). Full annotated HTML, raw JSON, XML and JUnit results are uploaded by the GitHub Actions `test-and-coverage` workflow for each run; the checked-in snapshot is a measured baseline, not a substitute for the current CI run.

## Reproduce

```bash
uv run pytest -q --cov=ai_ux --cov-branch --cov-fail-under=80 \
  --cov-report=term-missing --cov-report=html:artifacts/coverage/html \
  --cov-report=json:artifacts/coverage/coverage.json \
  --cov-report=xml:artifacts/coverage/coverage.xml \
  --junitxml=artifacts/coverage/junit.xml
uv run python scripts/coverage_report.py
```
