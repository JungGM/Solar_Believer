import gzip
from datetime import date

from scripts.collect_public_history import Download
from scripts.postgres_ingest import execute, ingest_forecast_grid


def test_received_data_is_atomically_visible_in_postgres_service_tables_and_idempotent(tmp_path):
    job = Download(tmp_path, "koen", "key", 0)
    row = dict(dgenYmd="20261122", ippt="NEW1", hogi="1", qhorGen01="1.5")
    job.save_records([row], "one", {"fetched_at_utc": "2026-11-22T01:00:00+00:00"}, date.min, date.max)
    assert execute(job.db.connection, "SELECT count(*) FROM public.observations").fetchone()[0] == 24
    job.save_records([row], "two", {"fetched_at_utc": "2026-11-22T02:00:00+00:00"}, date.min, date.max)
    assert execute(job.db.connection, "SELECT count(*) FROM public.observations").fetchone()[0] == 24
    assert (
        execute(job.db.connection, "SELECT min(collected_at) FROM public.observations").fetchone()[0].hour
        == 1
    )
    job.save_records(
        [dict(row, qhorGen01="2.5")],
        "three",
        {"fetched_at_utc": "2026-11-22T03:00:00+00:00"},
        date.min,
        date.max,
    )
    assert (
        execute(
            job.db.connection,
            "SELECT (\"values\"->>'generation_raw')::float FROM public.observations ORDER BY observed_at LIMIT 1",
        ).fetchone()[0]
        == 2.5
    )
    assert job.db.execute("SELECT count(*) FROM revisions").fetchone()[0] == 1
    assert not list(tmp_path.rglob("*.sqlite"))
    job.close()


def test_other_pc_can_resume_response_from_postgres_without_local_raw_file(tmp_path):
    job = Download(tmp_path, "koen", "secret-key", 0)

    class Response:
        status_code = 200
        content = b'{"response":{"header":{"resultCode":"00"},"body":{"items":[],"totalCount":0}}}'

    job.session.get = lambda *a, **k: Response()
    job.request({"page": 1})
    relative = job.db.execute("SELECT path FROM requests").fetchone()[0]
    (job.directory / relative).unlink()
    job.session.get = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not call API"))
    assert job.request({"page": 1})[0] == []
    job.close()


def test_forecast_grid_body_and_issue_target_times_are_stored_in_postgres(tmp_path):
    job = Download(tmp_path, "koen", "key", 0)
    body = gzip.compress(b"original-grid")
    ingest_forecast_grid(
        job.db.connection,
        "forecast:test",
        {"tmfc": "2026112108", "tmef": "2026112206", "vars": "TMP"},
        body,
        {"fetched_at_utc": "2026-11-22T00:00:00+00:00"},
    )
    result = execute(
        job.db.connection, "SELECT body_gzip,issued_at,target_at FROM public.weather_forecast_grids"
    ).fetchone()
    assert gzip.decompress(bytes(result[0])) == b"original-grid"
    assert result[1] < result[2]
    job.close()


def test_forecast_targets_on_different_days_are_not_collapsed(tmp_path):
    job = Download(tmp_path, "forecast", "key", 0)
    row = dict(
        baseDate="20261121",
        baseTime="0800",
        nx=60,
        ny=127,
        fcstDate="20261122",
        fcstTime="0600",
        category="TMP",
        fcstValue="5",
    )
    job.save_records(
        [row, dict(row, fcstDate="20261123", fcstValue="6")],
        "r",
        {"fetched_at_utc": "2026-11-22T01:00:00+00:00"},
        date.min,
        date.max,
    )
    assert job.count() == 2
    assert execute(job.db.connection, "SELECT count(*) FROM public.observations").fetchone()[0] == 2
    job.close()
