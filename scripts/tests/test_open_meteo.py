import gzip
import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import requests

from scripts.collect_open_meteo import Collector, QuotaWait, call_cost, chunks, parser
from scripts.open_meteo_store import Store, parse_hourly

UTC = timezone.utc


def response_document(lead=1):
    origin = datetime(2022, 1, 1, tzinfo=UTC) - timedelta(hours=9)
    suffix = f'_previous_day{lead}' if lead else ''
    return {'hourly': {'time': [int((origin + timedelta(hours=i)).timestamp()) for i in range(24)],
                       'temperature_2m' + suffix: [0] + [None] * 23},
            'hourly_units': {'temperature_2m' + suffix: '°C'}}


def test_hourly_kst_boundary_null_and_zero_are_preserved():
    rows, units, coverage = parse_hourly(response_document(), ['temperature_2m'], 1, date(2022, 1, 1), date(2022, 1, 1))
    assert rows[0][0] == datetime(2021, 12, 31, 15, tzinfo=UTC)
    assert rows[0][1] == {'temperature_2m': 0} and rows[0][2] == []
    assert rows[1][1] == {'temperature_2m': None} and rows[1][2] == ['temperature_2m']
    assert coverage['non_null_hours']['temperature_2m'] == 1 and units['temperature_2m'] == '°C'


def test_missing_array_and_hour_axis_are_not_successful_coverage():
    document = response_document()
    document['hourly']['temperature_2m_previous_day1'].pop()
    with pytest.raises(ValueError, match='unequal'):
        parse_hourly(document, ['temperature_2m'], 1, date(2022, 1, 1), date(2022, 1, 1))
    document = response_document()
    document['hourly']['time'][1] += 60
    with pytest.raises(ValueError, match='Non-hourly'):
        parse_hourly(document, ['temperature_2m'], 1, date(2022, 1, 1), date(2022, 1, 1))


def test_months_and_weighted_quota_include_leap_day():
    assert list(chunks(date(2024, 2, 28), date(2024, 3, 1))) == [
        (date(2024, 2, 28), date(2024, 2, 29)), (date(2024, 3, 1), date(2024, 3, 1))]
    assert call_cost(date(2022, 1, 1), date(2022, 1, 31)) == 6


def test_postgres_snapshot_replay_and_revision_do_not_overwrite():
    store = Store()
    at = datetime(2026, 11, 22, tzinfo=UTC)
    task = {'mode': 'historical_fixed_lead', 'model': 'jma_gsm', 'location_id': 'test',
            'lead_days': 1, 'start_date': date(2022, 1, 1), 'end_date': date(2022, 1, 1)}
    body = json.dumps(response_document()).encode()
    first, _ = store.save(task, {}, body, ['temperature_2m'], at, at)
    second, _ = store.save(task, {}, body, ['temperature_2m'], at, at + timedelta(hours=1))
    assert first == second
    assert store.query('SELECT count(*) FROM public.open_meteo_hourly').fetchone()[0] == 24
    raw = store.query('SELECT body_gzip,received_at FROM public.open_meteo_requests').fetchone()
    assert gzip.decompress(raw[0]) == body and raw[1] == at
    revised = response_document()
    revised['hourly']['temperature_2m_previous_day1'][0] = 5
    store.save(task, {}, json.dumps(revised).encode(), ['temperature_2m'], at, at + timedelta(hours=1))
    assert store.query('SELECT count(*) FROM public.open_meteo_requests').fetchone()[0] == 2
    store.close()


def test_history_quota_reserves_budget_for_live():
    store = Store()
    args = parser().parse_args([])
    collector = Collector(args, store=store)
    at = datetime.now(UTC)
    store.query("INSERT INTO public.open_meteo_calls(at,cost,mode,status) VALUES(%s,8499,'historical_fixed_lead','received')",
                (at - timedelta(hours=2),))
    store.conn.commit()
    with pytest.raises(QuotaWait):
        collector.reserve(2, 'historical_fixed_lead', at)
    assert collector.reserve(2, 'live_forecast', at)
    store.close()


