"""Read legacy archives once and move all received data into PostgreSQL."""

import csv
import json
import sqlite3
from pathlib import Path

from psycopg import sql

from scripts.postgres_store import open_store, connect_postgres
from scripts.postgres_ingest import ensure_service_tables, ingest_daily_records, ingest_forecast_grid

ROOT = Path(__file__).resolve().parents[1]


def emit(**values):
    print(json.dumps(values, ensure_ascii=False), flush=True)


def finished(conn, namespace, name):
    return conn.execute(
        "SELECT row_count FROM public.collector_migrations WHERE namespace=%s AND object_name=%s",
        (namespace, name),
    ).fetchone()


def mark(conn, namespace, name, count):
    conn.execute(
        """INSERT INTO public.collector_migrations(namespace,object_name,row_count) VALUES(%s,%s,%s)
        ON CONFLICT(namespace,object_name) DO UPDATE SET row_count=excluded.row_count,migrated_at=now()""",
        (namespace, name, count),
    )


def transfer_table(old, store, table):
    conn = store.connection
    if finished(conn, store.schema, table):
        return
    cols = [r[1] for r in old.execute(f'PRAGMA table_info("{table}")')]
    query = f'SELECT * FROM "{table}"'
    if table == "calls" and cols == ["at", "day"]:
        cols = ["id", *cols]
        query = "SELECT rowid,at,day FROM calls"
    names = sql.SQL(",").join(map(sql.Identifier, cols))
    target = sql.Identifier(store.schema, table)
    conn.execute(
        sql.SQL("CREATE TEMP TABLE migration_stage AS SELECT {} FROM {} WITH NO DATA").format(names, target)
    )
    cursor = old.execute(query)
    count = 0
    while rows := cursor.fetchmany(5000):
        conn.execute("TRUNCATE pg_temp.migration_stage")
        with conn.cursor().copy(
            sql.SQL("COPY pg_temp.migration_stage ({}) FROM STDIN").format(names)
        ) as copy:
            for row in rows:
                copy.write_row(row)
        if table == "revisions":
            equal = sql.SQL(" AND ").join(
                sql.SQL("t.{} IS NOT DISTINCT FROM s.{}").format(sql.Identifier(c), sql.Identifier(c))
                for c in cols
            )
            conn.execute(
                sql.SQL(
                    "INSERT INTO {} ({}) SELECT {} FROM pg_temp.migration_stage s WHERE NOT EXISTS(SELECT 1 FROM {} t WHERE {})"
                ).format(
                    target,
                    names,
                    sql.SQL(",").join(sql.SQL("s.{}").format(sql.Identifier(c)) for c in cols),
                    target,
                    equal,
                )
            )
        else:
            conn.execute(
                sql.SQL(
                    "INSERT INTO {} ({}) SELECT {} FROM pg_temp.migration_stage ON CONFLICT DO NOTHING"
                ).format(target, names, names)
            )
        count += len(rows)
        conn.commit()
    conn.execute("DROP TABLE pg_temp.migration_stage")
    if table == "calls" and "id" in cols and store.kind != "usage":
        sequence = conn.execute(
            "SELECT pg_get_serial_sequence(%s,'id')", (f"{store.schema}.calls",)
        ).fetchone()[0]
        maximum = conn.execute(sql.SQL("SELECT coalesce(max(id),0) FROM {}").format(target)).fetchone()[0]
        if maximum:
            conn.execute("SELECT setval(%s,%s)", (sequence, maximum))
    actual = conn.execute(sql.SQL("SELECT count(*) FROM {}").format(target)).fetchone()[0]
    if actual != count:
        raise ValueError(f"Count mismatch: {store.schema}.{table}: {actual} != {count}")
    mark(conn, store.schema, table, count)
    conn.commit()
    emit(stage="legacy_table", namespace=store.schema, table=table, rows=count)


def transfer_raw(store, old, kind):
    conn = store.connection
    if finished(conn, store.schema, "raw_responses"):
        return
    count = 0
    for path in store.directory.rglob("*.gz"):
        if "responses" not in path.parts and "raw" not in path.relative_to(store.directory).parts:
            continue
        meta_path = path.with_suffix(".json")
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        relative = path.relative_to(store.directory)
        blob = path.read_bytes()
        store.store_raw(relative, blob, meta)
        if kind == "kma" and "forecast" in relative.parts and meta.get("params"):
            params = meta["params"]
            task_id = f"forecast:{params['tmfc']}:{params['tmef']}:{params['vars']}"
            ingest_forecast_grid(conn, task_id, params, blob, meta)
        count += 1
        if count % 100 == 0:
            conn.commit()
            if count % 1000 == 0:
                emit(stage="raw_responses", namespace=store.schema, rows=count)
    mark(conn, store.schema, "raw_responses", count)
    conn.commit()
    emit(stage="raw_complete", namespace=store.schema, rows=count)


