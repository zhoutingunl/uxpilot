# Test coverage report

Generated: 2026-11-03T16:53:07.686558+00:00. Python 3.12.8 on Darwin; coverage.py 7.17.0.

**74 tests; 0 failures; 0 errors; 0 skipped.**

- Statements: 1973 / 2350 = 83.96%
- Branches: 482 / 692 = 69.65%
- coverage.py combined statement/branch coverage: 80.70% (CI floor: 80%).

Source fingerprint (sorted Python path + content): `e46daa1150fcd432ac977d053acb9e90c3f1c457207d7fe66b7f500e529ac412`.

| Module | Statements hit / total | Branches hit / total | Combined |
|---|---:|---:|---:|
| src/ai_ux/__init__.py | 1 / 1 | 0 / 0 | 100.0% |
| src/ai_ux/agent.py | 222 / 261 | 49 / 62 | 83.9% |
| src/ai_ux/analytics.py | 86 / 91 | 24 / 30 | 90.9% |
| src/ai_ux/browser.py | 233 / 320 | 88 / 130 | 71.3% |
| src/ai_ux/cli.py | 79 / 80 | 17 / 18 | 98.0% |
| src/ai_ux/defaults.py | 2 / 3 | 0 / 0 | 66.7% |
| src/ai_ux/deployments.py | 47 / 69 | 8 / 22 | 60.4% |
| src/ai_ux/discovery.py | 78 / 79 | 5 / 6 | 97.6% |
| src/ai_ux/evidence.py | 57 / 60 | 4 / 6 | 92.4% |
| src/ai_ux/jobs.py | 78 / 134 | 14 / 32 | 55.4% |
| src/ai_ux/llm.py | 136 / 153 | 41 / 54 | 85.5% |
| src/ai_ux/media.py | 73 / 75 | 29 / 32 | 95.3% |
| src/ai_ux/policy.py | 64 / 66 | 28 / 30 | 95.8% |
| src/ai_ux/probe.py | 41 / 42 | 5 / 6 | 95.8% |
| src/ai_ux/replay.py | 71 / 78 | 21 / 24 | 90.2% |
| src/ai_ux/report.py | 75 / 93 | 30 / 46 | 75.5% |
| src/ai_ux/repository.py | 147 / 175 | 48 / 70 | 79.6% |
| src/ai_ux/schema.py | 158 / 176 | 21 / 38 | 83.6% |
| src/ai_ux/source_map.py | 47 / 55 | 11 / 14 | 84.1% |
| src/ai_ux/store.py | 130 / 151 | 25 / 38 | 82.0% |
| src/ai_ux/verifier.py | 29 / 31 | 11 / 14 | 88.9% |
| src/ai_ux/webapp.py | 119 / 157 | 3 / 20 | 68.9% |

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
