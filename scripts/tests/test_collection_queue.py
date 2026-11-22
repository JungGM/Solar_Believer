import io
import json
import zipfile
from datetime import date

import pytest

from scripts.collect_public_history import APIError, Download
from scripts.collection_queue import enqueue, load_queue, run_queue
from scripts.generation_roster import parse_code_table


def records(first, last, day="20220101"):
    return [dict(dgenYmd=day, ippt=f"G{i:04d}", hogi="1") for i in range(first, last)]


def response(rows, total):
    return rows, total, {}, "request", {"fetched_at_utc": "2026-11-22T00:00:00+00:00"}


def monthly(queue, day="2022-01-01", end="2022-01-31", size=100):
    enqueue(
        queue,
        {"startD": day.replace("-", ""), "endD": end.replace("-", "")},
        date.fromisoformat(day),
        date.fromisoformat(end),
        "page",
        "size",
        size,
    )


def test_all_350_resources_are_saved_across_every_response_page(tmp_path):
    job = Download(tmp_path, "koen", "key", 0)
    path = tmp_path / "queue.json"
    queue = load_queue(path)
    monthly(queue)
    calls = []

    def request(params):
        page = params["page"]
        calls.append(page)
        return response(records((page - 1) * 100, min(page * 100, 350)), 350)

    job.request = request
    result = run_queue(job, queue, path)
    assert calls == [1, 2, 3, 4] and job.count() == 350
    assert result["completed_tasks"] == 1 and queue["resource_count_limit"] is None
    job.close()


def test_failed_middle_page_is_skipped_and_later_page_saved_without_retry(tmp_path):
    job = Download(tmp_path, "koen", "key", 0)
    path = tmp_path / "queue.json"
    queue = load_queue(path)
    monthly(queue)
    calls = []

    def request(params):
        page = params["page"]
        calls.append(page)
        if page == 2:
            raise APIError("02", "provider error")
        return response(records((page - 1) * 100, min(page * 100, 250)), 250)

    job.request = request
    result = run_queue(job, queue, path)
    assert calls == [1, 2, 3] and job.count() == 150 and result["skipped_pages"] == 1
    run_queue(job, load_queue(path), path)
    assert calls == [1, 2, 3]
    job.close()


def test_failed_first_month_does_not_block_next_month(tmp_path):
    job = Download(tmp_path, "koen", "key", 0)
    path = tmp_path / "queue.json"
    queue = load_queue(path)
    monthly(queue)
    monthly(queue, "2022-02-01", "2022-02-28")
    calls = []

    def request(params):
        calls.append(params["startD"])
        if params["startD"] == "20220101":
            raise APIError("HTTP_504", "timeout")
        return response(records(0, 1, "20220201"), 1)

    job.request = request
    result = run_queue(job, queue, path)
    assert calls == ["20220101", "20220201"] and job.count() == 1
    assert result["completed_tasks"] == 1 and result["skipped_tasks"] == 1
    job.close()


def test_quota_wait_preserves_cursor_and_resumes_only_uncollected_page(tmp_path):
    job = Download(tmp_path, "koen", "key", 0)
    path = tmp_path / "queue.json"
    queue = load_queue(path)
    monthly(queue)
    calls = []

    def request(params):
        page = params["page"]
        calls.append(page)
        if len(calls) == 2:
            raise APIError("LOCAL_DAILY_BUDGET", "wait until reset")
        return response(records((page - 1) * 100, page * 100), 200)

    job.request = request
    assert run_queue(job, queue, path)["state"] == "waiting"
    result = run_queue(job, load_queue(path), path)
    assert calls == [1, 2, 2] and job.count() == 200 and result["completed_tasks"] == 1
    job.close()


def test_previous_provider_error_is_skipped_without_new_http_call(tmp_path):
    import hashlib
    from scripts.collect_public_history import ENDPOINTS

    job = Download(tmp_path, "koen", "key", 0)
    path = tmp_path / "queue.json"
    queue = load_queue(path)
    monthly(queue)
    params = {"startD": "20220101", "endD": "20220131", "page": 1, "size": 100}
    identity = hashlib.sha256(json.dumps([ENDPOINTS["koen"], params], sort_keys=True).encode()).hexdigest()[
        :24
    ]
    job.db.execute(
        "INSERT INTO requests VALUES(?,?,?,?)",
        (identity, "error", "unused", json.dumps({"error_code": "02"})),
    )
    job.db.commit()
    job.request = lambda p: pytest.fail("failed archive request must not be retried")
    assert run_queue(job, queue, path)["skipped_tasks"] == 1
    job.close()


def test_empty_response_leaves_missing_data_and_advances(tmp_path):
    job = Download(tmp_path, "koen", "key", 0)
    path = tmp_path / "queue.json"
    queue = load_queue(path)
    monthly(queue)
    job.request = lambda p: response([], 0)
    result = run_queue(job, queue, path)
    assert job.count() == 0 and result["skipped_tasks"] == 1 and result["pending_tasks"] == 0
    job.close()


def test_official_roster_parser_keeps_more_than_200_units_and_separates_storage():
    def row(cells):
        return (
            "<w:tr>"
            + "".join(f"<w:tc><w:p><w:r><w:t>{v}</w:t></w:r></w:p></w:tc>" for v in cells)
            + "</w:tr>"
        )

    rows = row(["설비 코드", "설비 명", "호기 코드", "호기명"])
    rows += "".join(row([f"F{i:03d}", "태양광", "001", "1호기"]) for i in range(250))
    rows += row(["", "", "2ESS", "2ESS"])
    xml = f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:tbl>{rows}</w:tbl></w:body></w:document>'
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("word/document.xml", xml.encode())
    resources, storage = parse_code_table(stream.getvalue())
    assert len(resources) == 250 and resources[-1] == ("F249", "001")
    assert storage == [("F249", "2ESS")]


def test_http_failure_is_requested_once_without_backoff_retry(tmp_path):
    job = Download(tmp_path, "koen", "key", 0)

    class Response:
        status_code = 503
        content = b'{"response":{"header":{"resultCode":"00"},"body":{"items":[],"totalCount":0}}}'

    calls = []
    job.session.get = lambda *a, **kw: calls.append(1) or Response()
    with pytest.raises(APIError, match="HTTP_503"):
        job.request({"page": 1})
    assert calls == [1]
    job.close()
