"""Tests for the staging parser and loader (step 2.1: reference entities).

The landing is produced in-process from the real raw_data/, the same way
tests/test_extraction.py does it, so no server or prior extract is needed.

Tests marked `postgres` load into the `staging_test` schema, which they
create and drop. They never touch `staging`. Without the POSTGRES_*
variables they skip with a reason; run them with
`uv run --env-file .env python -m pytest -rs`.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql

from ingestion import load_staging as loader
from ingestion.client import JiraClient
from ingestion.extract import MANIFEST, run_extraction
from ingestion.parse import ParseError, page_kind, parse_reference, parse_timestamp, read_landing
from mock_api.app import create_app

RAW_DATA = Path(__file__).resolve().parent.parent / "raw_data"
TEST_SCHEMA = "staging_test"

EXPECTED = {"teams": 3, "users": 12, "boards": 3, "sprints": 36, "capacity": 36}
"""Shipped dataset (SEED=22) targets, from CLAUDE.md."""

EXPECTED_TYPES = {
    "bigint": "bigint",
    "text": "text",
    "integer": "integer",
    "double precision": "double precision",
    "timestamptz": "timestamp with time zone",
}
"""DDL type -> information_schema.columns.data_type."""


# --- Fixtures and helpers -------------------------------------------------


@pytest.fixture(scope="session")
def landing(tmp_path_factory) -> Path:
    if not RAW_DATA.is_dir():
        pytest.fail(f"{RAW_DATA} is missing: run `uv run python scripts/generate_raw_data.py` first")
    target = tmp_path_factory.mktemp("staging") / "landing"
    with TestClient(create_app(RAW_DATA)) as app:
        run_extraction(JiraClient(app, token="test-token", backoff_seconds=0), target)
    return target


@pytest.fixture
def landing_copy(landing: Path, tmp_path: Path) -> Path:
    """A writable copy of the landing, for tests that damage it."""
    return Path(shutil.copytree(landing, tmp_path / "landing"))


@pytest.fixture
def conn():
    missing = [name for name in loader.REQUIRED_ENV if not os.environ.get(name, "").strip()]
    if missing:
        pytest.skip(f"{', '.join(missing)} not set; run with `uv run --env-file .env python -m pytest`")
    assert TEST_SCHEMA != loader.DEFAULT_SCHEMA
    with loader.connect_from_environment() as connection:
        drop_test_schema(connection)
        yield connection
        drop_test_schema(connection)


def drop_test_schema(connection: psycopg.Connection) -> None:
    connection.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(TEST_SCHEMA)))


def contents(connection: psycopg.Connection, table: loader.Table, with_loaded_at: bool = False) -> list[tuple]:
    """Every row of a test-schema table in primary key order, `_loaded_at` left out unless asked."""
    names = table.column_names + ["_source_file"] + (["_loaded_at"] if with_loaded_at else [])
    query = sql.SQL("select {} from {} order by {}").format(
        sql.SQL(", ").join(map(sql.Identifier, names)),
        sql.Identifier(TEST_SCHEMA, table.name),
        sql.SQL(", ").join(map(sql.Identifier, table.primary_key)),
    )
    return connection.execute(query).fetchall()


def snapshot(connection: psycopg.Connection) -> dict[str, list[tuple]]:
    return {table.name: contents(connection, table) for table in loader.TABLES}


def load(connection: psycopg.Connection, landing_dir: Path) -> dict[str, int]:
    return loader.load_staging(connection, landing_dir, schema=TEST_SCHEMA)


def edit_page(landing_dir: Path, file: str, change) -> None:
    path = landing_dir / file
    page = json.loads(path.read_bytes())
    change(page)
    path.write_text(json.dumps(page))


# --- Parser, no database ------------------------------------------------------


def test_both_timestamp_formats_parse_to_the_same_utc_instant() -> None:
    platform = parse_timestamp("2024-02-05T09:00:00.000+0000")
    agile = parse_timestamp("2024-02-05T09:00:00.000+00:00")
    assert platform == agile == datetime(2024, 2, 5, 9, 0, tzinfo=UTC)
    assert platform.tzinfo is UTC and agile.tzinfo is UTC
    assert parse_timestamp("2024-02-05T10:30:00.250+01:30") == datetime(2024, 2, 5, 9, 0, 0, 250000, tzinfo=UTC)
    assert parse_timestamp(None) is None


@pytest.mark.parametrize("value", ["2024-02-05T09:00:00.000", "2024-02-05T09:00:00Z", "2024-02-05 09:00:00.000+0000", "05/02/2024"])
def test_unknown_timestamp_formats_are_refused(value: str) -> None:
    with pytest.raises(ParseError, match="neither Jira format"):
        parse_timestamp(value)


def test_unknown_request_path_is_refused() -> None:
    assert page_kind("/rest/agile/1.0/board/66676782/sprint") == "board_sprints"
    with pytest.raises(ParseError, match="Unknown request path"):
        page_kind("/rest/api/3/search")


def test_parsed_counts_match_the_shipped_dataset(landing: Path) -> None:
    rows = parse_reference(read_landing(landing))
    assert {name: len(table) for name, table in rows.items()} == EXPECTED


def test_parser_reads_the_manifest_not_the_directory(landing_copy: Path) -> None:
    """An extra file the manifest doesn't list is ignored; a listed file that is missing fails."""
    shutil.copy(landing_copy / "capacity" / "page-0000.json", landing_copy / "capacity" / "page-0099.json")
    assert len(parse_reference(read_landing(landing_copy))["capacity"]) == 36
    (landing_copy / "boards" / "page-0000.json").unlink()
    with pytest.raises(ParseError, match="boards/page-0000.json is listed in the manifest but missing"):
        read_landing(landing_copy)


