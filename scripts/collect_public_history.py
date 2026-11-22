"""Collect approved public API data into PostgreSQL, retaining raw file copies.

Raw HTTP bodies, request parameters (without keys), collection timestamps and
coverage reports are retained locally. Missing values and units are not guessed.
"""

from __future__ import annotations

import argparse
import calendar
import concurrent.futures
import csv
import gzip
import hashlib
import json
import os
import re
import sys
import threading
import time
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote

import requests
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector_credentials import get_api_key
from scripts.postgres_store import open_store, store_exists, persist_runtime_state

ROOT = Path(__file__).resolve().parents[1]
KST = timezone(timedelta(hours=9))
ENDPOINTS = {
    "koen": "https://apis.data.go.kr/B551893/solar-power-by-hour/list",
    "kospo": "https://apis.data.go.kr/B552520/PwrSunLightInfo/getDataService",
    "komipo": "https://apis.data.go.kr/B552521/solarGen/getData",
    "iwest": "https://apis.data.go.kr/B552522/pg/reGeneration/getReGeneration",
    "smp": "https://apis.data.go.kr/B552115/SmpWithForecastDemand/getSmpWithForecastDemand",
    "forecast": "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst",
}
# Reserve includes inspection requests made before this downloader was started.
LIMITS = {"koen": 9990, "kospo": 9990, "komipo": 990, "iwest": 90, "smp": 90, "forecast": 9990}
# Official 15157016 guide, appendix "설비 및 호기명 코드표"; ESS excluded.
KOMIPO_GUIDE_URL = (
    "https://www.data.go.kr/cmm/cmm/fileDownload.do?atchFileId=FILE_000000003575637&fileDetailSn=1"
)
KOMIPO_RESOURCES = [
    ["8573", "001"],
    ["8573", "002"],
    ["8575", "001"],
    ["85E1", "001"],
    ["85E1", "002"],
    ["85E1", "003"],
    ["85E1", "004"],
    ["85E3", "001"],
    ["85G1", "003"],
    ["881A", "001"],
    ["881A", "002"],
    ["881B", "001"],
    ["881E", "001"],
    ["881E", "002"],
    ["88E1", "001"],
    ["88E1", "002"],
    ["88E1", "003"],
    ["88G1", "001"],
    ["88G1", "002"],
    ["8901", "001"],
    ["8911", "001"],
    ["8995", "001"],
    ["A801", "001"],
    ["A801", "002"],
    ["A901", "001"],
    ["A901", "002"],
]
PRINT_LOCK = threading.Lock()


def emit(**values):
    with PRINT_LOCK:
        print(json.dumps(values, ensure_ascii=False), flush=True)


def now():
    return datetime.now(timezone.utc).isoformat()


def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    persist_runtime_state(path, obj)


class APIError(Exception):
    def __init__(self, code, message):
        self.code = str(code)
        self.message = str(message)
        super().__init__(f"API {self.code}: {self.message}")


def decode_body(raw):
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise APIError("DECODE", "Response is neither UTF-8 nor CP949")


