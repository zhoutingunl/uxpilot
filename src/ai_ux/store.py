"""SQLite job queue and normalized audit facts. Durable across web restarts."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager, nullcontext
from pathlib import Path

from .analytics import event_error, usage_numbers
from .policy import scrub

TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


class Store:
    def __init__(self, directory: Path):
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "workbench.sqlite3"
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS jobs(
              id TEXT PRIMARY KEY, status TEXT NOT NULL, request TEXT NOT NULL, created_at REAL NOT NULL,
              started_at REAL, finished_at REAL, stage TEXT NOT NULL DEFAULT 'queued',
              report_path TEXT, error TEXT, parent_id TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS events(
              id INTEGER PRIMARY KEY, job_id TEXT NOT NULL, stream TEXT NOT NULL, seq INTEGER NOT NULL,
              at REAL NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
              UNIQUE(job_id,stream,seq));
            CREATE INDEX IF NOT EXISTS events_job ON events(job_id,id);
            CREATE TABLE IF NOT EXISTS calls(
              event_id INTEGER PRIMARY KEY, job_id TEXT NOT NULL, at REAL NOT NULL, provider TEXT, model TEXT,
              purpose TEXT, status TEXT, duration_ms REAL, prompt_tokens INTEGER, completion_tokens INTEGER,
              total_tokens INTEGER);
            CREATE TABLE IF NOT EXISTS errors(
              event_id INTEGER PRIMARY KEY, job_id TEXT NOT NULL, at REAL NOT NULL, cluster_id TEXT NOT NULL,
              tool TEXT NOT NULL, category TEXT NOT NULL, signature TEXT NOT NULL, message TEXT NOT NULL,
              stream TEXT, seq INTEGER);
            CREATE INDEX IF NOT EXISTS errors_cluster ON errors(cluster_id,at);
            CREATE TABLE IF NOT EXISTS triage(
              cluster_id TEXT PRIMARY KEY, state TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS deployments(
              id INTEGER PRIMARY KEY, at REAL NOT NULL, version TEXT NOT NULL, commit_sha TEXT NOT NULL,
              target TEXT NOT NULL, status TEXT NOT NULL, checks TEXT NOT NULL, coverage TEXT,
              UNIQUE(version,commit_sha,target,status));
            """)
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def create_job(self, request: dict, parent_id=None, max_queued=100):
        now = time.time()
        job_id = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT count(*) FROM jobs WHERE status='queued'").fetchone()[0] >= max_queued:
                raise ValueError("任务队列已满，请稍后再试")
            db.execute(
                "INSERT INTO jobs(id,status,request,created_at,parent_id) VALUES(?,?,?,?,?)",
                (job_id, "queued", json.dumps(scrub(request), ensure_ascii=False), now, parent_id),
            )
        return self.job(job_id) or {
            "id": job_id,
            "status": "queued",
            "request": scrub(request),
            "created_at": now,
            "stage": "queued",
        }

    @staticmethod
    def job_row(row):
        if row is None:
            return None
        value = dict(row)
        value["request"] = json.loads(value["request"])
        return value

    def job(self, job_id):
        with self.connect() as db:
            return self.job_row(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def jobs(self, limit=100):
        with self.connect() as db:
            return [
                self.job_row(r)
                for r in db.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))
            ]

    def claim(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM jobs WHERE status='queued' ORDER BY created_at,id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            db.execute(
                "UPDATE jobs SET status='running',stage='preparing',started_at=? WHERE id=?",
                (time.time(), row["id"]),
            )
            return self.job_row(db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone())

    def stage(self, job_id, stage):
        with self.connect() as db:
            db.execute("UPDATE jobs SET stage=? WHERE id=? AND status='running'", (stage, job_id))

    def finish(self, job_id, status, error=None, report_path=None):
        if status not in TERMINAL:
            raise ValueError("invalid terminal job status")
        with self.connect() as db:
            row = db.execute("SELECT cancel_requested,status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row or row["status"] in TERMINAL:
                return
            if row["cancel_requested"] and status == "completed":
                status = "cancelled"
            db.execute(
                "UPDATE jobs SET status=?,stage=?,finished_at=?,error=?,report_path=? WHERE id=?",
                (status, status, time.time(), scrub(error), report_path, job_id),
            )

    def cancel(self, job_id):
        with self.connect() as db:
            db.execute(
                "UPDATE jobs SET cancel_requested=1 WHERE id=? AND status NOT IN ('completed','failed','cancelled','interrupted')",
                (job_id,),
            )
            db.execute(
                "UPDATE jobs SET status='cancelled',stage='cancelled',finished_at=? WHERE id=? AND status='queued'",
                (time.time(), job_id),
            )
        return self.job(job_id)

    def recover(self):
        with self.connect() as db:
            count = db.execute("SELECT count(*) FROM jobs WHERE status='running'").fetchone()[0]
            db.execute(
                "UPDATE jobs SET status='interrupted',stage='interrupted',finished_at=?,error='服务进程中断；已保留现场，可重新提交' WHERE status='running'",
                (time.time(),),
            )
        return count

    def append_event(self, job_id, stream, seq, event, at=None, *, connection=None):
        event = scrub(event)
        at = time.time() if at is None else at
        with self.connect() if connection is None else nullcontext(connection) as db:
            cur = db.execute(
                "INSERT OR IGNORE INTO events(job_id,stream,seq,at,kind,payload) VALUES(?,?,?,?,?,?)",
                (
                    job_id,
                    stream,
                    seq,
                    at,
                    event.get("kind", "unknown"),
                    json.dumps(event, ensure_ascii=False),
                ),
            )
            if not cur.rowcount:
                return False
            event_id = cur.lastrowid
            if event.get("kind") == "model_call":
                usage = usage_numbers(event.get("usage"))
                db.execute(
                    "INSERT INTO calls VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        event_id,
                        job_id,
                        at,
                        event.get("provider", "unknown"),
                        event.get("model", "unknown"),
                        event.get("purpose", "unknown"),
                        event.get("status", "unknown"),
                        event.get("duration_ms"),
                        usage["prompt_tokens"],
                        usage["completion_tokens"],
                        usage["total_tokens"],
                    ),
                )
            error = event_error(event)
            if error:
                db.execute(
                    "INSERT INTO errors VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        event_id,
                        job_id,
                        at,
                        error["cluster_id"],
                        error["tool"],
                        error["category"],
                        error["signature"],
                        error["message"],
                        stream,
                        seq,
                    ),
                )
        return True

    def event(self, event_id):
        with self.connect() as db:
            r = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
            if r is None:
                return None
            value = dict(r)
            value["payload"] = json.loads(value["payload"])
            return value

    def events(self, job_id, after=0, limit=1000):
        with self.connect() as db:
            result = []
            for row in db.execute(
                "SELECT * FROM events WHERE job_id=? AND id>? ORDER BY id LIMIT ?", (job_id, after, limit)
            ):
                value = dict(row)
                value["payload"] = json.loads(value["payload"])
                result.append(value)
            return result

    def first_activity_at(self):
        """Oldest recorded submission or model call, independent of the query window."""
        with self.connect() as db:
            return db.execute(
                "SELECT min(at) FROM (SELECT min(created_at) AS at FROM jobs UNION ALL SELECT min(at) AS at FROM calls)"
            ).fetchone()[0]

    def analytics_rows(self, start_ts, end_ts):
        with self.connect() as db:
            jobs = [
                self.job_row(r)
                for r in db.execute(
                    "SELECT * FROM jobs WHERE created_at>=? AND created_at<?", (start_ts, end_ts)
                )
            ]
            calls = [
                dict(r) for r in db.execute("SELECT * FROM calls WHERE at>=? AND at<?", (start_ts, end_ts))
            ]
        return jobs, calls

    def clusters(self, start_ts, end_ts, state=None):
        with self.connect() as db:
            rows = db.execute(
                """SELECT e.cluster_id,e.tool,e.category,e.signature,count(*) count,
                   count(DISTINCT e.job_id) jobs,min(e.at) first_at,max(e.at) last_at,
                   COALESCE(t.state,'open') state,COALESCE(t.note,'') note
                   FROM errors e LEFT JOIN triage t ON t.cluster_id=e.cluster_id
                   WHERE e.at>=? AND e.at<? GROUP BY e.cluster_id
                   ORDER BY count DESC,last_at DESC""",
                (start_ts, end_ts),
            ).fetchall()
            result = []
            for row in rows:
                r = dict(row)
                if state and r["state"] != state:
                    continue
                r["candidate"] = r["jobs"] >= 2 and r["category"] in {
                    "schema",
                    "not_found",
                    "invalid_input",
                    "other",
                }
                r["samples"] = [
                    dict(s)
                    for s in db.execute(
                        "SELECT event_id,job_id,at,message,stream,seq FROM errors WHERE cluster_id=? AND at>=? AND at<? ORDER BY at DESC LIMIT 5",
                        (r["cluster_id"], start_ts, end_ts),
                    )
                ]
                result.append(r)
            return result

    def triage(self, ids, state, note=""):
        if state not in {"open", "investigating", "fixed", "ignored"}:
            raise ValueError("invalid triage state")
        if not 1 <= len(ids) <= 100:
            raise ValueError("select 1..100 clusters")
        with self.connect() as db:
            for key in ids:
                if not db.execute("SELECT 1 FROM errors WHERE cluster_id=?", (key,)).fetchone():
                    raise ValueError("unknown cluster")
            for key in ids:
                db.execute(
                    "INSERT INTO triage VALUES(?,?,?,?) ON CONFLICT(cluster_id) DO UPDATE SET state=excluded.state,note=excluded.note,updated_at=excluded.updated_at",
                    (key, state, scrub(note[:1000]), time.time()),
                )

    def record_deployment(self, record):
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO deployments(at,version,commit_sha,target,status,checks,coverage) VALUES(?,?,?,?,?,?,?)",
                (
                    record["at"],
                    record["version"],
                    record["commit"],
                    record["target"],
                    record["status"],
                    json.dumps(record["checks"], ensure_ascii=False),
                    json.dumps(record.get("coverage")),
                ),
            )

    def deployments(self):
        with self.connect() as db:
            return [
                {
                    **dict(r),
                    "checks": json.loads(r["checks"]),
                    "coverage": json.loads(r["coverage"]) if r["coverage"] else None,
                }
                for r in db.execute("SELECT * FROM deployments ORDER BY id DESC LIMIT 100")
            ]

    def delete_all(self):
        with self.connect() as db:
            for table in ["errors", "calls", "events", "triage", "deployments", "jobs"]:
                db.execute(f"DELETE FROM {table}")