def test_conflicting_pages_are_refused(landing_copy: Path) -> None:
    """Teams come from boards and from issues; if they disagree the parser stops."""
    edit_page(landing_copy, "boards/page-0000.json", lambda p: p["values"][0]["location"].update(projectName="Renamed"))
    with pytest.raises(ParseError, match="Team 18405660 differs"):
        parse_reference(read_landing(landing_copy))


# --- Loader, Postgres ------------------------------------------------------------


@pytest.mark.postgres
def test_loaded_counts_match_the_shipped_dataset(conn, landing: Path) -> None:
    assert load(conn, landing) == EXPECTED
    for table in loader.TABLES:
        count = conn.execute(sql.SQL("select count(*) from {}").format(sql.Identifier(TEST_SCHEMA, table.name))).fetchone()[0]
        assert count == EXPECTED[table.name], table.name


@pytest.mark.postgres
def test_tables_have_typed_columns_primary_keys_and_no_foreign_keys(conn, landing: Path) -> None:
    load(conn, landing)
    for table in loader.TABLES:
        declared = {name: EXPECTED_TYPES[kind.removesuffix(" not null")] for name, kind in table.columns + loader.LINEAGE_COLUMNS}
        actual = dict(conn.execute(
            "select column_name, data_type from information_schema.columns where table_schema = %s and table_name = %s",
            (TEST_SCHEMA, table.name),
        ).fetchall())
        assert actual == declared, table.name
        key = [row[0] for row in conn.execute(
            """select k.column_name from information_schema.table_constraints c
               join information_schema.key_column_usage k using (constraint_schema, constraint_name)
               where c.table_schema = %s and c.table_name = %s and c.constraint_type = 'PRIMARY KEY'
               order by k.ordinal_position""",
            (TEST_SCHEMA, table.name),
        ).fetchall()]
        assert key == list(table.primary_key), table.name
    foreign_keys = conn.execute(
        "select count(*) from information_schema.table_constraints where table_schema = %s and constraint_type = 'FOREIGN KEY'",
        (TEST_SCHEMA,),
    ).fetchone()[0]
    assert foreign_keys == 0