def test_http_failure_is_checkpointed_and_retried_without_fake_success(monkeypatch):
    store = Store()
    store.query("""INSERT INTO public.open_meteo_tasks(task_id,location_id,model,lead_days,start_date,end_date)
        VALUES('test','loc','gfs_global',1,'2022-01-01','2022-01-01')""")
    store.conn.commit()
    collector = Collector(parser().parse_args([]), store=store)
    monkeypatch.setattr(collector.session, 'get', lambda *a, **k: (_ for _ in ()).throw(requests.Timeout()))
    task = {'task_id': 'test', 'location_id': 'loc', 'model': 'gfs_global', 'lead_days': 1,
            'start_date': date(2022, 1, 1), 'end_date': date(2022, 1, 1), 'mode': 'historical_fixed_lead'}
    assert collector.fetch(task, {'latitude': 35, 'longitude': 127}) is None
    row = store.query('SELECT state,attempts,error,due_at FROM public.open_meteo_tasks').fetchone()
    assert row[:3] == ('error', 1, 'Timeout') and row[3] > datetime.now(UTC)
    assert store.query('SELECT count(*) FROM public.open_meteo_requests').fetchone()[0] == 0
    store.close()


def test_new_run_waits_for_replication_buffer_and_retains_tomorrow(monkeypatch):
    store = Store()
    args = parser().parse_args(['--models', 'gfs_global'])
    collector = Collector(args, store=store)
    collector.locations = [{'location_id': 'loc', 'latitude': 35, 'longitude': 127}]
    current = datetime(2026, 11, 22, 6, tzinfo=UTC)
    meta = {'last_run_initialisation_time': int((current - timedelta(hours=6)).timestamp()),
            'last_run_availability_time': int(current.timestamp()), 'update_interval_seconds': 21600}
    monkeypatch.setattr(collector.session, 'get', lambda *a, **k: SimpleNamespace(raise_for_status=lambda: None, json=lambda: meta))
    fetched = []
    monkeypatch.setattr(collector, 'fetch', lambda task, *a: fetched.append(task) or ('request', {'non_null_hours': {'temperature_2m': 24, 'cloud_cover': 24}}))
    collector.collect_live(current + timedelta(minutes=9))
    assert not fetched
    collector.collect_live(current + timedelta(minutes=10))
    assert fetched[0]['start_date'] == fetched[0]['end_date'] == date(2026, 11, 23)
    collector.collect_live(current + timedelta(minutes=11))
    assert len(fetched) == 1
    store.close()


def test_history_first_does_not_poll_live_while_pending_history_exists(tmp_path, monkeypatch):
    store = Store()
    args = parser().parse_args(['--once', '--output', str(tmp_path)])
    collector = Collector(args, store=store)
    events = []
    monkeypatch.setattr(collector, 'seed', lambda today: events.append('seed'))
    monkeypatch.setattr(collector, 'history_one', lambda: events.append('history') or True)
    monkeypatch.setattr(collector, 'collect_live', lambda current: pytest.fail('live must wait for history'))
    result = collector.run()
    assert events == ['seed', 'history'] and result['state'] == 'bounded_run_complete'


def test_seed_resumes_and_only_adds_new_days(monkeypatch):
    import scripts.collect_open_meteo as module

    store = Store()
    args = parser().parse_args(['--start', '2022-01-01', '--models', 'jma_gsm', '--lead-days', '1'])
    collector = Collector(args, store=store)
    monkeypatch.setattr(module, 'known_locations', lambda s: [
        {'location_id': 'loc', 'latitude': 35, 'longitude': 127, 'members': []}])
    collector.seed(date(2022, 2, 1))
    collector.seed(date(2022, 2, 1))
    assert store.query('SELECT count(*) FROM public.open_meteo_tasks').fetchone()[0] == 2
    collector.seed(date(2022, 2, 2))
    assert store.query('SELECT start_date,end_date FROM public.open_meteo_tasks ORDER BY start_date DESC LIMIT 1').fetchone() == (date(2022, 2, 2), date(2022, 2, 2))
    store.close()
