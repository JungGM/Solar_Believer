"""Standalone Open-Meteo archive collector. Python 3.10+, standard library only."""
import argparse, base64, gzip, hashlib, json, math, os, signal, time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.request import urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parent
UTC = timezone.utc
VARIABLES = ["temperature_2m", "relative_humidity_2m", "dew_point_2m", "cloud_cover", "cloud_cover_low", "cloud_cover_mid", "cloud_cover_high", "wind_speed_10m", "wind_direction_10m", "pressure_msl", "precipitation", "snowfall", "snow_depth", "shortwave_radiation", "direct_radiation", "diffuse_radiation", "direct_normal_irradiance", "terrestrial_radiation", "sunshine_duration", "is_day"]
ENDPOINT = "https://previous-runs-api.open-meteo.com/v1/forecast"
STOP = False

def atomic(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)

def tasks(locations, start, end):
    current = end
    while current >= start:
        first = max(start, current.replace(day=1))
        for loc in locations:
            for model in ("gfs_global", "jma_gsm"):
                for lead in (1, 2):
                    yield {"location_id": loc["location_id"], "model": model, "lead_days": lead,
                           "start_date": str(first), "end_date": str(current), "location": loc}
        current = first - timedelta(days=1)

def task_id(task):
    return hashlib.sha256(json.dumps(task, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

def validate(body, task):
    doc = json.loads(body)
    hourly = doc.get("hourly", {})
    times = hourly.get("time", [])
    first = date.fromisoformat(task["start_date"])
    last = date.fromisoformat(task["end_date"])
    origin = datetime.combine(first, datetime.min.time(), UTC) - timedelta(hours=9)
    expected = [int((origin + timedelta(hours=i)).timestamp()) for i in range(((last-first).days+1)*24)]
    if times != expected:
        raise ValueError("Incomplete or mismatched KST hourly axis")
    counts = {}
    for name in VARIABLES:
        values = hourly.get(name + "_previous_day" + str(task["lead_days"]))
        if not isinstance(values, list) or len(values) != len(times):
            raise ValueError("Malformed array: " + name)
        counts[name] = sum(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values)
    return {"expected_hours": len(times), "non_null_hours": counts,
            "state": "complete" if all(n == len(times) for n in counts.values()) else "partial" if any(counts.values()) else "unavailable"}

def quota_wait(calls, cost, now):
    wait = 0
    for window, limit in ((60, 400), (3600, 4000), (86400, 8500)):
        rows = sorted((t, c) for t, c in calls if t > now-window)
        total = sum(c for t,c in rows)
        for t,c in rows:
            if total + cost <= limit: break
            total -= c
            wait = max(wait, t+window-now+1)
    return wait

def lock_file(path):
    f = path.open("a+b")
    f.seek(0); f.write(b"1"); f.flush(); f.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close(); raise RuntimeError("Collector already running for this output folder")
    return f

def run(args):
    global STOP
    STOP = False
    output = Path(args.output).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    lock = lock_file(output / "collector.lock")
    try:
        locations = json.loads((ROOT / "locations.json").read_text(encoding="utf-8-sig"))
        state_path = output / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"tasks": {}, "calls": [], "blocked_until": 0}
        atomic(output / "locations.json", locations)
        items = list(tasks(locations, args.start, args.end))
        def report(status):
            counts = {}
            for t in items:
                s = state["tasks"].get(task_id(t), {}).get("state", "pending")
                counts[s] = counts.get(s, 0) + 1
            atomic(output / "status.json", {"updated_at_utc": datetime.now(UTC).isoformat(), "state": status,
                "start_kst": str(args.start), "end_kst": str(args.end), "coordinates": len(locations),
                "resource_members": sum(len(l["members"]) for l in locations), "tasks": counts,
                "note": "partial/unavailable are saved responses, not complete training data"})
        successes = 0
        while not STOP:
            attempted = False
            for task in items:
                if STOP: break
                key = task_id(task)
                previous = state["tasks"].get(key, {})
                if previous.get("state") in ("complete", "partial", "unavailable"): continue
                if previous.get("retry_at", 0) > time.time(): continue
                cost = math.ceil((date.fromisoformat(task["end_date"])-date.fromisoformat(task["start_date"])).days/14 + 1/14)*2
                state["calls"] = [[t,c] for t,c in state["calls"] if t > time.time()-86400]
                wait = max(quota_wait(state["calls"], cost, time.time()), state.get("blocked_until", 0)-time.time())
                if wait > 0:
                    report("waiting_for_quota")
                    for _ in range(min(30, math.ceil(wait))):
                        if STOP: break
                        time.sleep(1)
                    attempted = True
                    break
                loc = task["location"]
                params = {"latitude": loc["latitude"], "longitude": loc["longitude"], "models": task["model"],
                    "start_date": task["start_date"], "end_date": task["end_date"], "timezone": "Asia/Seoul",
                    "timeformat": "unixtime", "wind_speed_unit": "ms",
                    "hourly": ",".join(v+"_previous_day"+str(task["lead_days"]) for v in VARIABLES)}
                requested = datetime.now(UTC).isoformat()
                state["calls"].append([time.time(),cost])
                atomic(state_path, state)
                attempted = True
                try:
                    with urlopen(ENDPOINT+"?"+urlencode(params), timeout=90) as response:
                        body = response.read()
                    coverage = validate(body, task)
                    received = datetime.now(UTC).isoformat()
                    envelope = {"format_version": 1, "task_id": key, "task": task, "params": params,
                        "endpoint": ENDPOINT, "variables": VARIABLES, "requested_at_utc": requested,
                        "received_at_utc": received, "body_sha256": hashlib.sha256(body).hexdigest(),
                        "body_base64": base64.b64encode(body).decode(), "coverage": coverage,
                        "time_contract": "fixed 24h/48h lead archive; not a fixed daily issue; historical download does not prove past availability"}
                    folder = output / "responses" / task["start_date"][:7]
                    folder.mkdir(parents=True, exist_ok=True)
                    destination = folder / (key+".json.gz")
                    tmp = destination.with_suffix(".tmp")
                    tmp.write_bytes(gzip.compress(json.dumps(envelope, ensure_ascii=False).encode(), mtime=0))
                    os.replace(tmp,destination)
                    state["tasks"][key] = {"state": coverage["state"], "file": str(destination.relative_to(output)), "received_at_utc": received}
                    successes += 1
                except Exception as exc:
                    code = exc.code if isinstance(exc,HTTPError) else None
                    delay = 3600 if code == 429 else 900
                    if code == 429: state["blocked_until"] = time.time()+delay
                    state["tasks"][key] = {"state":"error", "error":type(exc).__name__, "http_status":code, "retry_at":time.time()+delay}
                atomic(state_path,state)
                report("collecting")
                if args.max_requests and successes >= args.max_requests: return
                for _ in range(2):
                    if STOP: break
                    time.sleep(1)
            else:
                if all(state["tasks"].get(task_id(t),{}).get("state") in ("complete","partial","unavailable") for t in items):
                    report("first_pass_finished"); return
            if not attempted:
                report("waiting_for_retry")
                for _ in range(30):
                    if STOP: break
                    time.sleep(1)
        report("stopped")
    finally:
        lock.close()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", required=True)
    p.add_argument("--start",type=date.fromisoformat,default=date(2022,1,1))
    p.add_argument("--end",type=date.fromisoformat,default=date(2026,9,30))
    p.add_argument("--max-requests",type=int,default=0)
    args=p.parse_args()
    if args.start>args.end or args.max_requests<0: p.error("Invalid range")
    def stop(*_):
        global STOP
        STOP=True
    signal.signal(signal.SIGINT,stop)
    signal.signal(signal.SIGTERM,stop)
    run(args)

if __name__=="__main__": main()
