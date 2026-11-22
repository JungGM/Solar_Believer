"""All published KOMIPO solar codes, refreshed from the official code table."""

import hashlib
import io
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET

import requests
from scripts.postgres_store import open_store, store_exists

from scripts.collect_public_history import KOMIPO_GUIDE_URL, KOMIPO_RESOURCES, ROOT, dump, now


def parse_code_table(raw):
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    resources, storage = set(), set()
    for table in root.findall(".//w:tbl", ns):
        rows = [
            [
                "".join(node.text or "" for node in cell.findall(".//w:t", ns)).strip()
                for cell in row.findall("w:tc", ns)
            ]
            for row in table.findall("w:tr", ns)
        ]
        if not rows or not any("설비 코드" in cell for cell in rows[0]):
            continue
        code = None
        for cells in rows[1:]:
            if len(cells) < 3:
                continue
            if cells[0]:
                code = cells[0]
            unit = cells[2]
            if not code or not re.fullmatch(r"[A-Z0-9]{4}", code):
                continue
            if re.fullmatch(r"\d+", unit):
                resources.add((code, unit.zfill(3)))
            elif "ESS" in unit.upper():
                storage.add((code, unit))
    if not resources:
        raise ValueError("Official solar code table was not found")
    return sorted(resources), sorted(storage)


def all_komipo_resources(history_output, live_output, *, refresh=True):
    """Union the entire official roster with every code actually observed."""
    directory = ROOT / "data/source_catalogs"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "komipo.json"
    previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    resources = {tuple(row) for row in previous.get("resources", KOMIPO_RESOURCES)}
    metadata = dict(
        previous,
        source_url=KOMIPO_GUIDE_URL,
        resource_count_limit=None,
        scope="all published solar units plus all observed source codes; ESS kept separately",
    )
    fetched = previous.get("fetched_at_utc")
    fresh = fetched and (datetime.now(timezone.utc) - datetime.fromisoformat(fetched)).total_seconds() < 86400
    if refresh and not fresh:
        try:
            response = requests.get(KOMIPO_GUIDE_URL, timeout=(10, 30))
            response.raise_for_status()
            published, storage = parse_code_table(response.content)
            resources.update(published)
            (directory / "komipo_guide.docx").write_bytes(response.content)
            metadata.update(
                fetched_at_utc=now(),
                sha256=hashlib.sha256(response.content).hexdigest(),
                published_solar_units=len(published),
                storage_units=storage,
                refresh_error=None,
            )
        except (requests.RequestException, ValueError, zipfile.BadZipFile, ET.ParseError, KeyError) as exc:
            # Keep the entire last verified official list, without fabricating
            # unlisted codes or truncating it to a requested number of plants.
            metadata.update(refresh_error=type(exc).__name__, refresh_attempt_at_utc=now())
    for output in (Path(history_output), Path(live_output)):
        checkpoint = output / "komipo"
        if not store_exists(checkpoint):
            continue
        with open_store(checkpoint) as conn:
            for (resource,) in conn.execute("SELECT DISTINCT resource FROM records"):
                code, unit = resource.split(":", 1)
                resources.add((code, unit.zfill(3)))
    metadata.update(resources=sorted(resources), discovered_solar_units=len(resources), updated_at_utc=now())
    dump(path, metadata)
    return [list(row) for row in sorted(resources)]