def project_records(store, source):
    if source not in ("koen", "kospo", "komipo", "iwest", "smp", "forecast"):
        return
    conn = store.connection
    if finished(conn, store.schema, "service_observations"):
        return
    # Read through a second connection so COPY/commit does not invalidate the reader.
    reader = open_store(store.directory)
    cursor = reader.connection.cursor(name="project_received_records")
    cursor.execute("SELECT day,resource,payload,fetched,request_id FROM records ORDER BY day,resource")
    count = 0
    while rows := cursor.fetchmany(1000):
        count += ingest_daily_records(conn, source, rows)
        conn.commit()
        emit(stage="service_observations", source=source, namespace=store.schema, hourly_rows=count)
    cursor.close()
    reader.close()
    mark(conn, store.schema, "service_observations", count)
    conn.commit()


def supplementary_csvs():
    with connect_postgres() as conn:
        ensure_service_tables(conn)
        conn.execute("""CREATE TABLE IF NOT EXISTS public.source_dataset_rows(
            dataset TEXT,row_number BIGINT,payload JSONB NOT NULL,PRIMARY KEY(dataset,row_number))""")
        conn.commit()
        paths = list((ROOT / "data/raw").glob("*.csv")) + list(
            (ROOT / "data/api_history_20220101_20261031/supplementary_official_files").glob("*.csv")
        )
        for path in paths:
            dataset = path.relative_to(ROOT / "data").as_posix()
            if finished(conn, "source_datasets", dataset):
                continue
            conn.execute(
                "CREATE TEMP TABLE source_rows_stage(dataset TEXT,row_number BIGINT,payload JSONB) ON COMMIT DELETE ROWS"
            )
            count = 0
            with path.open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                while True:
                    rows = []
                    for _ in range(5000):
                        try:
                            row = next(reader)
                        except StopIteration:
                            break
                        count += 1
                        rows.append((dataset, count, json.dumps(row, ensure_ascii=False)))
                    if not rows:
                        break
                    with conn.cursor().copy("COPY pg_temp.source_rows_stage FROM STDIN") as copy:
                        for row in rows:
                            copy.write_row(row)
                    conn.execute(
                        "INSERT INTO public.source_dataset_rows SELECT * FROM pg_temp.source_rows_stage ON CONFLICT DO NOTHING"
                    )
                    conn.commit()
                    if count % 100000 == 0:
                        emit(stage="source_csv", dataset=dataset, rows=count)
            conn.execute("DROP TABLE pg_temp.source_rows_stage")
            mark(conn, "source_datasets", dataset, count)
            conn.commit()
            emit(stage="source_csv_complete", dataset=dataset, rows=count)


def known_plant_metadata():
    path = ROOT / "data/raw/koen_plants_meta.csv"
    if not path.exists():
        return
    with connect_postgres() as conn, path.open(encoding="utf-8-sig", newline="") as stream:
        count = 0
        for row in csv.DictReader(stream):
            conn.execute(
                """INSERT INTO public.plants(id,name,capacity_kw,latitude,longitude,
                smp_zone,generation_source,is_demo) VALUES(%s,%s,%s,%s,%s,%s,%s,false)
                ON CONFLICT(id) DO NOTHING""",
                (
                    row["plant_id"],
                    row["plant_name"],
                    row["capacity_kw"],
                    float(row["latitude"]),
                    float(row["longitude"]),
                    row["smp_zone"],
                    row["generation_source"],
                ),
            )
            count += 1
        emit(stage="known_plant_metadata", rows=count)


def main():
    paths = sorted((ROOT / "data").rglob("checkpoint.sqlite"))
    usage = ROOT / "data/public_api_usage.sqlite"
    if usage.exists():
        paths.insert(0, usage)
    for path in paths:
        kind = "usage" if path == usage else "kma" if path.parent.name == "kma_hub" else "public"
        directory = path.with_suffix("") if kind == "usage" else path.parent
        store = open_store(directory, kind)
        ensure_service_tables(store.connection)
        store.commit()
        old = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            tables = [
                r[0]
                for r in old.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            ]
            for table in tables:
                transfer_table(old, store, table)
            transfer_raw(store, old, kind)
            if kind == "public":
                project_records(store, path.parent.name)
        finally:
            old.close()
            store.close()
    supplementary_csvs()
    known_plant_metadata()
    emit(stage="complete", database="PostgreSQL")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
