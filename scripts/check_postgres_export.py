"""Check exportable HTTP payloads for the locally configured API credentials."""

import gzip
import json
from pathlib import Path
from urllib.parse import quote, unquote

from psycopg import sql

from backend.app.config import get_settings
from scripts.postgres_store import connect_postgres


def main():
    settings = get_settings()
    secrets = set()
    for value in (settings.data_go_kr_api_key, settings.kma_apihub_key):
        if value:
            secrets.update(v.encode() for v in (value, unquote(value), quote(unquote(value), safe="")))
    examined = 0
    found = 0
    with connect_postgres() as conn:
        schemas = [
            r[0]
            for r in conn.execute(
                "SELECT schemaname FROM pg_tables WHERE tablename='raw_responses' AND schemaname LIKE 'collector_%'"
            )
        ]
        for schema in schemas:
            with conn.cursor(name="export_key_scan") as cursor:
                cursor.execute(
                    sql.SQL("SELECT body_gzip,metadata FROM {}.raw_responses").format(sql.Identifier(schema))
                )
                while rows := cursor.fetchmany(25):
                    for compressed, metadata in rows:
                        raw = gzip.decompress(bytes(compressed))
                        encoded = json.dumps(metadata, ensure_ascii=False).encode()
                        found += int(any(secret in raw or secret in encoded for secret in secrets))
                        examined += 1
    report = {"checked_raw_responses": examined, "configured_credential_matches": found, "passed": found == 0}
    path = Path("docs/reports/postgres_export_check_20261122.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report), flush=True)
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
