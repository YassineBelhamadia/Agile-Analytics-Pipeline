"""Load the landed API pages into typed staging tables in Postgres.

Run from the repo root, with Postgres up and landing/ extracted:

    uv run --env-file .env python -m ingestion.load_staging [landing_dir] [--schema staging]

Inputs: landing_dir (default landing/), read through its manifest by
`ingestion.parse`, and the POSTGRES_* environment variables. This module
never reads .env itself; `uv run --env-file .env` puts them in the environment.

Outputs: one table per entity in the target schema (default `staging`),
each with typed columns, a primary key, `_source_file` and `_loaded_at`.
No foreign keys: relationships are checked by dbt tests later.

Each run is a full refresh in one transaction. Every page is parsed first,
then the tables are dropped, recreated and filled, then committed. Postgres
DDL is transactional, so a run that fails at any point leaves the previous
tables exactly as they were.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from psycopg import sql

from ingestion import parse
from ingestion.extract import DEFAULT_LANDING_DIR

DEFAULT_SCHEMA = "staging"
REQUIRED_ENV = ("POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB")
CONNECT_TIMEOUT_SECONDS = 10


class StagingError(RuntimeError):
    """The load cannot run as configured. Nothing was written."""


@dataclass(frozen=True)
class Table:
    """A staging table: its entity columns (lineage columns are added to all).

    Attributes:
        name: Table name within the target schema.
        columns: (column, Postgres type) pairs, in table order.
        primary_key: Primary key columns.
    """

    name: str
    columns: tuple[tuple[str, str], ...]
    primary_key: tuple[str, ...]

    @property
    def column_names(self) -> list[str]:
        return [name for name, _ in self.columns]


LINEAGE_COLUMNS = (("_source_file", "text not null"), ("_loaded_at", "timestamptz not null"))

TABLES = (
    Table("teams", (
        ("project_id", "bigint not null"),
        ("project_key", "text not null"),
        ("project_name", "text not null"),
    ), ("project_id",)),
    Table("users", (
        ("account_id", "text not null"),
        ("display_name", "text not null"),
    ), ("account_id",)),
    Table("boards", (
        ("board_id", "bigint not null"),
        ("board_name", "text not null"),
        ("board_type", "text not null"),
        ("project_id", "bigint not null"),
        ("project_key", "text not null"),
    ), ("board_id",)),
    Table("sprints", (
        ("sprint_id", "bigint not null"),
        ("board_id", "bigint not null"),
        ("sprint_name", "text not null"),
        ("state", "text not null"),
        ("start_at", "timestamptz"),
        ("end_at", "timestamptz"),
        ("complete_at", "timestamptz"),
    ), ("sprint_id",)),
    Table("capacity", (
        ("team_key", "text not null"),
        ("sprint_id", "bigint not null"),
        ("headcount", "integer not null"),
        ("pto_days", "double precision not null"),
    ), ("team_key", "sprint_id")),
)
"""Tables in load order. Sprint dates stay nullable: Jira's future sprints have none."""


def connect_from_environment() -> psycopg.Connection:
    """Open an autocommit connection from the POSTGRES_* variables.

    Autocommit means `load_staging`'s transaction block is a real
    BEGIN/COMMIT rather than a savepoint inside an implicit transaction.

    Raises:
        StagingError: Naming whichever variables are missing.
    """
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name, "").strip()]
    if missing:
        raise StagingError(
            f"Missing {', '.join(missing)}. Run with `uv run --env-file .env ...` "
            "(see .env.example), or export them."
        )
    return psycopg.connect(
        host=os.environ["POSTGRES_HOST"],
        port=os.environ["POSTGRES_PORT"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ["POSTGRES_DB"],
        connect_timeout=CONNECT_TIMEOUT_SECONDS,
        autocommit=True,
    )


def _replace_table(conn: psycopg.Connection, schema: str, table: Table, rows: list[dict], loaded_at: datetime) -> None:
    target = sql.Identifier(schema, table.name)
    definitions = [sql.SQL("{} {}").format(sql.Identifier(name), sql.SQL(kind)) for name, kind in table.columns + LINEAGE_COLUMNS]
    definitions.append(sql.SQL("primary key ({})").format(sql.SQL(", ").join(map(sql.Identifier, table.primary_key))))
    conn.execute(sql.SQL("drop table if exists {}").format(target))
    conn.execute(sql.SQL("create table {} ({})").format(target, sql.SQL(", ").join(definitions)))

    names = table.column_names + ["_source_file", "_loaded_at"]
    copy_statement = sql.SQL("copy {} ({}) from stdin").format(target, sql.SQL(", ").join(map(sql.Identifier, names)))
    with conn.cursor() as cursor, cursor.copy(copy_statement) as copy:
        for row in rows:
            copy.write_row([row[name] for name in table.column_names] + [row["_source_file"], loaded_at])


def load_staging(conn: psycopg.Connection, landing_dir: Path | str, schema: str = DEFAULT_SCHEMA) -> dict[str, int]:
    """Parse `landing_dir` and replace the staging tables in `schema`.

    Args:
        conn: An open connection with no transaction in progress
            (`connect_from_environment` gives one).
        landing_dir: A landing directory written by `ingestion.extract`.
        schema: Target schema, created if missing. Tests pass their own.

    Returns:
        Rows loaded per table.

    Raises:
        parse.ParseError: If the landing can't be parsed. Nothing is written.
        StagingError: If `conn` is already inside a transaction.
        psycopg.Error: If the database rejects the load. The transaction is
            rolled back, so the previous tables are unchanged.
    """
    rows = parse.parse_reference(parse.read_landing(landing_dir))
    if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
        raise StagingError("The connection is already inside a transaction; the load needs its own.")
    loaded_at = datetime.now(UTC)
    with conn.transaction():
        conn.execute(sql.SQL("create schema if not exists {}").format(sql.Identifier(schema)))
        for table in TABLES:
            _replace_table(conn, schema, table, rows[table.name], loaded_at)
    return {table.name: len(rows[table.name]) for table in TABLES}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m ingestion.load_staging", description=__doc__.splitlines()[0])
    parser.add_argument("landing_dir", nargs="?", default=DEFAULT_LANDING_DIR, type=Path)
    parser.add_argument("--schema", default=DEFAULT_SCHEMA)
    args = parser.parse_args(argv)
    try:
        with connect_from_environment() as conn:
            counts = load_staging(conn, args.landing_dir, args.schema)
    except (StagingError, parse.ParseError, psycopg.Error) as exc:
        print(f"Staging load failed: {exc}", file=sys.stderr)
        return 1
    print(f"Loaded {args.landing_dir} into schema {args.schema}")
    for name, count in counts.items():
        print(f"  {name:<10} {count:>5} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
