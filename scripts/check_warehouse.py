"""Smoke-checks the Postgres warehouse: connect, run SELECT 1, report the version.

Run from the repo root after the container is healthy:

    docker compose --env-file .env -f warehouse/docker-compose.yml up -d --wait
    python scripts/check_warehouse.py

Connection settings come from environment variables, falling back to the
gitignored .env at the repo root (see .env.example). Exits non-zero with a
readable message if a setting is missing or the connection fails.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg

REPO_ROOT = Path(__file__).resolve().parent.parent
REQUIRED = ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB")


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
    return 0


if __name__ == "__main__":
    sys.exit(main())
