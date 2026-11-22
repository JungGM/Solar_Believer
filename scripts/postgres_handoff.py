"""Export/restore a portable PostgreSQL backup and collector continuation state."""

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from scripts.postgres_store import connect_postgres, persist_runtime_state, restore_runtime_state
from backend.app.config import get_settings

ROOT = Path(__file__).resolve().parents[1]


def snapshot_counts():
    with connect_postgres() as conn:
        observations = conn.execute(
            "SELECT source,count(*) FROM public.observations GROUP BY source ORDER BY source"
        ).fetchall()
        datasets = conn.execute(
            "SELECT dataset,count(*) FROM public.source_dataset_rows GROUP BY dataset ORDER BY dataset"
        ).fetchall()
        grids = conn.execute("SELECT count(*) FROM public.weather_forecast_grids").fetchone()[0]
        size = conn.execute("SELECT pg_database_size(current_database())").fetchone()[0]
    return dict(
        observations=dict(observations),
        source_datasets=dict(datasets),
        forecast_grids=grids,
        database_bytes=size,
    )


def persist_existing_state():
    for folder in (ROOT / "data/continuous_collection", ROOT / "data/kma_hub", ROOT / "data/source_catalogs"):
        for path in folder.glob("*.json"):
            if (
                path.name.endswith("queue.json")
                or path.name.endswith("status.json")
                or folder.name == "source_catalogs"
            ):
                persist_runtime_state(path, json.loads(path.read_text(encoding="utf-8-sig")))


def export_backup(output, chunk_mb):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    persist_existing_state()
    config = get_settings()
    dump = output / "postgres.dump"
    command = [
        "docker",
        "compose",
        "exec",
        "-T",
        "db",
        "pg_dump",
        "-U",
        config.postgres_user,
        "-d",
        config.postgres_db,
        "-Fc",
        "-Z6",
        "--no-owner",
        "--no-acl",
        "--exclude-schema=tcol_*",
    ]
    with dump.open("wb") as stream:
        subprocess.run(command, cwd=ROOT, stdout=stream, check=True)
    pieces = []
    digest = hashlib.sha256()
    total = 0
    with dump.open("rb") as stream:
        index = 0
        while block := stream.read(chunk_mb * 1024 * 1024):
            path = output / f"postgres.dump.part{index:03d}"
            path.write_bytes(block)
            digest.update(block)
            total += len(block)
            pieces.append(dict(file=path.name, bytes=len(block), sha256=hashlib.sha256(block).hexdigest()))
            index += 1
    manifest = dict(
        format="PostgreSQL custom archive",
        created_at_utc=datetime.now(timezone.utc).isoformat(),
        database=config.postgres_db,
        bytes=total,
        sha256=digest.hexdigest(),
        parts=pieces,
        counts=snapshot_counts(),
        collector_state_included=True,
        sqlite_runtime=False,
        note="API credentials are configured locally in .env; no API keys are needed to restore the database.",
    )
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {"manifest": str(output / "manifest.json"), "bytes": total, "parts": len(pieces)},
            ensure_ascii=False,
        ),
        flush=True,
    )
    return manifest


def restore_backup(manifest_path, state_output=None):
    manifest_path = manifest_path.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    with connect_postgres() as conn:
        existing = conn.execute(
            "SELECT count(*) FROM pg_tables WHERE schemaname='public' OR schemaname LIKE 'collector_%'"
        ).fetchone()[0]
    if existing:
        raise ValueError("Restore requires a new empty database. Existing tables were preserved.")
    config = get_settings()
    dump = manifest_path.parent / "postgres.restore.dump"
    digest = hashlib.sha256()
    with dump.open("wb") as stream:
        for piece in manifest["parts"]:
            path = (manifest_path.parent / piece["file"]).resolve()
            if not path.is_relative_to(manifest_path.parent):
                raise ValueError("Invalid backup part path")
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != piece["sha256"]:
                raise ValueError("Backup part checksum mismatch")
            digest.update(data)
            stream.write(data)
    if digest.hexdigest() != manifest["sha256"]:
        raise ValueError("Backup checksum mismatch")
    command = [
        "docker",
        "compose",
        "exec",
        "-T",
        "db",
        "pg_restore",
        "-U",
        config.postgres_user,
        "-d",
        config.postgres_db,
        "--no-owner",
        "--no-acl",
        "--exit-on-error",
    ]
    with dump.open("rb") as stream:
        subprocess.run(command, cwd=ROOT, stdin=stream, check=True)
    actual = snapshot_counts()
    for key in ("observations", "source_datasets", "forecast_grids"):
        if actual[key] != manifest["counts"][key]:
            raise ValueError("Restored row counts differ: " + key)
    restored = restore_runtime_state(state_output)
    print(
        json.dumps(
            {"restored": "PostgreSQL", "verified_counts": actual, "collector_state_files": restored},
            ensure_ascii=False,
        ),
        flush=True,
    )
    return actual


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--chunk-mb", type=int, default=40)
    restore = commands.add_parser("restore")
    restore.add_argument("manifest", type=Path)
    restore.add_argument("--state-output", type=Path)
    args = parser.parse_args()
    if args.command == "export":
        if not 1 <= args.chunk_mb <= 90:
            parser.error("Chunk size must be 1..90 MB")
        export_backup(args.output, args.chunk_mb)
    else:
        restore_backup(args.manifest, args.state_output)


if __name__ == "__main__":
    main()