@pytest.mark.postgres
def test_values_survive_the_round_trip(conn, landing: Path) -> None:
    """Sprint instants are 09:00 UTC, pto_days keeps full precision, lineage names real manifest files."""
    load(conn, landing)
    sprints = {table.name: table for table in loader.TABLES}["sprints"]
    for row in contents(conn, sprints):
        start_at, end_at = row[4], row[5]
        assert start_at.astimezone(UTC).hour == 9 and end_at.astimezone(UTC).hour == 9
        assert (end_at - start_at).days == 14

    landed = {(r["team_key"], r["sprint_id"]): r["pto_days"] for r in json.loads((landing / "capacity" / "page-0000.json").read_bytes())["values"]}
    stored = dict(((t, s), p) for t, s, p in conn.execute(f"select team_key, sprint_id, pto_days from {TEST_SCHEMA}.capacity").fetchall())
    assert stored == landed  # exact float equality, no rounding

    manifest_files = {e["file"] for e in json.loads((landing / MANIFEST).read_text())["files"]}
    for table in loader.TABLES:
        sources = conn.execute(sql.SQL("select distinct _source_file from {}").format(sql.Identifier(TEST_SCHEMA, table.name))).fetchall()
        assert {s for (s,) in sources} <= manifest_files, table.name


@pytest.mark.postgres
def test_rerun_gives_identical_contents(conn, landing: Path) -> None:
    load(conn, landing)
    first = snapshot(conn)
    first_loaded_at = conn.execute(f"select distinct _loaded_at from {TEST_SCHEMA}.teams").fetchall()
    load(conn, landing)
    assert snapshot(conn) == first
    loaded_at = set()
    for table in loader.TABLES:
        loaded_at |= set(conn.execute(sql.SQL("select distinct _loaded_at from {}").format(sql.Identifier(TEST_SCHEMA, table.name))).fetchall())
    assert len(first_loaded_at) == 1 and len(loaded_at) == 1  # one timestamp per run, shared by every table
    assert loaded_at != set(first_loaded_at)


@pytest.mark.postgres
def test_sabotage_the_rerun_comparison_catches_a_changed_value(conn, landing: Path) -> None:
    """The comparison used by the rerun gate must notice a single changed value."""
    load(conn, landing)
    before = snapshot(conn)
    conn.execute(f"update {TEST_SCHEMA}.capacity set pto_days = pto_days + 1e-9 where ctid = (select min(ctid) from {TEST_SCHEMA}.capacity)")
    assert snapshot(conn) != before


@pytest.mark.postgres
def test_failed_parse_leaves_the_previous_tables_intact(conn, landing: Path, landing_copy: Path) -> None:
    load(conn, landing)
    before = {table.name: contents(conn, table, with_loaded_at=True) for table in loader.TABLES}
    (landing_copy / "capacity" / "page-0000.json").write_text('{"values": [')  # truncated page
    with pytest.raises(ParseError, match="capacity/page-0000.json is not valid JSON"):
        load(conn, landing_copy)
    assert {table.name: contents(conn, table, with_loaded_at=True) for table in loader.TABLES} == before


@pytest.mark.postgres
def test_failure_mid_transaction_rolls_back_every_table(conn, landing: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failure after teams, users, boards and sprints were already replaced must undo all of them."""
    load(conn, landing)
    before = {table.name: contents(conn, table, with_loaded_at=True) for table in loader.TABLES}
    real_replace = loader._replace_table

    def fail_on_capacity(connection, schema, table, rows, loaded_at):
        if table.name == "capacity":
            raise psycopg.errors.DiskFull("simulated failure while loading capacity")
        real_replace(connection, schema, table, rows[:1], loaded_at)  # earlier tables are replaced with 1 row

    monkeypatch.setattr(loader, "_replace_table", fail_on_capacity)
    with pytest.raises(psycopg.errors.DiskFull):
        load(conn, landing)
    assert {table.name: contents(conn, table, with_loaded_at=True) for table in loader.TABLES} == before


def test_cli_names_the_missing_variable_and_writes_nothing(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    for name in loader.REQUIRED_ENV:
        monkeypatch.setenv(name, "unused")
    monkeypatch.delenv("POSTGRES_PASSWORD")
    assert loader.main(["--schema", TEST_SCHEMA]) == 1
    err = capsys.readouterr().err
    assert "Missing POSTGRES_PASSWORD." in err and "--env-file .env" in err
