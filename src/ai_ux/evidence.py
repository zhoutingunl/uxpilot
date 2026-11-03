from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from .policy import scrub


class Evidence:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=False)
        (root / "screenshots").mkdir()
        self.started = time.monotonic()
        self.wall_started = datetime.now(timezone.utc).isoformat()
        self.events: list[dict] = []
        self.observations: list[dict] = []
        self.max_events = 8000
        self.dropped_events = 0

    @classmethod
    def reopen(cls, root: Path):
        """Read an existing run for a new Judge attempt, preserving its browser evidence."""
        self = cls.__new__(cls)
        self.root = root.resolve()
        prior = json.loads((root / "run.json").read_text())
        self.started = time.monotonic() - prior["duration_ms"] / 1000
        self.wall_started = prior["started_at"]
        self.events = [json.loads(line) for line in (root / "events.jsonl").read_text().splitlines()]
        self.observations = [
            json.loads(p.read_text()) for p in sorted((root / "observations").glob("*.json"))
        ]
        self.max_events = 8000
        self.dropped_events = 0
        return self

    def ms(self):
        return round((time.monotonic() - self.started) * 1000)

    def event(self, kind: str, **fields):
        event = scrub({"t_ms": self.ms(), "kind": kind, **fields})
        if len(self.events) >= self.max_events:
            self.dropped_events += 1
            return
        self.events.append(event)
        with (self.root / "events.jsonl").open("a") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def write(self, path: str, data):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(scrub(data), ensure_ascii=False, indent=2) + "\n")

    def resolve(self, reference: str):
        target = (self.root / reference).resolve()
        if not target.is_relative_to(self.root.resolve()) or not target.is_file():
            raise ValueError("invalid or missing evidence reference")
        return target

    def manifest(self):
        paths = [p for p in sorted(self.root.rglob("*")) if p.is_file() and p.name != "manifest.json"]
        result = {
            "started_at": self.wall_started,
            "duration_ms": self.ms(),
            "events_dropped": self.dropped_events,
            "files": [
                {
                    "path": str(p.relative_to(self.root)),
                    "bytes": p.stat().st_size,
                    "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                }
                for p in paths
            ],
        }
        self.write("manifest.json", result)
        return result
