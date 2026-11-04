from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from ai_ux.analytics import statistics, statistics_period
from ai_ux.store import Store

ZONE = ZoneInfo("Asia/Shanghai")


def test_daily_starts_at_first_record_and_lists_latest_date_first():
    created = datetime(2026, 11, 2, 10, tzinfo=ZONE).timestamp()
    jobs = [
        {"created_at": created, "status": "completed", "started_at": created, "finished_at": created + 10}
    ]
    data = statistics(jobs, [], date(2026, 10, 6), date(2026, 11, 4))
    assert data["first_recorded_date"] == data["start"] == "2026-11-02"
    assert data["window_start"] == "2026-10-06"
    assert [row["date"] for row in data["daily"]] == ["2026-11-04", "2026-11-03", "2026-11-02"]
    assert [row["submitted"] for row in data["daily"]] == [0, 0, 1]


def test_empty_history_has_no_synthetic_zero_days():
    data = statistics([], [], date(2026, 10, 6), date(2026, 11, 4))
    assert data["daily"] == [] and data["start"] is None
    assert data["first_recorded_date"] is None and data["submitted"] == 0


def test_project_older_than_window_preserves_zero_activity_days_in_window():
    first = datetime(2026, 10, 1, tzinfo=ZONE).timestamp()
    data = statistics([], [], date(2026, 11, 2), date(2026, 11, 4), first_activity=first)
    assert data["first_recorded_date"] == "2026-10-01"
    assert data["start"] == "2026-11-02"
    assert [r["date"] for r in data["daily"]] == ["2026-11-04", "2026-11-03", "2026-11-02"]


def test_window_includes_today_and_uses_shanghai_calendar_boundaries():
    now = datetime(2026, 11, 3, 18, 30, tzinfo=timezone.utc)  # November 4 in Shanghai.
    start, end = statistics_period(1, now)
    assert start == datetime(2026, 11, 4, 0, tzinfo=ZONE)
    assert end.date() == date(2026, 11, 4)
    start, end = statistics_period(30, now)
    assert start.date() == date(2026, 10, 6)
    assert (end.date() - start.date()).days + 1 == 30


def test_first_activity_is_global_and_includes_model_only_history(tmp_path):
    store = Store(tmp_path / "db")
    assert store.first_activity_at() is None
    job = store.create_job({"repo": "demo", "url": "https://example.com/"})
    created = job["created_at"]
    assert store.first_activity_at() == created
    store.append_event(job["id"], "demo", 0, {"kind": "model_call", "status": "error"}, at=created - 3600)
    assert store.first_activity_at() == created - 3600
    jobs, calls = store.analytics_rows(created + 1, created + 2)
    assert jobs == calls == []
    data = statistics(
        jobs,
        calls,
        datetime.fromtimestamp(created, ZONE).date(),
        datetime.fromtimestamp(created, ZONE).date(),
        first_activity=store.first_activity_at(),
    )
    assert len(data["daily"]) == 1


def test_stats_api_keeps_after_midnight_local_submissions(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from ai_ux import webapp

    monkeypatch.setenv("UXPILOT_DATA_DIR", str(tmp_path / "db"))
    monkeypatch.setenv("UXPILOT_WORKERS", "0")
    now = datetime(2026, 11, 3, 18, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(webapp, "statistics_period", lambda days: statistics_period(days, now))
    with TestClient(webapp.create_app(webapp.Settings())) as client:
        store = client.app.state.store
        job = store.create_job({"repo": "demo", "url": "https://example.com/"})
        created = datetime(2026, 11, 4, 0, 30, tzinfo=ZONE).timestamp()
        with store.connect() as db:
            db.execute("UPDATE jobs SET created_at=? WHERE id=?", (created, job["id"]))
        response = client.get("/api/stats?days=1")
        assert response.status_code == 200
        data = response.json()
        assert data["submitted"] == 1
        assert data["window_start"] == data["start"] == data["end"] == "2026-11-04"
        assert [row["date"] for row in data["daily"]] == ["2026-11-04"]