def parse_response(raw):
    """Return records, advertised total and header, including provider quirks."""
    text = decode_body(raw).strip()
    if text.startswith("{"):
        obj = json.loads(text)
        response = obj.get("response", obj.get("reponse", obj))
        header = response.get("header", response.get("cmmMsgHeader", {}))
        if "OpenAPI_ServiceResponse" in obj:
            header = obj["OpenAPI_ServiceResponse"].get("cmmMsgHeader", {})
        body = response.get("body", {}) or {}
        rows = body.get("content", body.get("items", []))
        if isinstance(rows, dict):
            rows = rows.get("item", [])
        if isinstance(rows, dict):
            rows = [rows]
        rows = [r for r in (rows or []) if isinstance(r, dict)]
        total = body.get("totalElements", body.get("totalCount"))
    elif text.startswith("<"):
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            raise APIError("NON_API", "Response was not valid API XML") from None
        header_node = root.find(".//header")
        if header_node is None:
            header_node = root.find(".//cmmMsgHeader")
        header = {c.tag: c.text or "" for c in header_node} if header_node is not None else {}
        nodes = root.findall(".//item")
        if not nodes:
            nodes = [n for n in root.findall(".//items") if len(n) and all(len(c) == 0 for c in n)]
        rows = [{c.tag: c.text for c in n} for n in nodes]
        total_node = next(
            (
                root.find(".//" + tag)
                for tag in ("totalCount", "totalCnt", "totalElements")
                if root.find(".//" + tag) is not None
            ),
            None,
        )
        total = total_node.text if total_node is not None else None
    else:
        raise APIError("NON_API", "Response was not JSON or XML")
    code = str(header.get("resultCode", header.get("returnReasonCode", "")))
    message = header.get("resultMsg", header.get("errMsg", "Missing API result header"))
    if code not in {"00", "0", "800", "03", "3", "NODATA_ERROR"}:
        raise APIError(code or "NO_HEADER", message)
    if code in {"03", "3", "NODATA_ERROR"}:
        rows = []
    return rows, int(total) if total not in (None, "") else None, header


def months(start, end):
    current = start
    while current <= end:
        stop = min(
            end, date(current.year, current.month, calendar.monthrange(current.year, current.month)[1])
        )
        yield current, stop
        current = stop + timedelta(days=1)


def clean_day(value):
    digits = re.sub(r"\D", "", str(value))[:8]
    return date.fromisoformat(f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}")


def record_identity(source, row):
    if source == "koen":
        return clean_day(row["dgenYmd"]), f"{row['ippt']}:{row['hogi']}", row.get("ipptNam", "")
    if source == "kospo":
        return clean_day(row["ymd"]), f"{row['gencd']}:{row['hogi']}", row.get("ipptnm", "")
    if source == "komipo":
        return clean_day(row["dgenYmd"]), f"{row['ipptGrp']}:{row['grp']}", row.get("ncodR", "")
    if source == "iwest":
        # The provider returns distinct installations with identical genNm.
        # Capacity is only a disambiguator, not a verified stable plant ID.
        return clean_day(row["date"]), f"{row['genNm']}|qcap={row.get('qcap', '')}", str(row["genNm"])
    if source == "smp":
        return clean_day(row["date"]), f"{row['areaName']}:{int(row['hour'])}", row["areaName"]
    if source == "forecast":
        key = f"{clean_day(row['baseDate']):%Y%m%d}:{str(row['baseTime']).zfill(4)}:{int(row['nx'])}:{int(row['ny'])}:{str(row['fcstTime']).zfill(4)}:{row['category']}"
        return clean_day(row["fcstDate"]), key, f"grid:{row['nx']}:{row['ny']}"
    raise ValueError("Unsupported source")


