"""Check the shared DB without API calls, database writes, or schema changes."""

import argparse
import getpass
import json
import os
from pathlib import Path
import socket
import sys


ROOT = Path(__file__).resolve().parent
TABLES = (
    "open_meteo_locations", "open_meteo_tasks", "open_meteo_calls",
    "open_meteo_requests", "open_meteo_hourly", "open_meteo_state",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "db_connection.example.json")
    parser.add_argument("--tcp-only", action="store_true", help="No Python packages or password needed")
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text(encoding="utf-8-sig"))
        target = {
            "host": config["host"], "port": int(config["port"]),
            "dbname": config["database"], "user": config["user"],
        }
        if (target["host"], target["port"], target["dbname"]) != (
            "100.102.164.7", 15432, "solar_believer"
        ) or not isinstance(target["user"], str) or not target["user"].strip():
            raise ValueError("Unexpected target")
    except (OSError, ValueError, KeyError, TypeError):
        parser.exit(2, "Invalid config or unexpected main DB target. No connection attempted.\n")

    try:
        with socket.create_connection((target["host"], target["port"]), timeout=5):
            pass
    except OSError:
        parser.exit(1, "TCP connection failed. Check Tailscale access and the main DB server.\n")
    if args.tcp_only:
        print(json.dumps({"tcp_connected": True, "target": "100.102.164.7:15432",
                          "authentication_checked": False}))
        return 0

    try:
        import psycopg
    except ImportError:
        parser.exit(2, "Install the DB driver once: py -3 -m pip install -r requirements-db.txt\n")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    if not password:
        if not sys.stdin.isatty():
            parser.exit(2, "Run interactively to enter the DB password, or set POSTGRES_PASSWORD locally.\n")
        try:
            password = getpass.getpass("DB password (hidden; never saved): ")
        except (EOFError, KeyboardInterrupt):
            parser.exit(2, "Password entry cancelled.\n")
    if not password:
        parser.exit(2, "A DB password is required.\n")
    try:
        with psycopg.connect(
            **target, password=password, connect_timeout=5,
            options="-c default_transaction_read_only=on -c statement_timeout=5000 "
                    "-c application_name=team-meteo-connection-check",
        ) as conn:
            database, user, read_only = conn.execute(
                "SELECT current_database(), current_user, current_setting('transaction_read_only')"
            ).fetchone()
            tables = {}
            for name in TABLES:
                relation = "public." + name
                exists = conn.execute("SELECT to_regclass(%s)::text", (relation,)).fetchone()[0]
                tables[relation] = {"exists": exists is not None}
                if exists:
                    select, insert, update = conn.execute(
                        "SELECT has_table_privilege(current_user,%s,'SELECT'), "
                        "has_table_privilege(current_user,%s,'INSERT'), "
                        "has_table_privilege(current_user,%s,'UPDATE')",
                        (relation, relation, relation),
                    ).fetchone()
                    tables[relation].update(select=select, insert=insert, update=update)
            # Always roll back the diagnostic transaction, even though it is read-only.
            conn.rollback()
    except psycopg.Error:
        # Never echo exception text, connection strings, or credentials.
        parser.exit(1, "Authentication/query failed. Check credentials, database access, and server availability.\n")
    print(json.dumps({
        "target": "100.102.164.7:15432", "authenticated": True,
        "database": database, "user": user, "transaction_read_only": read_only,
        "tables": tables,
        "note": "Read-only inspection only; collector writes, sequence permissions, and imports are not tested.",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
