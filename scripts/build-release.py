#!/usr/bin/env python3
"""Package a verifiable workspace snapshot; never includes keys or run artifacts."""

import argparse
import datetime
import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--reference", type=Path, default=Path("../DuckKing"))
parser.add_argument("--reference-ref", default="HEAD")
parser.add_argument(
    "--change",
    action="append",
    required=True,
    help="Incremental change since the last successful release; repeat for multiple changes",
)
parser.add_argument("--output", type=Path, default=Path("dist"))
args = parser.parse_args()
root = Path(__file__).resolve().parent.parent
version = datetime.datetime.now(datetime.timezone.utc).strftime("deploy-jfcs62-%Y%m%dT%H%M%SZ")
args.output.mkdir(parents=True, exist_ok=True)
archive = args.output.resolve() / (version + ".tar.gz")
files = []
for folder in ["src", "configs", "fixtures", "tests", "prompts", "deploy"]:
    files += [
        p
        for p in (root / folder).rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.name != ".DS_Store"
    ]
files += [
    root / n
    for n in [
        "pyproject.toml",
        "uv.lock",
        "README.md",
        ".env.example",
        "scripts/build-release.py",
        "BASELINE.json",
        "LICENSE",
        "THIRD_PARTY.md",
    ]
]
sha = subprocess.check_output(
    ["git", "-C", str(args.reference), "rev-parse", args.reference_ref], text=True
).strip()
paths = [
    "src",
    "frontend/src",
    "frontend/index.html",
    "frontend/scenarios.html",
    "frontend/deploys.html",
    "README.md",
    "pyproject.toml",
    "LICENSE",
]
source = subprocess.check_output(["git", "-C", str(args.reference), "archive", sha, *paths])
manifest = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}
digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
metadata = {
    "version": version,
    "source_kind": "local_workspace_snapshot",
    "snapshot_sha256": digest,
    "reference_commit": sha,
    "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "target": "jfcs62",
    "operator": "zt",
    "files": manifest,
    "changes": args.change,
    "baseline": {
        k: v
        for k, v in json.loads((root / "BASELINE.json").read_text()).items()
        if k in ["repository", "commit", "restored_at"]
    },
}
with tarfile.open(archive, "w:gz") as tar:
    for p in sorted(files):
        tar.add(p, arcname=str(p.relative_to(root)), recursive=False)
    with tarfile.open(fileobj=io.BytesIO(source)) as source_tar:
        for member in source_tar:
            if member.isfile():
                data = source_tar.extractfile(member).read()
                item = tarfile.TarInfo("source/DuckKing/" + member.name)
                item.size = len(data)
                item.mode = 0o644
                tar.addfile(item, io.BytesIO(data))
    for name, data in [
        ("release.json", metadata),
        (
            "source/DuckKing/SOURCE_SNAPSHOT.json",
            {
                "commit": sha,
                "scope": paths,
                "note": "Tracked source exported for review, not a runnable DuckKing deployment.",
            },
        ),
    ]:
        raw = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode()
        item = tarfile.TarInfo(name)
        item.size = len(raw)
        item.mode = 0o644
        tar.addfile(item, io.BytesIO(raw))
info = {
    "version": version,
    "archive": str(archive),
    "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
    "snapshot_sha256": digest,
    "bytes": archive.stat().st_size,
    "reference_commit": sha,
}
(archive.with_suffix(".json")).write_text(json.dumps(info, indent=2) + "\n")
print(json.dumps(info, indent=2))
