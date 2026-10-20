"""Smoke-checks the Postgres warehouse: connect, run SELECT 1, report the version.

Run from the repo root after the container is healthy:

    docker compose --env-file .env -f warehouse/docker-compose.yml up -d --wait
    python scripts/check_warehouse.py                # smoke check only
    python scripts/check_warehouse.py --persistence  # also restart and check data survives

The persistence check writes one row, restarts the container with `down` then
`up` (never `down -v`, so the named volume is kept), and confirms the row is
still there. That is what proves the volume mount actually holds the data
directory. It always drops its table afterwards.

Connection settings come from environment variables, falling back to the
gitignored .env at the repo root (see .env.example). Exits non-zero with a
readable message if a setting is missing or the connection fails.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import psycopg

REPO_ROOT = Path(__file__).resolve().parent.parent
REQUIRED = ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB")
COMPOSE = [
    "docker", "compose",
    "--env-file", str(REPO_ROOT / ".env"),
    "-f", str(REPO_ROOT / "warehouse" / "docker-compose.yml"),
]
PERSISTENCE_TABLE = "aap_persistence_check"
VOLUME_TARGET = "/var/lib/postgresql"
"""Where docker-compose.yml mounts the named volume."""


def load_env(path: Path) -> dict[str, str]:
    """Read KEY=VALUE lines from a .env file; real environment variables win.

    Deliberately minimal (no quoting or interpolation rules): .env.example
    only uses plain KEY=VALUE lines, and a dependency for this alone isn't
    worth it.
    """
    values: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
    return {**values, **{k: v for k, v in os.environ.items() if k.startswith("POSTGRES_")}}


def compose(*args: str) -> str:
    """Run a docker compose command for the warehouse; raise with its output if it fails."""
    result = subprocess.run([*COMPOSE, *args], cwd=REPO_ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"docker compose {' '.join(args)} failed:\n{result.stdout}{result.stderr}")
    return result.stdout.strip()


def check_persistence(conninfo: dict) -> bool:
    """Write a row, restart the container, and confirm the row survived.

    Returns:
        True if the row read back after the restart is the one written.
    """
    pgdata = compose("exec", "-T", "postgres", "printenv", "PGDATA")
    print(f"PGDATA inside the container: {pgdata} (volume mounted at {VOLUME_TARGET})")
    if not pgdata.startswith(VOLUME_TARGET + "/"):
        print("FAIL: the data directory is not inside the mounted volume")
        return False

    marker = f"written {datetime.now(UTC).isoformat()}"
    with psycopg.connect(**conninfo) as conn:
        conn.execute(f"DROP TABLE IF EXISTS {PERSISTENCE_TABLE}")
        conn.execute(f"CREATE TABLE {PERSISTENCE_TABLE} (marker text NOT NULL)")
        conn.execute(f"INSERT INTO {PERSISTENCE_TABLE} (marker) VALUES (%s)", (marker,))
    print(f"wrote 1 row to {PERSISTENCE_TABLE}: {marker!r}")

    try:
        compose("down")  # no -v: the named volume must survive
        print("container removed (docker compose down, volume kept)")
        compose("up", "-d", "--wait")
        print("container recreated and healthy (docker compose up -d --wait)")
        with psycopg.connect(**conninfo) as conn:
            rows = conn.execute(f"SELECT marker FROM {PERSISTENCE_TABLE}").fetchall()
    finally:
        with psycopg.connect(**conninfo) as conn:
            conn.execute(f"DROP TABLE IF EXISTS {PERSISTENCE_TABLE}")
        print(f"dropped {PERSISTENCE_TABLE}")

    if rows != [(marker,)]:
        print(f"FAIL: after restart the table holds {rows!r}, expected [({marker!r},)]")
        return False
    print("row survived the restart: the volume keeps the data")
    return True


def main() -> int:
    env = load_env(REPO_ROOT / ".env")
    missing = [key for key in REQUIRED if not env.get(key)]
    if missing:
        print(f"Missing {', '.join(missing)}: copy .env.example to .env and fill it in.")
        return 1

    conninfo = {
        "host": env.get("POSTGRES_HOST", "localhost"),
        "port": env.get("POSTGRES_PORT", "5433"),
        "user": env["POSTGRES_USER"],
        "password": env["POSTGRES_PASSWORD"],
        "dbname": env["POSTGRES_DB"],
        "connect_timeout": 5,
    }
    try:
        with psycopg.connect(**conninfo) as conn:
            one = conn.execute("SELECT 1").fetchone()[0]
            version = conn.execute("SHOW server_version").fetchone()[0]
            full = conn.execute("SELECT version()").fetchone()[0]
    except psycopg.OperationalError as exc:
        print(f"Could not connect to {conninfo['host']}:{conninfo['port']}/{conninfo['dbname']}: {exc}")
        return 1

    if one != 1:
        print(f"SELECT 1 returned {one!r}")
        return 1
    print(f"connected to {conninfo['host']}:{conninfo['port']}/{conninfo['dbname']} as {conninfo['user']}")
    print("SELECT 1 -> 1")
    print(f"server_version: {version}")
    print(f"version(): {full}")

    if "--persistence" in sys.argv[1:]:
        print()
        if not check_persistence(conninfo):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
