import csv
import gzip
import pytest

from scripts.collect_public_history import APIError, Download, months, parse_response, record_identity
from datetime import date


def test_provider_response_variants():
    koen = b'{"reponse":{"header":{"resultCode":"00"},"body":{"content":[{"ippt":"A"}],"totalElements":3}}}'
    assert parse_response(koen)[:2] == ([{"ippt": "A"}], 3)
    kospo = b'<response><header><resultCode>800</resultCode></header><body><items><ymd>20220101</ymd></items><items><ymd>20220102</ymd></items><paginginfo><totalCnt>2</totalCnt></paginginfo></body></response>'
    assert parse_response(kospo)[:2] == ([{"ymd": "20220101"}, {"ymd": "20220102"}], 2)
    assert parse_response(b'{"reponse":{"header":{"resultCode":"3","resultMsg":"NODATA_ERROR"},"body":null}}')[0] == []


def test_error_response_is_not_an_empty_success():
    with pytest.raises(APIError, match="30"):
        parse_response(b'<OpenAPI_ServiceResponse><cmmMsgHeader><returnReasonCode>30</returnReasonCode><errMsg>not registered</errMsg></cmmMsgHeader></OpenAPI_ServiceResponse>')
    with pytest.raises(APIError, match="10"):
        parse_response(b'{"response":{"header":{"resultCode":"10","resultMsg":"recent only"}}}')


def test_smp_hour_padding_does_not_create_duplicate_market_intervals():
    row = dict(date="20220101", areaName="land", hour="01")
    assert record_identity("smp", row) == record_identity("smp", dict(row, hour="1"))


def test_same_western_plant_name_with_different_capacity_is_retained(tmp_path):
    job = Download(tmp_path, "iwest", "secret", 0)
    row = dict(date="20220101", genNm="same-name", qcap="1", q01="100")
    job.save_records([row, dict(row, qcap="2", q01="200")], "r", {"fetched_at_utc": "now"}, date(2022,1,1), date(2022,1,31))
    assert job.count() == 2
    job.db.close()


def test_months_preserve_leap_day_and_partial_bounds():
    assert list(months(date(2024, 2, 20), date(2024, 3, 2))) == [
        (date(2024, 2, 20), date(2024, 2, 29)),
        (date(2024, 3, 1), date(2024, 3, 2)),
    ]


def test_pagination_follows_total_when_server_clamps_page_size(tmp_path):
    job = Download(tmp_path, "koen", "secret", 0)
    pages = []
    def request(params):
        pages.append(params["pageNo"])
        rows = [{"id": i} for i in ([1, 2] if params["pageNo"] == 1 else [3])]
        return rows, 3, {}, "rid", {}
    job.request = request
    assert sum(len(rows) for rows, _, _ in job.pages({})) == 3
    assert pages == [1, 2]
    job.db.close()


def test_repeated_page_is_rejected(tmp_path):
    job = Download(tmp_path, "koen", "secret", 0)
    job.request = lambda p: ([{"id": 1}], 3, {}, "rid", {})
    with pytest.raises(APIError, match="PAGINATION_STALLED"):
        list(job.pages({}))
    job.db.close()


def test_export_retains_hour_24_negative_values_and_missingness(tmp_path):
    job = Download(tmp_path, "koen", "secret", 0)
    row = dict(dgenYmd="2022-01-01", ippt="A", hogi="1", ipptNam="plant", qhorGen01=-1, qhorGen24=7)
    job.save_records([row], "first", {"fetched_at_utc": "2026-11-21T00:00:00+00:00"}, date(2022, 1, 1), date(2022, 1, 1))
    report = job.export(date(2022, 1, 1), date(2022, 1, 1))
    assert report["hourly_rows"] == 24
    assert report["missing_hour_values"] == 22
    with gzip.open(tmp_path / "koen" / "hourly_records.csv.gz", "rt", encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0]["value_raw"] == "-1"
    assert rows[-1]["interval_end_kst"] == "2022-01-02T00:00:00+09:00"
    assert rows[-1]["interval_start_kst"] == "2022-01-01T23:00:00+09:00"
    assert rows[1]["value_raw"] == "" and rows[1]["is_missing"] == "1"
    assert "capacity_kw" not in (tmp_path / "koen" / "daily_records.csv").read_text(encoding="utf-8-sig")
    job.db.close()


def test_revisions_and_requested_date_filter(tmp_path):
    job = Download(tmp_path, "koen", "secret", 0)
    base = dict(dgenYmd="2022-01-01", ippt="A", hogi="1", qhorGen01=1)
    args = ("r", {"fetched_at_utc": "now"}, date(2022, 1, 1), date(2022, 1, 31))
    job.save_records([base, dict(base, dgenYmd="2022-02-01")], *args)
    job.save_records([dict(base, qhorGen01=2)], *args)
    assert job.count() == 1
    assert job.db.execute("SELECT count(*) FROM revisions").fetchone()[0] == 1
    job.db.close()


def test_completed_response_resumes_without_network_and_redacts_key(tmp_path):
    job = Download(tmp_path, "koen", "secret-key", 0)
    class Response:
        status_code = 200
        content = b'{"response":{"header":{"resultCode":"00"},"body":{"items":{"item":[{"echo":"secret-key"}]},"totalCount":1}}}'
    calls = []
    job.session.get = lambda *a, **k: calls.append(1) or Response()
    assert job.request({"pageNo": 1})[0] == [{"echo": "[REDACTED]"}]
    job.db.close()
    resumed = Download(tmp_path, "koen", "secret-key", 0)
    resumed.session.get = lambda *a, **k: pytest.fail("network requested on resume")
    assert resumed.request({"pageNo": 1})[0] == [{"echo": "[REDACTED]"}]
    assert calls == [1]
    resumed.db.close()
