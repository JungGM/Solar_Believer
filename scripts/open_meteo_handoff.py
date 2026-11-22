"""Move only Open-Meteo data/checkpoints between PCs; never replace an existing DB."""

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from backend.app.config import get_settings
from scripts.collect_open_meteo import ROOT
from scripts.postgres_store import connect_postgres

TABLES = ('open_meteo_locations', 'open_meteo_tasks', 'open_meteo_calls',
          'open_meteo_requests', 'open_meteo_hourly', 'open_meteo_state')


def counts():
    with connect_postgres() as conn:
        return {name: conn.execute('SELECT count(*) FROM public.' + name).fetchone()[0] for name in TABLES}


def export(output):
    config = get_settings()
    output.mkdir(parents=True, exist_ok=True)
    with connect_postgres() as conn:
        # An active collector makes a continuation snapshot ambiguous.
        if not conn.execute("SELECT pg_try_advisory_lock(hashtextextended('public:open_meteo_collector',0))").fetchone()[0]:
            raise ValueError('Stop the Open-Meteo collector before exporting')
        command = ['docker', 'compose', 'exec', '-T', 'db', 'pg_dump', '-U', config.postgres_user,
                   '-d', config.postgres_db, '-Fc', '-Z6', '--no-owner', '--no-acl',
                   '--table=public.open_meteo_*']
        archive = output / 'open_meteo.dump'
        if archive.exists():
            raise ValueError('Choose a new export directory; existing backup was preserved')
        with archive.open('wb') as stream:
            subprocess.run(command, cwd=ROOT, stdout=stream, check=True)
        result = {'created_at_utc': datetime.now(timezone.utc).isoformat(), 'file': archive.name,
                  'bytes': archive.stat().st_size, 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                  'tables': counts(), 'scope': 'Open-Meteo only; generation/SMP/credentials are excluded',
                  'restore_rule': 'Existing public.open_meteo_* tables must be absent; never delete destination tables.'}
        (output / 'manifest.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


def restore(manifest_path):
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    archive = (manifest_path.parent / manifest['file']).resolve()
    if archive.parent != manifest_path.parent.resolve():
        raise ValueError('Invalid backup file path')
    if (archive.stat().st_size != manifest['bytes'] or
            hashlib.sha256(archive.read_bytes()).hexdigest() != manifest['sha256']):
        raise ValueError('Backup checksum/size mismatch')
    if set(manifest['tables']) != set(TABLES):
        raise ValueError('Unexpected handoff table list')
    with connect_postgres() as conn:
        if conn.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public' AND left(tablename,11)='open_meteo_'").fetchone()[0]:
            raise ValueError('Destination already has Open-Meteo tables; existing data was preserved')
        if not conn.execute("SELECT to_regclass('public.observations')").fetchone()[0]:
            raise ValueError('Run the project database migrations before restoring')
        config = get_settings()
        command = ['docker', 'compose', 'exec', '-T', 'db', 'pg_restore', '-U', config.postgres_user,
                   '-d', config.postgres_db, '--no-owner', '--no-acl', '--exit-on-error', '--single-transaction']
        with archive.open('rb') as stream:
            subprocess.run(command, cwd=ROOT, stdin=stream, check=True)
    actual = counts()
    if actual != manifest['tables']:
        raise ValueError('Restored Open-Meteo counts differ from the manifest')
    return {'restored': actual, 'sha256_verified': True}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest='command', required=True)
    commands.add_parser('export').add_argument('--output', type=Path, required=True)
    commands.add_parser('restore').add_argument('manifest', type=Path)
    args = p.parse_args()
    result = export(args.output) if args.command == 'export' else restore(args.manifest)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
