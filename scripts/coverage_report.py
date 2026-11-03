"""Create a compact, portable coverage snapshot from coverage.py JSON and pytest JUnit."""

import argparse
import hashlib
import json
import platform
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


def fingerprint(root):
    digest = hashlib.sha256()
    for path in sorted((root / "src").rglob("*.py")):
        digest.update(str(path.relative_to(root)).encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("artifacts/coverage/coverage.json"))
    parser.add_argument("--junit", type=Path, default=Path("artifacts/coverage/junit.xml"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    raw = json.loads(args.input.read_text())
    junit = ET.parse(args.junit).getroot()
    suites = [junit] if junit.tag == "testsuite" else list(junit.findall("testsuite"))
    counts = {k: sum(int(s.get(k, 0)) for s in suites) for k in ["tests", "errors", "failures", "skipped"]}
    files = {
        name: {
            **data["summary"],
            "missing_lines": data["missing_lines"],
            "missing_branches": data.get("missing_branches", []),
        }
        for name, data in raw["files"].items()
    }
    snapshot = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(),
        "platform": platform.system(),
        "coverage_version": raw["meta"]["version"],
        "source_sha256": fingerprint(root),
        "tests": counts,
        "totals": raw["totals"],
        "files": files,
    }
    dest = root / "docs/coverage"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "summary.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n")
    t = raw["totals"]

    def pct(covered, total):
        return 100 * covered / total if total else 100

    lines = [
        "# Test coverage report",
        "",
        f"Generated: {snapshot['generated_at']}. Python {snapshot['python']} on {snapshot['platform']}; coverage.py {snapshot['coverage_version']}.",
        "",
        f"**{counts['tests']} tests; {counts['failures']} failures; {counts['errors']} errors; {counts['skipped']} skipped.**",
        "",
        f"- Statements: {t['covered_lines']} / {t['num_statements']} = {pct(t['covered_lines'], t['num_statements']):.2f}%",
        f"- Branches: {t['covered_branches']} / {t['num_branches']} = {pct(t['covered_branches'], t['num_branches']):.2f}%",
        f"- coverage.py combined statement/branch coverage: {t['percent_covered']:.2f}% (CI floor: 80%).",
        "",
        f"Source fingerprint (sorted Python path + content): `{snapshot['source_sha256']}`.",
        "",
        "| Module | Statements hit / total | Branches hit / total | Combined |",
        "|---|---:|---:|---:|",
    ]
    for name, data in files.items():
        lines.append(
            f"| {name} | {data['covered_lines']} / {data['num_statements']} | {data['covered_branches']} / {data['num_branches']} | {data['percent_covered']:.1f}% |"
        )
    lines += [
        "",
        "## Scope",
        "",
        "The denominator includes every Python module under `src/ai_ux`; no production modules are omitted. The suite includes unit tests, deterministic HTTP/model protocol doubles, generated-video tests, and real local Playwright browser integration. Paid APIs and public sites are not called by pytest.",
        "",
        "Coverage is not a correctness or UX-quality score. Live provider behavior, cloud voice, CAPTCHA/authentication, real mobile devices, production resource contention and many timing races remain outside this offline suite. Browser cleanup, voice-specific branches and operating-system/network failure cases are only partially exercised. See the machine-readable snapshot for missing lines and branches.",
        "",
        "[Portable JSON summary](coverage/summary.json). Full annotated HTML, raw JSON, XML and JUnit results are uploaded by the GitHub Actions `test-and-coverage` workflow for each run; the checked-in snapshot is a measured baseline, not a substitute for the current CI run.",
        "",
        "## Reproduce",
        "",
        "```bash",
        "uv run pytest -q --cov=ai_ux --cov-branch --cov-fail-under=80 \\\n  --cov-report=term-missing --cov-report=html:artifacts/coverage/html \\\n  --cov-report=json:artifacts/coverage/coverage.json \\\n  --cov-report=xml:artifacts/coverage/coverage.xml \\\n  --junitxml=artifacts/coverage/junit.xml",
        "uv run python scripts/coverage_report.py",
        "```",
        "",
    ]
    (root / "docs/coverage.md").write_text("\n".join(lines))
    print(
        json.dumps(
            {
                "tests": counts,
                "combined_percent": t["percent_covered"],
                "source_sha256": snapshot["source_sha256"],
            }
        )
    )


if __name__ == "__main__":
    main()
