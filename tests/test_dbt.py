"""Tests for the dbt project in dbt/ (Session 3).

dbt runs as a subprocess, with the same profile the documented command uses
(dbt/profiles.yml, credentials from POSTGRES_*). Tests never touch the real
schemas: staging is loaded into `staging_test`, and dbt builds into
`stg_test` and `marts_test` through two vars, `staging_schema` and
`schema_suffix` (dbt/macros/generate_schema_name.sql). Target and log files
go to a temporary directory, so dbt/target/ is left alone. All three schemas
are dropped before and after.

dbt runs with the temporary directory as its working directory. dbt-core 1.12
loads a `.env` from the working directory (or a parent) by itself, without
overriding variables already set (dbt/cli/main.py, `load_dotenv`). From the
repo root it would fill in a removed POSTGRES_* variable from the root .env,
so the tests run elsewhere and depend on the process environment only.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest
import yaml
from psycopg import sql

from ingestion import load_staging as loader
from test_staging import EXPECTED, TEST_SCHEMA, connect_or_fail, landing  # noqa: F401  (fixture)

DBT_DIR = Path(__file__).resolve().parent.parent / "dbt"
DBT = Path(sys.executable).with_name("dbt")
SUFFIX = "_test"
STG, MARTS = f"stg{SUFFIX}", f"marts{SUFFIX}"
VARS = json.dumps({"staging_schema": TEST_SCHEMA, "schema_suffix": SUFFIX})

STG_VIEWS = {f"stg_{table}": count for table, count in EXPECTED.items()}
"""One stg_ view per staging table, with the shipped dataset's row counts."""


def run_dbt(tmp_path: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    command = [
        str(DBT), *args,
        "--project-dir", str(DBT_DIR), "--profiles-dir", str(DBT_DIR), "--log-path", str(tmp_path / "logs"),
    ]
    if args[0] != "debug":  # debug takes neither
        command += ["--target-path", str(tmp_path / "target"), "--vars", VARS]
    return subprocess.run(command, capture_output=True, text=True, env=env or os.environ.copy(), cwd=tmp_path)


def drop_dbt_test_schemas(connection: psycopg.Connection) -> None:
    for schema in (STG, MARTS, TEST_SCHEMA):
        connection.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema)))


@pytest.fixture
def warehouse(landing: Path):
    """staging_test loaded from the real landing, and no stg_test/marts_test yet."""
    with connect_or_fail() as connection:
        drop_dbt_test_schemas(connection)
        loader.load_staging(connection, landing, schema=TEST_SCHEMA)
        yield connection
        drop_dbt_test_schemas(connection)


def views(connection: psycopg.Connection, schema: str) -> dict[str, int]:
    """View name -> oid. The oid changes if a view is dropped and recreated."""
    rows = connection.execute(
        "select c.relname, c.oid::int from pg_class c join pg_namespace n on n.oid = c.relnamespace "
        "where n.nspname = %s and c.relkind = 'v'",
        (schema,),
    ).fetchall()
    return dict(rows)


def counts(connection: psycopg.Connection, schema: str, names) -> dict[str, int]:
    return {
        name: connection.execute(sql.SQL("select count(*) from {}").format(sql.Identifier(schema, name))).fetchone()[0]
        for name in names
    }


# --- Profile ------------------------------------------------------------------


def test_profile_takes_every_connection_value_from_the_environment() -> None:
    output = yaml.safe_load((DBT_DIR / "profiles.yml").read_text())["aap"]["outputs"]["dev"]
    for field, variable in [("host", "POSTGRES_HOST"), ("port", "POSTGRES_PORT"), ("user", "POSTGRES_USER"),
                            ("password", "POSTGRES_PASSWORD"), ("dbname", "POSTGRES_DB")]:
        assert f"env_var('{variable}')" in str(output[field]), field


@pytest.mark.postgres
def test_dbt_debug_connects_with_the_environment(tmp_path: Path) -> None:
    connect_or_fail().close()
    result = run_dbt(tmp_path, "debug")
    assert result.returncode == 0, result.stdout
    assert "All checks passed!" in result.stdout


@pytest.mark.postgres
def test_sabotage_a_wrong_credential_fails_dbt_debug_clearly(tmp_path: Path) -> None:
    """Sabotage: a wrong password, or a missing variable, makes dbt debug fail and say why."""
    connect_or_fail().close()
    wrong = os.environ | {"POSTGRES_PASSWORD": "not-the-password"}
    result = run_dbt(tmp_path, "debug", env=wrong)
    assert result.returncode != 0
    assert "password authentication failed" in result.stdout

    missing = {k: v for k, v in os.environ.items() if k != "POSTGRES_HOST"}
    result = run_dbt(tmp_path, "debug", env=missing)
    assert result.returncode != 0
    assert "Env var required but not provided: 'POSTGRES_HOST'" in result.stdout


# --- stg views -------------------------------------------------------------------


@pytest.mark.postgres
def test_stg_views_build_count_and_survive_a_staging_reload(warehouse, landing: Path, tmp_path: Path) -> None:
    """dbt builds one view per staging table in stg_test. A normal reload keeps the same views (same oids)."""
    result = run_dbt(tmp_path, "build", "--select", "staging")
    assert result.returncode == 0, result.stdout

    built = views(warehouse, STG)
    assert set(built) == set(STG_VIEWS)
    assert counts(warehouse, STG, STG_VIEWS) == STG_VIEWS

    loader.load_staging(warehouse, landing, schema=TEST_SCHEMA)
    assert views(warehouse, STG) == built
    assert counts(warehouse, STG, STG_VIEWS) == STG_VIEWS


@pytest.mark.postgres
def test_dbt_never_writes_outside_the_test_schemas(warehouse, tmp_path: Path) -> None:
    """With the test vars, the compiled sources and models point at the _test schemas only."""
    result = run_dbt(tmp_path, "compile", "--select", "staging")
    assert result.returncode == 0, result.stdout
    manifest = json.loads((tmp_path / "target" / "manifest.json").read_text())
    assert {n["schema"] for n in manifest["nodes"].values() if n["resource_type"] == "model"} == {STG}
    assert {s["schema"] for s in manifest["sources"].values()} == {TEST_SCHEMA}
