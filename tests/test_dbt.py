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
from test_staging import EXPECTED, RAW_DATA, TEST_SCHEMA, connect_or_fail, landing  # noqa: F401  (fixture)

DBT_DIR = Path(__file__).resolve().parent.parent / "dbt"
DBT = Path(sys.executable).with_name("dbt")
SUFFIX = "_test"
STG, MARTS = f"stg{SUFFIX}", f"marts{SUFFIX}"
VARS = json.dumps({"staging_schema": TEST_SCHEMA, "schema_suffix": SUFFIX})

STG_VIEWS = {f"stg_{table}": count for table, count in EXPECTED.items()}
"""One stg_ view per staging table, with the shipped dataset's row counts."""

DIMENSIONS = {"dim_team": 3, "dim_user": 12, "dim_sprint": 36, "dim_issue": 530}
FACTS = {"fct_status_transitions": 1488, "fct_sprint_membership": 320, "fct_capacity": 36}


def run_dbt(tmp_path: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    command = [
        str(DBT), *args,
        "--project-dir", str(DBT_DIR), "--profiles-dir", str(DBT_DIR), "--log-path", str(tmp_path / "logs"),
    ]
    if args[0] != "debug":  # debug takes neither
        command += ["--target-path", str(tmp_path / "target"), "--vars", VARS]
    return subprocess.run(command, capture_output=True, text=True, env=env or os.environ.copy(), cwd=tmp_path)


def failed_tests(tmp_path: Path) -> set[str]:
    """Names of the dbt tests that failed or errored in the last run (from run_results.json)."""
    results = json.loads((tmp_path / "target" / "run_results.json").read_text())["results"]
    return {
        r["unique_id"].split(".")[2]
        for r in results
        if r["unique_id"].startswith("test.") and r["status"] in ("fail", "error")
    }


def run_then_test(tmp_path: Path) -> set[str]:
    """`dbt run`, then `dbt test`, and the names of the failed tests.

    For sabotage checks. `dbt build` skips every node downstream of a failing
    test, so one early failure would hide the tests that should also fail.
    Running all models first and then all tests shows every failure.
    """
    result = run_dbt(tmp_path, "run")
    assert result.returncode == 0, result.stdout
    assert run_dbt(tmp_path, "test").returncode != 0, "the sabotage made no test fail"
    return failed_tests(tmp_path)


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
    """dbt runs one view per staging table into stg_test. A normal reload keeps the same views (same oids)."""
    result = run_dbt(tmp_path, "run", "--select", "staging")
    assert result.returncode == 0, result.stdout

    built = views(warehouse, STG)
    assert set(built) == set(STG_VIEWS)
    assert counts(warehouse, STG, STG_VIEWS) == STG_VIEWS

    loader.load_staging(warehouse, landing, schema=TEST_SCHEMA)
    assert views(warehouse, STG) == built
    assert counts(warehouse, STG, STG_VIEWS) == STG_VIEWS


@pytest.mark.postgres
def test_dbt_never_writes_outside_the_test_schemas(warehouse, tmp_path: Path) -> None:
    """With the test vars, every model is in stg_test or marts_test, and the source is staging_test."""
    result = run_dbt(tmp_path, "parse")
    assert result.returncode == 0, result.stdout
    manifest = json.loads((tmp_path / "target" / "manifest.json").read_text())
    models = [n for n in manifest["nodes"].values() if n["resource_type"] == "model"]
    assert {n["schema"] for n in models if n["fqn"][1] == "staging"} == {STG}
    assert {n["schema"] for n in models if n["fqn"][1] == "marts"} == {MARTS}
    assert {n["schema"] for n in models} == {STG, MARTS}
    assert {s["schema"] for s in manifest["sources"].values()} == {TEST_SCHEMA}


# --- Dimensions ------------------------------------------------------------------


@pytest.mark.postgres
def test_dimensions_build_with_the_shipped_counts_and_pass_their_tests(warehouse, tmp_path: Path) -> None:
    result = run_dbt(tmp_path, "build")
    assert result.returncode == 0, result.stdout
    assert failed_tests(tmp_path) == set()
    assert counts(warehouse, MARTS, DIMENSIONS) == DIMENSIONS
    tables = warehouse.execute(
        "select table_name from information_schema.tables where table_schema = %s and table_type = 'BASE TABLE'",
        (MARTS,),
    ).fetchall()
    assert {name for (name,) in tables} >= set(DIMENSIONS)


@pytest.mark.postgres
def test_derived_user_teams_equal_raw_data(warehouse, tmp_path: Path) -> None:
    """The team derived from assigned issues (D-029) is each user's team in the simulator's ground truth."""
    assert run_dbt(tmp_path, "run", "--select", "+dim_user", "+dim_team").returncode == 0
    team_keys = {t["team_id"]: t["key"] for t in json.loads((RAW_DATA / "teams.json").read_text())}
    expected = {u["display_name"]: team_keys[u["team_id"]] for u in json.loads((RAW_DATA / "users.json").read_text())}
    derived = warehouse.execute(
        sql.SQL("select u.display_name, t.team_key from {} u join {} t using (team_id)").format(
            sql.Identifier(MARTS, "dim_user"), sql.Identifier(MARTS, "dim_team")
        )
    ).fetchall()
    assert dict(derived) == expected


@pytest.mark.postgres
def test_sabotage_a_deleted_staging_user_fails_the_user_relationships(warehouse, landing: Path, tmp_path: Path) -> None:
    """Sabotage: delete one staging user. Exactly the tests that point at dim_user fail: the issue's assignee and
    reporter, the transition's author, and the transition join to every dimension."""
    user = "Phoenix Dev 1"
    reported = warehouse.execute(
        sql.SQL("select count(*) from {} i join {} u on u.account_id = i.reporter_account_id where u.display_name = %s").format(
            sql.Identifier(TEST_SCHEMA, "issues"), sql.Identifier(TEST_SCHEMA, "users")
        ),
        (user,),
    ).fetchone()[0]
    warehouse.execute(sql.SQL("delete from {} where display_name = %s").format(sql.Identifier(TEST_SCHEMA, "users")), (user,))

    failed = run_then_test(tmp_path)
    expected = {
        "relationships_dim_issue_assignee_user_id__user_id__ref_dim_user_",
        "relationships_fct_status_transitions_author_user_id__user_id__ref_dim_user_",
        "assert_transitions_join_every_dimension_once",
    }
    if reported:
        expected.add("relationships_dim_issue_reporter_user_id__user_id__ref_dim_user_")
    assert failed == expected

    loader.load_staging(warehouse, landing, schema=TEST_SCHEMA)
    assert run_dbt(tmp_path, "build").returncode == 0


@pytest.mark.postgres
def test_sabotage_a_duplicated_staging_user_fails_unique(warehouse, tmp_path: Path) -> None:
    """Sabotage: staging's primary key refuses a duplicate user. With the key dropped, unique on dim_user fails,
    and the duplicate also doubles the transitions joined to that user."""
    users = sql.Identifier(TEST_SCHEMA, "users")
    duplicate = sql.SQL("insert into {} select * from {} limit 1").format(users, users)
    with pytest.raises(psycopg.errors.UniqueViolation):
        warehouse.execute(duplicate)

    warehouse.execute(sql.SQL("alter table {} drop constraint users_pkey").format(users))
    warehouse.execute(duplicate)
    assert run_then_test(tmp_path) == {"unique_dim_user_user_id", "assert_transitions_join_every_dimension_once"}


@pytest.mark.postgres
def test_sabotage_a_user_in_two_teams_fails_the_team_mapping(warehouse, tmp_path: Path) -> None:
    """Sabotage: move one of a user's issues to another team. The user no longer maps to one team, and not_null fails.
    The moved issue's sprint membership now crosses teams, which the fact team check reports."""
    issues, teams = sql.Identifier(TEST_SCHEMA, "issues"), sql.Identifier(TEST_SCHEMA, "teams")
    warehouse.execute(
        sql.SQL(
            "update {} set project_id = (select project_id from {} where project_key = 'ATL') "
            "where issue_key = (select min(issue_key) from {} where issue_key like 'PHX-%%' and issue_type <> 'Epic')"
        ).format(issues, teams, issues)
    )
    assert run_then_test(tmp_path) == {"not_null_dim_user_team_id", "assert_fact_teams_match_their_sprint"}


# --- Facts -----------------------------------------------------------------------


def staging(table: str) -> sql.Identifier:
    return sql.Identifier(TEST_SCHEMA, table)


def mart(table: str) -> sql.Identifier:
    return sql.Identifier(MARTS, table)


@pytest.mark.postgres
def test_facts_build_with_the_shipped_counts_and_join_all_four_dimensions(warehouse, tmp_path: Path) -> None:
    result = run_dbt(tmp_path, "build")
    assert result.returncode == 0, result.stdout
    assert counts(warehouse, MARTS, FACTS) == FACTS
    joined = warehouse.execute(
        sql.SQL(
            "select count(*), count(distinct f.changelog_id) from {} f "
            "join {} i on i.issue_id = f.issue_id join {} t on t.team_id = f.team_id "
            "join {} u on u.user_id = f.author_user_id join {} s on s.sprint_id = f.event_sprint_id"
        ).format(mart("fct_status_transitions"), mart("dim_issue"), mart("dim_team"), mart("dim_user"), mart("dim_sprint"))
    ).fetchone()
    assert joined == (1488, 1488)


@pytest.mark.postgres
def test_event_sprint_is_the_window_that_contains_the_transition(warehouse, tmp_path: Path) -> None:
    """216 transitions happen after their issue's membership sprint ended; all of them land in a later sprint."""
    assert run_dbt(tmp_path, "build").returncode == 0
    rows = warehouse.execute(
        sql.SQL(
            "select count(*), count(*) filter (where f.transitioned_at >= ms.end_at and es.start_at >= ms.end_at) "
            "from {} f join {} m using (issue_id) join {} ms on ms.sprint_id = m.sprint_id "
            "join {} es on es.sprint_id = f.event_sprint_id where f.event_sprint_id <> m.sprint_id"
        ).format(mart("fct_status_transitions"), mart("fct_sprint_membership"), mart("dim_sprint"), mart("dim_sprint"))
    ).fetchone()
    assert rows == (216, 216)


@pytest.mark.postgres
def test_a_transition_on_a_sprint_boundary_belongs_to_the_later_sprint(warehouse, tmp_path: Path) -> None:
    """The window is half-open: a transition exactly at Sprint 6's start (= Sprint 5's end) is in Sprint 6 only."""
    boundary_sprint = (
        "(select sprint_id from {s} where sprint_name = 'Sprint 6' "
        "and board_id = (select board_id from {b} where project_key = 'PHX'))"
    )
    moved = warehouse.execute(
        sql.SQL(
            "update {t} set transitioned_at = (select start_at from {s} where sprint_id = " + boundary_sprint + ") "
            "where changelog_id = (select min(changelog_id) from {t} where issue_id in "
            "(select issue_id from {i} where issue_key like 'PHX-%%')) returning changelog_id"
        ).format(t=staging("status_transitions"), s=staging("sprints"), b=staging("boards"), i=staging("issues"))
    ).fetchone()[0]

    result = run_dbt(tmp_path, "build")
    assert result.returncode == 0, result.stdout
    event_sprint, expected = warehouse.execute(
        sql.SQL("select event_sprint_id, " + boundary_sprint + " from {f} where changelog_id = %s").format(
            f=mart("fct_status_transitions"), s=staging("sprints"), b=staging("boards")
        ),
        (moved,),
    ).fetchone()
    assert event_sprint == expected


@pytest.mark.postgres
def test_sabotage_an_orphan_transition_fails_the_issue_relationship(warehouse, tmp_path: Path) -> None:
    """Sabotage: a transition whose issue ID doesn't exist. The relationship test fails, and so do the
    checks that need the issue: its team, its window, and the join to every dimension."""
    transitions = staging("status_transitions")
    warehouse.execute(
        sql.SQL(
            "insert into {t} select changelog_id + 1000000000, 999999999, author_account_id, transitioned_at, "
            "from_status_id, from_status_name, to_status_id, to_status_name, _source_file, _loaded_at "
            "from {t} order by changelog_id limit 1"
        ).format(t=transitions)
    )
    assert run_then_test(tmp_path) == {
        "relationships_fct_status_transitions_issue_id__issue_id__ref_dim_issue_",
        "not_null_fct_status_transitions_team_id",
        "not_null_fct_status_transitions_event_sprint_id",
        "assert_each_transition_in_exactly_one_sprint_window",
        "assert_transitions_join_every_dimension_once",
    }


@pytest.mark.postgres
def test_sabotage_overlapping_sprints_fail_the_window_test(warehouse, tmp_path: Path) -> None:
    """Sabotage: PHX Sprint 5 ends a day late, overlapping Sprint 6. Transitions in that day match two windows."""
    warehouse.execute(
        sql.SQL(
            "update {s} set end_at = end_at + interval '1 day' where sprint_name = 'Sprint 5' "
            "and board_id = (select board_id from {b} where project_key = 'PHX')"
        ).format(s=staging("sprints"), b=staging("boards"))
    )
    assert run_then_test(tmp_path) == {
        "assert_each_transition_in_exactly_one_sprint_window",
        "unique_fct_status_transitions_changelog_id",
    }