class UsageLedger:
    """Shared daily counts for historical and ongoing collectors."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path = self.path.with_suffix("")
        self.db = open_store(self.path, "usage")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY, source TEXT, at TEXT, day TEXT)"
        )
        self.db.execute("CREATE INDEX IF NOT EXISTS source_day ON calls(source,day)")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS blocks (source TEXT, day TEXT, reason TEXT, PRIMARY KEY(source,day))"
        )
        self.db.commit()

    def seed(self, output):
        for source in ENDPOINTS:
            path = Path(output) / source
            if not store_exists(path):
                continue
            local = open_store(path)
            try:
                rows = local.execute("SELECT id,at,day FROM calls").fetchall()
            finally:
                local.close()
            prefix = local.schema
            self.db.executemany(
                "INSERT INTO calls VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
                [(f"{prefix}:{rowid}", source, at, day) for rowid, at, day in rows],
            )
        self.db.commit()

    def used(self, source, day=None):
        day = day or datetime.now(KST).date().isoformat()
        return self.db.execute(
            "SELECT count(*) FROM calls WHERE source=? AND day=?", (source, day)
        ).fetchone()[0]

    def claim(self, local_db, directory, source, today, limit):
        self.db.execute("LOCK TABLE calls IN EXCLUSIVE MODE")
        try:
            used = self.used(source, today)
            blocked = self.db.execute(
                "SELECT reason FROM blocks WHERE source=? AND day=?", (source, today)
            ).fetchone()
            if used >= limit or blocked:
                raise APIError(
                    "LOCAL_DAILY_BUDGET",
                    f"Used {used} calls; waiting for reserved budget or next daily reset",
                )
            at = now()
            row = local_db.execute(
                "INSERT INTO calls(at,day) VALUES (?,?) RETURNING id", (at, today)
            ).lastrowid
            local_db.commit()
            prefix = local_db.schema
            self.db.execute("INSERT INTO calls VALUES (?,?,?,?)", (f"{prefix}:{row}", source, at, today))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def block_today(self, source, reason):
        self.db.execute(
            "INSERT INTO blocks VALUES (?,?,?) ON CONFLICT(source,day) DO UPDATE SET reason=excluded.reason",
            (source, datetime.now(KST).date().isoformat(), reason),
        )
        self.db.commit()

    def close(self):
        self.db.close()


class Download:
    def __init__(self, output, source, key, delay=0.6, usage_path=None):
        self.output, self.source, self.key, self.delay = Path(output), source, key, delay
        self.directory = self.output / source
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db = open_store(self.directory)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, status TEXT, path TEXT, metadata TEXT)"
        )
        self.db.execute("CREATE TABLE IF NOT EXISTS calls (at TEXT, day TEXT)")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS records (day TEXT, resource TEXT, payload TEXT, fetched TEXT, request_id TEXT, PRIMARY KEY(day,resource))"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS revisions (day TEXT, resource TEXT, payload TEXT, fetched TEXT, request_id TEXT)"
        )
        self.db.commit()
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "SolarBeliever-PublicDataCollector/1.0"
        self.last_call = 0.0
        self.max_attempts = 1
        self.cache_seconds = None
        self.empty_cache_seconds = None
        self.usage = UsageLedger(usage_path) if usage_path else None
        if self.usage:
            self.usage.seed(output)

    def claim_call(self, today):
        if self.usage:
            self.usage.claim(self.db, self.directory, self.source, today, LIMITS[self.source])
            return
        used = self.db.execute("SELECT count(*) FROM calls WHERE day=?", (today,)).fetchone()[0]
        if used >= LIMITS[self.source]:
            raise APIError("LOCAL_DAILY_BUDGET", f"Stopped at {used} calls; resume after quota resets")
        self.db.execute("INSERT INTO calls(at,day) VALUES (?,?) RETURNING id", (now(), today))
        self.db.commit()

    def request(self, params, endpoint=None):
        endpoint = endpoint or ENDPOINTS[self.source]
        safe = dict(params)
        identity = hashlib.sha256(json.dumps([endpoint, safe], sort_keys=True).encode()).hexdigest()[:24]
        cached = self.db.execute(
            "SELECT status,path,metadata FROM requests WHERE id=?", (identity,)
        ).fetchone()
        cache_fresh = cached is not None and cached[0] == "ok"
        if cache_fresh:
            cached_meta = json.loads(cached[2])
            ttl = (
                self.empty_cache_seconds
                if cached_meta.get("rows") == 0 and self.empty_cache_seconds is not None
                else self.cache_seconds
            )
            if ttl is not None:
                age = (
                    datetime.now(timezone.utc) - datetime.fromisoformat(cached_meta["fetched_at_utc"])
                ).total_seconds()
                cache_fresh = age < ttl
        if cache_fresh:
            path = self.directory / cached[1]
            if path.is_file():
                with gzip.open(path, "rb") as stream:
                    raw = stream.read()
            else:
                response = self.db.execute(
                    "SELECT body_gzip FROM raw_responses WHERE path=?", (cached[1],)
                ).fetchone()
                if response is None:
                    raise APIError("CACHE_MISSING", "PostgreSQL cached response is missing")
                raw = gzip.decompress(bytes(response[0]))
            if hashlib.sha256(raw).hexdigest() != json.loads(cached[2])["sha256"]:
                raise APIError("CACHE_CHECKSUM", "Cached response checksum mismatch")
            return (*parse_response(raw), identity, json.loads(cached[2]))
        for attempt in range(self.max_attempts):
            today = datetime.now(KST).date().isoformat()
            time.sleep(max(0, self.delay - (time.monotonic() - self.last_call)))
            self.claim_call(today)
            self.last_call = time.monotonic()
            collected = now()
            try:
                response = self.session.get(
                    endpoint, params={"serviceKey": self.key, **safe}, timeout=(15, 90)
                )
                raw = response.content
            except requests.RequestException as exc:
                if attempt < self.max_attempts - 1:
                    time.sleep(2 ** (attempt + 1))
                    continue
                raise APIError("NETWORK", type(exc).__name__) from None
            # Some API error pages echo request URLs. Never persist a credential.
            raw = raw.replace(self.key.encode(), b"[REDACTED]")
            filename = (
                f"responses/{identity}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}.body.gz"
            )
            path = self.directory / filename
            path.parent.mkdir(exist_ok=True)
            with gzip.open(path, "wb") as stream:
                stream.write(raw)
            metadata = {
                "endpoint": endpoint,
                "params": safe,
                "fetched_at_utc": collected,
                "http_status": response.status_code,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw),
            }
            dump(path.with_suffix(".json"), metadata)
            self.db.store_raw(filename, gzip.compress(raw, mtime=0), metadata)
            try:
                rows, total, header = parse_response(raw)
                if response.status_code != 200:
                    raise APIError(f"HTTP_{response.status_code}", "Non-success HTTP response")
            except (APIError, ValueError) as exc:
                error = exc if isinstance(exc, APIError) else APIError("PARSE", type(exc).__name__)
                metadata["error_code"] = error.code
                self.db.execute(
                    "INSERT INTO requests VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,path=excluded.path,metadata=excluded.metadata",
                    (identity, "error", filename, json.dumps(metadata)),
                )
                self.db.commit()
                if self.usage and error.code == "22":
                    self.usage.block_today(self.source, error.message)
                if response.status_code in {429, 500, 502, 503, 504} and attempt < self.max_attempts - 1:
                    time.sleep(2 ** (attempt + 1))
                    continue
                raise error from None
            metadata["rows"], metadata["total"], metadata["header"] = len(rows), total, header
            self.db.execute(
                "INSERT INTO requests VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,path=excluded.path,metadata=excluded.metadata",
                (identity, "ok", filename, json.dumps(metadata)),
            )
            self.db.commit()
            return rows, total, header, identity, metadata
        raise APIError("RETRY_EXHAUSTED", "Request did not complete")

    def close(self):
        self.db.close()
        if self.usage:
            self.usage.close()

    def pages(self, params, page_key="pageNo", size_key="numOfRows", size=10000):
        page, received, previous_fingerprint = 1, 0, None
        while True:
            rows, total, header, request_id, metadata = self.request(
                {**params, page_key: page, size_key: size}
            )
            fingerprint = hashlib.sha256(
                json.dumps(rows, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
            if rows and fingerprint == previous_fingerprint:
                raise APIError("PAGINATION_STALLED", "Server returned the same page twice")
            previous_fingerprint = fingerprint
            yield rows, request_id, metadata
            received += len(rows)
            if total is not None and received >= total:
                break
            if not rows:
                if total is not None and received < total:
                    raise APIError("INCOMPLETE_PAGES", f"Received {received} of {total} advertised rows")
                break
            if total is None and len(rows) < size:
                break
            page += 1

    def save_records(self, rows, request_id, metadata, start, end):
        count = 0
        changed = []
        for row in rows:
            day, resource, _ = record_identity(self.source, row)
            if not start <= day <= end:
                continue
            payload = json.dumps(row, ensure_ascii=False, sort_keys=True)
            previous = self.db.execute(
                "SELECT payload FROM records WHERE day=? AND resource=?", (day.isoformat(), resource)
            ).fetchone()
            values = (day.isoformat(), resource, payload, metadata["fetched_at_utc"], request_id)
            if previous and previous[0] == payload:
                # Retain the first actual receipt time of unchanged values.
                count += 1
                continue
            if previous and previous[0] != payload:
                self.db.execute(
                    "INSERT INTO revisions SELECT * FROM records WHERE day=? AND resource=?",
                    (day.isoformat(), resource),
                )
            self.db.execute(
                "INSERT INTO records VALUES (?,?,?,?,?) ON CONFLICT(day,resource) DO UPDATE SET payload=excluded.payload,fetched=excluded.fetched,request_id=excluded.request_id",
                values,
            )
            changed.append(values)
            count += 1
        if changed:
            from scripts.postgres_ingest import ingest_daily_records

            ingest_daily_records(self.db.connection, self.source, changed)
        self.db.commit()
        return count

    def collect(self, start, end, komipo_resources):
        if self.source == "forecast":
            attempts = []
            for probe_date in sorted({start, end}):
                try:
                    rows, total, header, rid, metadata = self.request(
                        dict(
                            base_date=probe_date.strftime("%Y%m%d"),
                            base_time="0500",
                            nx=60,
                            ny=127,
                            pageNo=1,
                            numOfRows=10000,
                            dataType="JSON",
                        )
                    )
                    attempts.append({"date": probe_date.isoformat(), "rows": len(rows), "header": header})
                except APIError as exc:
                    attempts.append(
                        {"date": probe_date.isoformat(), "code": exc.code, "message": exc.message}
                    )
            dump(
                self.directory / "historical_availability.json",
                {
                    "attempts": attempts,
                    "scope": "Archive availability probes; not a fleet forecast dataset",
                    "requested_start": str(start),
                    "requested_end": str(end),
                },
            )
            if all(a.get("code") == "10" for a in attempts):
                raise APIError(
                    "ARCHIVE_NOT_PROVIDED",
                    "This API only provides the most recent 3 days; requested historical issues cannot be downloaded",
                )
            raise APIError(
                "ARCHIVE_NOT_COLLECTED",
                "Historical availability probe retained; full historical coverage is not established",
            )
        if self.source == "smp":
            # date is optional in the official API. A live probe confirmed that
            # omission enumerates historical records, avoiding 1 request/day.
            for rows, rid, meta in self.pages({"dataType": "json"}):
                self.save_records(rows, rid, meta, start, end)
                emit(source=self.source, page_rows=len(rows), stored=self.count())
            return
        if self.source == "komipo":
            failures = []
            for code, unit in komipo_resources:
                try:
                    rows, total, _, rid, meta = self.request(
                        dict(
                            strDateS=start.strftime("%Y%m%d"),
                            strDateE=end.strftime("%Y%m%d"),
                            strOrgNo=code,
                            strHoki=unit,
                        )
                    )
                    if total is not None and total != len(rows):
                        raise APIError(
                            "INCOMPLETE_RESPONSE", "KOMIPO row count differs from its advertised total"
                        )
                    n = self.save_records(rows, rid, meta, start, end)
                    emit(source=self.source, resource=f"{code}:{unit}", records=n, stored=self.count())
                except APIError as exc:
                    failures.append({"code": code, "unit": unit, "error": exc.code, "message": exc.message})
            dump(
                self.directory / "resource_requests.json",
                {"requested": komipo_resources, "failures": failures},
            )
            if failures:
                raise APIError("RESOURCE_ERRORS", json.dumps(failures, ensure_ascii=False))
            return
        for left, right in months(start, end):
            s, e = left.strftime("%Y%m%d"), right.strftime("%Y%m%d")
            if self.source == "koen":
                batches = [self.pages(dict(startD=s, endD=e), "page", "size", size=100)]
            elif self.source == "kospo":
                batches = [self.pages(dict(strSdate=s, strEdate=e))]
            elif self.source == "iwest":
                batches = [self.pages(dict(startDate=s, endDate=e), size=100)]
            else:
                raise ValueError("Unknown source")
            n = 0
            for batch in batches:
                for rows, rid, meta in batch:
                    n += self.save_records(rows, rid, meta, start, end)
            emit(source=self.source, month=s[:6], records=n, stored=self.count())

    def count(self):
        return self.db.execute("SELECT count(*) FROM records").fetchone()[0]

    def export(self, start, end, error=None):
        fields = set()
        per_resource = {}
        per_month = Counter()
        empty_hour_values = 0
        hourly_rows = 0
        for day, resource, payload in self.db.execute(
            "SELECT day,resource,payload FROM records ORDER BY day,resource"
        ):
            row = json.loads(payload)
            fields.update(row)
            _, _, name = record_identity(self.source, row)
            info = per_resource.setdefault(
                resource,
                {
                    "resource_id": resource,
                    "name": name,
                    "first_day": day,
                    "last_day": day,
                    "days": 0,
                    "missing_days_between_first_and_last": 0,
                },
            )
            if self.source == "iwest":
                info["capacity_raw"] = row.get("qcap")
                info["identity_basis"] = "provider_name_and_capacity; stable resource ID not supplied"
            info["last_day"], info["days"] = day, info["days"] + 1
            per_month[day[:7]] += 1
        base_fields = ["source", "delivery_date", "resource_id", "collected_at_utc", "request_id"]
        with (self.directory / "daily_records.csv").open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.DictWriter(stream, fieldnames=base_fields + sorted(fields))
            writer.writeheader()
            for day, resource, payload, fetched, rid in self.db.execute(
                "SELECT * FROM records ORDER BY day,resource"
            ):
                writer.writerow(
                    dict(
                        source=self.source,
                        delivery_date=day,
                        resource_id=resource,
                        collected_at_utc=fetched,
                        request_id=rid,
                        **json.loads(payload),
                    )
                )
        if self.source not in {"forecast"}:
            hour_fields = [
                "source",
                "delivery_date",
                "resource_id",
                "resource_name",
                "provider_hour",
                "interval_start_kst",
                "interval_end_kst",
                "source_field",
                "value_raw",
                "unit_status",
                "is_missing",
                "collected_at_utc",
                "request_id",
            ]
            with gzip.open(
                self.directory / "hourly_records.csv.gz", "wt", newline="", encoding="utf-8-sig"
            ) as stream:
                writer = csv.DictWriter(stream, fieldnames=hour_fields)
                writer.writeheader()
                for day, resource, payload, fetched, rid in self.db.execute(
                    "SELECT * FROM records ORDER BY day,resource"
                ):
                    row = json.loads(payload)
                    _, _, name = record_identity(self.source, row)
                    hour_range = [int(row["hour"])] if self.source == "smp" else range(1, 25)
                    for hour in hour_range:
                        if not 1 <= hour <= 24:
                            raise ValueError("Provider hour outside 1..24")
                        if self.source == "smp":
                            field, unit = "smp", "KRW/kWh"
                        else:
                            prefix = {"koen": "qhorGen", "kospo": "qhorgen", "iwest": "q", "komipo": "q"}[
                                self.source
                            ]
                            field, unit = f"{prefix}{hour:02d}", "not_independently_verified"
                        value = row.get(field)
                        missing = value in (None, "", "null")
                        empty_hour_values += int(missing)
                        left = datetime.fromisoformat(day).replace(tzinfo=KST) + timedelta(hours=hour - 1)
                        writer.writerow(
                            dict(
                                source=self.source,
                                delivery_date=day,
                                resource_id=resource,
                                resource_name=name,
                                provider_hour=hour,
                                interval_start_kst=left.isoformat(),
                                interval_end_kst=(left + timedelta(hours=1)).isoformat(),
                                source_field=field,
                                value_raw=value,
                                unit_status=unit,
                                is_missing=int(missing),
                                collected_at_utc=fetched,
                                request_id=rid,
                            )
                        )
                        hourly_rows += 1
        if self.source in {"koen", "kospo", "komipo", "iwest"}:
            from scripts.prepare_generation import prepare_file

            prepare_file(self.directory / "hourly_records.csv.gz")
        for info in per_resource.values():
            days = (date.fromisoformat(info["last_day"]) - date.fromisoformat(info["first_day"])).days + 1
            info["missing_days_between_first_and_last"] = days - info["days"]
        roster_fields = sorted({key for info in per_resource.values() for key in info}) or ["resource_id"]
        with (self.directory / "resource_roster.csv").open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.DictWriter(stream, fieldnames=roster_fields)
            writer.writeheader()
            writer.writerows(per_resource.values())
        # SMP resource keys include hour; expose zones separately for readability.
        report = {
            "source": self.source,
            "requested_start": str(start),
            "requested_end": str(end),
            "status": "stopped" if error else "download_finished",
            "error": error,
            "stored_records": self.count(),
            "hourly_rows": hourly_rows,
            "missing_hour_values": empty_hour_values,
            "resource_count": len(per_resource),
            "monthly_records": dict(sorted(per_month.items())),
            "resources": list(per_resource.values()),
            "generated_at_utc": now(),
            "notes": [
                "Raw source values retained; no interpolation, clipping or invented metadata.",
                "First/last observed dates do not establish commissioning or closure.",
                "Received source records and service observations are committed to PostgreSQL; files are retained as copies.",
            ],
        }
        dump(self.directory / "coverage.json", report)
        return report


def execute_source(output, source, key, start, end, resources, delay):
    job = Download(output, source, key, delay, usage_path=ROOT / "data/public_api_usage.sqlite")
    error = None
    result = {}
    try:
        if source == "forecast":
            job.collect(start, end, resources)
        else:
            from scripts.collection_queue import enqueue, load_queue, run_queue

            path = job.directory / "forward_queue.json"
            queue = load_queue(path)
            if source == "komipo":
                for code, unit in resources:
                    params = dict(
                        strDateS=start.strftime("%Y%m%d"),
                        strDateE=end.strftime("%Y%m%d"),
                        strOrgNo=code,
                        strHoki=unit,
                    )
                    enqueue(queue, params, start, end)
            elif source == "smp":
                enqueue(queue, {"dataType": "json"}, start, end, "pageNo", "numOfRows", 10000)
            else:
                for left, right in months(start, end):
                    first, last = left.strftime("%Y%m%d"), right.strftime("%Y%m%d")
                    if source == "koen":
                        params, page, size, count = dict(startD=first, endD=last), "page", "size", 100
                    elif source == "kospo":
                        params, page, size, count = (
                            dict(strSdate=first, strEdate=last),
                            "pageNo",
                            "numOfRows",
                            10000,
                        )
                    else:
                        params, page, size, count = (
                            dict(startDate=first, endDate=last),
                            "pageNo",
                            "numOfRows",
                            100,
                        )
                    enqueue(queue, params, left, right, page, size, count)
            result = run_queue(job, queue, path)
            error = result.get("error")
            if not error and result.get("skipped_tasks"):
                error = {
                    "code": "SKIPPED_REQUESTS",
                    "message": "Failed or empty archive requests were skipped; see forward_queue.json",
                }
    except APIError as exc:
        error = {"code": exc.code, "message": exc.message}
    except Exception as exc:
        error = {"code": "LOCAL_ERROR", "message": type(exc).__name__}
    report = job.export(start, end, error)
    report.update(
        policy="skip_errors_and_gaps",
        resource_count_limit=None,
        skipped_pages=result.get("skipped_pages", 0),
        skipped_tasks=result.get("skipped_tasks", 0),
    )
    dump(job.directory / "coverage.json", report)
    job.close()
    emit(
        source=source,
        status=report["status"],
        stored_records=report["stored_records"],
        hourly_rows=report["hourly_rows"],
        error=error,
    )
    return report


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2022, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 10, 31))
    parser.add_argument("--sources", default=",".join(ENDPOINTS))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--delay", type=float, default=0.6)
    parser.add_argument("--komipo-resources", type=Path, help="JSON list of [official plant code, unit code]")
    parser.add_argument(
        "--iwest-daily-limit",
        type=int,
        default=100,
        help="Approved daily limit; 10 calls are reserved for manual inspection",
    )
    args = parser.parse_args()
    if args.start > args.end or args.delay < 0 or not 1 <= args.workers <= 6:
        parser.error("Invalid date range, delay or worker count")
    sources = list(dict.fromkeys(args.sources.split(",")))
    if any(s not in ENDPOINTS for s in sources):
        parser.error("Unknown source")
    if args.iwest_daily_limit < 11:
        parser.error("Daily limit must exceed the 10-call inspection reserve")
    LIMITS["iwest"] = args.iwest_daily_limit - 10
    key = unquote(get_api_key("DATA_GO_KR_API_KEY", ROOT / ".env")).strip()
    if not key:
        parser.error("DATA_GO_KR_API_KEY is missing from local .env")
    output = args.output or ROOT / "data" / f"api_history_{args.start:%Y%m%d}_{args.end:%Y%m%d}"
    output.mkdir(parents=True, exist_ok=True)
    config_path = output / "run_config.json"
    previous = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    if previous and (previous.get("start"), previous.get("end")) != (str(args.start), str(args.end)):
        parser.error("Output directory belongs to a different date range; use a separate --output")
    from scripts.generation_roster import all_komipo_resources

    resources = (
        json.loads(args.komipo_resources.read_text(encoding="utf-8"))
        if args.komipo_resources
        else all_komipo_resources(output, ROOT / "data/continuous_collection/live")
    )
    dump(
        config_path,
        {
            "start": str(args.start),
            "end": str(args.end),
            "sources": list(ENDPOINTS),
            "selected_sources": sources,
            "endpoints": ENDPOINTS,
            "key": "stored only in ignored root .env",
            "komipo_resource_scope": resources,
            "komipo_roster_basis": KOMIPO_GUIDE_URL,
            "komipo_roster_complete": False,
            "started_at_utc": previous.get("started_at_utc", now()),
            "last_started_at_utc": now(),
        },
    )
    reports = {}
    for source in ENDPOINTS:
        coverage = output / source / "coverage.json"
        if coverage.is_file():
            reports[source] = json.loads(coverage.read_text(encoding="utf-8"))
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        pending = {
            pool.submit(execute_source, output, s, key, args.start, args.end, resources, args.delay): s
            for s in sources
        }
        for future in concurrent.futures.as_completed(pending):
            source = pending[future]
            try:
                reports[source] = future.result()
            except Exception as exc:
                reports[source] = {"source": source, "status": "local_failure", "error": type(exc).__name__}
            dump(
                output / "collection_report.json",
                {
                    "requested_start": str(args.start),
                    "requested_end": str(args.end),
                    "sources": reports,
                    "updated_at_utc": now(),
                },
            )
    return 0 if all(r["status"] == "download_finished" for r in reports.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
