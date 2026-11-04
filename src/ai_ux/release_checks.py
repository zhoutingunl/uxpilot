"""Read-only checks before stopping a workbench for deployment."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path


def ensure_idle(base: Path):
    for path in (base / "state/jobs").glob("*.json"):
        if json.loads(path.read_text()).get("status") in {"queued", "planning", "evaluating"}:
            raise RuntimeError("Legacy evaluations must finish before deployment")
    database = base / "workbench/workbench.sqlite3"
    if database.is_file():
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            count = db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
        if count:
            raise RuntimeError("Workbench evaluations must finish before deployment")
