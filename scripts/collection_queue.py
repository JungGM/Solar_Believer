"""Forward-only archive queue: save all pages, record failures, never repair gaps."""

import hashlib
import gzip
import json
import math
from datetime import date

from scripts.collect_public_history import APIError, ENDPOINTS, dump, now, parse_response

CONTROL_ERRORS = {"22", "LOCAL_DAILY_BUDGET", "CYCLE_BUDGET", "STOP_REQUESTED"}


def task_identity(params):
    return json.dumps(params, sort_keys=True)


def load_queue(path):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"policy": "skip_errors_and_gaps", "resource_count_limit": None, "tasks": {}}


def enqueue(queue, params, start, end, page_key=None, size_key=None, size=None, completed=False):
    key = task_identity(params)
    queue["tasks"].setdefault(
        key,
        dict(
            params=params,
            start=str(start),
            end=str(end),
            page_key=page_key,
            size_key=size_key,
            size=size,
            cursor=1,
            received=0,
            total=None,
            state="complete" if completed else "pending",
            skipped_pages=[],
        ),
    )
    task = queue["tasks"][key]
    if not completed and task["state"] == "complete" and task["received"] == 0:
        # Migrate a legacy zero-row response previously labelled complete.
        task["state"] = "pending"


def run_queue(job, queue, path):
    skipped = []
    waiting = None
    for task in queue["tasks"].values():
        if task["state"] != "pending":
            continue
        while task["state"] == "pending":
            params = dict(task["params"])
            if task["page_key"]:
                params.update({task["page_key"]: task["cursor"], task["size_key"]: task["size"]})
            try:
                # Requests that already failed before this policy change are
                # skipped too; successful cached pages remain usable.
                identity = hashlib.sha256(
                    json.dumps([ENDPOINTS[job.source], params], sort_keys=True).encode()
                ).hexdigest()[:24]
                prior = job.db.execute(
                    "SELECT status,metadata,path FROM requests WHERE id=?", (identity,)
                ).fetchone()
                if prior and prior[0] == "error":
                    error_code = json.loads(prior[1]).get("error_code")
                    if error_code is None:
                        try:
                            with gzip.open(job.directory / prior[2], "rb") as body:
                                parse_response(body.read())
                        except APIError as exc:
                            error_code = exc.code
                        except (OSError, ValueError):
                            pass
                    if error_code not in {"22", "HTTP_429"}:
                        raise APIError(
                            "PREVIOUS_ERROR", "Previously failed request skipped without another API call"
                        )
                rows, total, _, rid, metadata = job.request(params)
                if task["total"] is None:
                    task["total"] = total
                fingerprint = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
                if rows and fingerprint == task.get("previous_fingerprint"):
                    raise APIError("PAGINATION_STALLED", "Repeated page skipped")
                task["previous_fingerprint"] = fingerprint
                if rows and not task.get("effective_size"):
                    task["effective_size"] = len(rows)
                job.save_records(
                    rows, rid, metadata, date.fromisoformat(task["start"]), date.fromisoformat(task["end"])
                )
                task["received"] += len(rows)
                if not rows:
                    task["state"] = "skipped" if task["received"] == 0 else "complete_with_gaps"
                    task["empty_response"] = True
                elif not task["page_key"]:
                    task["state"] = "complete" if total in (None, len(rows)) else "complete_with_gaps"
                elif total is None and len(rows) < task["size"]:
                    task["state"] = "complete"
            except APIError as exc:
                if exc.code in CONTROL_ERRORS:
                    waiting = {"code": exc.code, "message": exc.message}
                    break
                item = dict(
                    page=task["cursor"],
                    params=params,
                    code=exc.code,
                    message=exc.message,
                    skipped_at_utc=now(),
                )
                task["skipped_pages"].append(item)
                skipped.append(item)
                # With no advertised page count, the failed range is skipped;
                # later months/resources still run. A known next page proceeds.
                if task["total"] is None or not task["page_key"]:
                    task["state"] = "skipped"
            if task["state"] == "pending":
                if task["total"] is not None:
                    last = math.ceil(task["total"] / max(1, task.get("effective_size", task["size"] or 1)))
                    if task["cursor"] >= last:
                        task["state"] = "complete_with_gaps" if task["skipped_pages"] else "complete"
                task["cursor"] += 1
            task["updated_at_utc"] = now()
            dump(path, queue)
        if waiting:
            break
    queue["updated_at_utc"] = now()
    dump(path, queue)
    return dict(
        state="waiting"
        if waiting
        else "finished_with_skips"
        if any(t["state"] in {"skipped", "complete_with_gaps"} for t in queue["tasks"].values())
        else "caught_up",
        error=waiting,
        errors=skipped,
        pending_tasks=sum(t["state"] == "pending" for t in queue["tasks"].values()),
        completed_tasks=sum(t["state"] == "complete" for t in queue["tasks"].values()),
        skipped_tasks=sum(t["state"] in {"skipped", "complete_with_gaps"} for t in queue["tasks"].values()),
        skipped_pages=sum(len(t["skipped_pages"]) for t in queue["tasks"].values()),
    )
