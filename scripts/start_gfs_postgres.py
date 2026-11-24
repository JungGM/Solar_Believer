"""Start an isolated local PostgreSQL for GFS without changing the project's DB."""
import os
from pathlib import Path
import subprocess
import zipfile

import psycopg
import requests

from backend.app.config import get_settings

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = Path.home() / ".cache" / "solar-gfs-postgres"
PORT = 55433


def main():
    settings = get_settings()
    binary = RUNTIME / "pgsql" / "bin"
    cluster = RUNTIME / "cluster"
    RUNTIME.mkdir(parents=True, exist_ok=True)
    if not (binary / "initdb.exe").exists():
        archive = ROOT / "data" / "noaa_gfs" / "runtime" / "postgresql.zip"
        if not archive.exists():
            archive = RUNTIME / "postgresql.zip"
            url = "https://get.enterprisedb.com/postgresql/postgresql-17.7-1-windows-x64-binaries.zip"
            with requests.get(url, stream=True, timeout=(20, 120)) as response:
                response.raise_for_status()
                with archive.open("wb") as output:
                    for chunk in response.iter_content(1024 * 1024):
                        output.write(chunk)
        with zipfile.ZipFile(archive) as source:
            members = [n for n in source.namelist() if n.startswith(("pgsql/bin/", "pgsql/lib/", "pgsql/share/"))]
            source.extractall(RUNTIME, members)
    env = os.environ.copy()
    env["PGPASSWORD"] = settings.postgres_password
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    if not (cluster / "PG_VERSION").exists():
        password_file = RUNTIME / "init.password"
        password_file.write_text(settings.postgres_password + "\n", encoding="utf-8")
        try:
            subprocess.run([str(binary / "initdb.exe"), "-D", str(cluster), "-U", settings.postgres_user,
                            "--pwfile", str(password_file), "-A", "scram-sha-256", "--encoding=UTF8",
                            "--locale=C"], check=True, env=env, creationflags=flags)
        finally:
            password_file.unlink(missing_ok=True)
    ready = subprocess.run([str(binary / "pg_isready.exe"), "-h", "127.0.0.1", "-p", str(PORT)],
                           env=env, creationflags=flags, capture_output=True)
    if ready.returncode:
        subprocess.run([str(binary / "pg_ctl.exe"), "-D", str(cluster), "-l", str(RUNTIME / "postgres.log"),
                        "-o", f'-p {PORT} -h 127.0.0.1', "-w", "start"],
                       check=True, env=env, creationflags=flags)
    with psycopg.connect(host="127.0.0.1", port=PORT, dbname="postgres", user=settings.postgres_user,
                         password=settings.postgres_password, autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname='solar_gfs'").fetchone():
            conn.execute('CREATE DATABASE solar_gfs')
    print(f"Isolated GFS PostgreSQL ready on 127.0.0.1:{PORT}/solar_gfs")


if __name__ == "__main__":
    main()
