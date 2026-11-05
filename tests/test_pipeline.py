"""Tests for the one-command pipeline in pipeline/ (Session 5).

Nothing here touches the real landing/, staging, stg or marts. The first test
builds a throwaway database, `aap_pipeline_test`, on the same server: created
empty, filled by a real `python -m pipeline.run`, dropped at the end. It uses
the real schema names, which the *_test schemas of the other test files can't
check. Any test that changes data works on its own clone of it
(`create database ... template ...`), so tests can't affect each other.

The API port is a free port picked per run, and dbt runs on a copy of dbt/, so
dbt/target/ is left alone. Sabotage cases wrap one stage function of
`pipeline.run` and go through `main`, so they prove the real exit code and
message. Without the POSTGRES_* variables they skip, like every Postgres test.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from ingestion.client import ExtractionError
from ingestion.load_staging import StagingError
from pipeline import completeness, run
from pipeline.mock_api_server import MockApiError, mock_api_server, port_in_use
from test_dbt import DBT_DIR, DIMENSIONS, FACTS, counts
from test_staging import EXPECTED, connect_or_fail

DATABASE = "aap_pipeline_test"
CLONE = f"{DATABASE}_clone"
HOST = "127.0.0.1"

REPORTS = {
    "rpt_sprint_metrics": 36, "rpt_data_as_of": 1, "rpt_q1_velocity": 36, "rpt_q2_velocity_vs_capacity": 36,
    "rpt_q3_deadline_outcomes": 320, "rpt_q3_breach_rate": 48, "rpt_q4_status_stays": 1988,
    "rpt_q4_time_in_status": 16, "rpt_q5_bug_to_story_ratio": 39, "rpt_q6_team_portfolio": 3,
}
"""Rows per rpt_ table, from docs/etl/04-metrics.md."""


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind((HOST, 0))
        return probe.getsockname()[1]


def connect_to(database: str, **options) -> psycopg.Connection:
    return psycopg.connect(
        host=os.environ["POSTGRES_HOST"], port=os.environ["POSTGRES_PORT"], user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"], dbname=database, autocommit=True, **options,
    )


def drop_database(name: str) -> None:
    with connect_to("postgres") as admin:  # not POSTGRES_DB: a test may have pointed it at the database being dropped
        admin.execute(sql.SQL("drop database if exists {} with (force)").format(sql.Identifier(name)))


def recreate_database(name: str, template: str | None = None) -> None:
    drop_database(name)
    statement = "create database {}" + (" template {}" if template else "")
    with connect_to("postgres") as admin:
        admin.execute(sql.SQL(statement).format(*map(sql.Identifier, filter(None, (name, template)))))


def copy_dbt_project(target: Path) -> Path:
    return Path(shutil.copytree(DBT_DIR, target, ignore=shutil.ignore_patterns("target", "logs")))


def all_counts(database: str) -> dict[str, dict[str, int]]:
    with connect_to(database) as connection:
        return {
            "staging": counts(connection, "staging", EXPECTED),
            "marts": counts(connection, "marts", DIMENSIONS | FACTS),
            "rpt": counts(connection, "marts", REPORTS),
        }


def run_process(database: str, port: int, landing: Path, project: Path, **kwargs) -> subprocess.CompletedProcess:
    """`python -m pipeline.run` as a real process."""
    env = os.environ | {
        "POSTGRES_DB": database, "AAP_API_BASE_URL": f"http://{HOST}:{port}", "AAP_API_TOKEN": "test-token",
    }
    command = [sys.executable, "-m", "pipeline.run", "--landing-dir", str(landing), "--dbt-project-dir", str(project)]
    return subprocess.run(command, env=env, capture_output=True, text=True, **kwargs)


@pytest.fixture(scope="module")
def first_run(tmp_path_factory):
    """One real run, from an empty database, with no landing directory yet."""
    connect_or_fail().close()
    recreate_database(DATABASE)
    work = tmp_path_factory.mktemp("pipeline")
    port = free_port()
    result = run_process(DATABASE, port, work / "landing", copy_dbt_project(work / "dbt"))
    assert result.returncode == 0, result.stdout + result.stderr
    yield result, port, work
    drop_database(DATABASE)


@pytest.fixture
def clone(first_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """This test's own database, a copy of the built one, with the environment pointing at it."""
    recreate_database(CLONE, template=DATABASE)
    port = free_port()
    monkeypatch.setenv("POSTGRES_DB", CLONE)
    monkeypatch.setenv("AAP_API_BASE_URL", f"http://{HOST}:{port}")
    monkeypatch.setenv("AAP_API_TOKEN", "test-token")
    yield port
    drop_database(CLONE)


def run_main(tmp_path: Path, project: Path | None = None) -> int:
    landing = tmp_path / "landing"
    return run.main(["--landing-dir", str(landing), "--dbt-project-dir", str(project or copy_dbt_project(tmp_path / "dbt"))])


def delete_one_row(table: sql.Identifier) -> None:
    with connect_to(CLONE) as connection:
        deleted = connection.execute(
            sql.SQL("delete from {t} where ctid = (select min(ctid) from {t})").format(t=table)
        )
        assert deleted.rowcount == 1


# --- A real run ----------------------------------------------------------------


@pytest.mark.postgres
def test_one_run_from_an_empty_database_reaches_every_count(first_run) -> None:
    result, port, work = first_run
    assert all_counts(DATABASE) == {"staging": EXPECTED, "marts": DIMENSIONS | FACTS, "rpt": REPORTS}
    assert len([p for p in (work / "landing").rglob("*") if p.is_file()]) == 579
    assert "Pipeline finished in" in result.stdout
    assert not port_in_use(HOST, port)


@pytest.mark.postgres
def test_a_second_run_exits_0_with_identical_counts(clone: int, tmp_path: Path, capfd) -> None:
    assert run_main(tmp_path) == run.EXIT_OK
    assert all_counts(CLONE) == {"staging": EXPECTED, "marts": DIMENSIONS | FACTS, "rpt": REPORTS}
    assert not port_in_use(HOST, clone)


# --- Sabotage: one row lost at each stage boundary, exit 3 -------------------------


def instead_of_dbt(sabotage=lambda: None):
    """Stands in for `dbt build` on a clone, whose marts are already built, and damages them."""
    return lambda project_dir: sabotage()


@pytest.mark.postgres
def test_sabotage_a_page_lost_after_extraction_exits_3(clone: int, tmp_path: Path, monkeypatch, capsys) -> None:
    """A changelog page is deleted but keeps its manifest entry."""
    def extract_then_lose_a_page(client, landing_dir):
        real_extraction(client, landing_dir)
        (Path(landing_dir) / "changelogs" / "PHX-1" / "page-0000.json").unlink()

    real_extraction = run.run_extraction
    monkeypatch.setattr(run, "run_extraction", extract_then_lose_a_page)
    assert run_main(tmp_path) == run.EXIT_INCOMPLETE
    error = capsys.readouterr().err
    assert "stage 'extraction'" in error
    assert "changelogs/PHX-1/page-0000.json is in the manifest (578 entries) but not on disk (577 pages)" in error


@pytest.mark.postgres
def test_sabotage_a_row_lost_from_landing_to_staging_exits_3(clone: int, tmp_path: Path, monkeypatch, capsys) -> None:
    def load_then_lose_a_row(conn, landing_dir):
        loaded = real_load(conn, landing_dir)
        delete_one_row(sql.Identifier("staging", "status_transitions"))
        return loaded

    real_load = run.load_staging
    monkeypatch.setattr(run, "load_staging", load_then_lose_a_row)
    assert run_main(tmp_path) == run.EXIT_INCOMPLETE
    error = capsys.readouterr().err
    assert "stage 'landing to staging'" in error
    assert "staging.status_transitions has 1,487 rows, landing has 1,488 status changelog items" in error


@pytest.mark.postgres
def test_sabotage_a_row_lost_from_staging_to_marts_exits_3(clone: int, tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(run, "build_dbt", instead_of_dbt(lambda: delete_one_row(sql.Identifier("marts", "fct_sprint_membership"))))
    assert run_main(tmp_path) == run.EXIT_INCOMPLETE
    error = capsys.readouterr().err
    assert "stage 'staging to marts'" in error
    assert "marts.fct_sprint_membership has 319 rows, staging.sprint_memberships has 320 rows" in error


@pytest.mark.postgres
def test_sabotage_a_row_lost_from_marts_to_the_rpt_tables_exits_3(clone: int, tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(run, "build_dbt", instead_of_dbt(lambda: delete_one_row(sql.Identifier("marts", "rpt_q1_velocity"))))
    assert run_main(tmp_path) == run.EXIT_INCOMPLETE
    error = capsys.readouterr().err
    assert "stage 'marts to rpt_ tables'" in error
    assert "marts.rpt_q1_velocity has 35 rows, marts.dim_sprint has 36 rows" in error


@pytest.mark.postgres
def test_an_untouched_clone_passes_every_check_so_the_sabotage_is_what_fails(clone: int, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(run, "build_dbt", instead_of_dbt())
    assert run_main(tmp_path) == run.EXIT_OK


def test_a_file_missing_from_the_manifest_fails_the_landing_check(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text(json.dumps({"files": []}))
    (tmp_path / "boards").mkdir()
    (tmp_path / "boards" / "page-0000.json").write_text("{}")
    with pytest.raises(completeness.CompletenessError, match=r"boards/page-0000.json is on disk \(1 pages\) but not in the manifest \(0 entries\)"):
        completeness.check_landing(tmp_path)


def test_a_missing_manifest_fails_the_landing_check(tmp_path: Path) -> None:
    with pytest.raises(completeness.CompletenessError, match="manifest.json does not exist"):
        completeness.check_landing(tmp_path)


# --- Preconditions: exit 2, nothing written -----------------------------------------


@pytest.mark.postgres
def test_sabotage_a_busy_api_port_exits_2_and_writes_nothing(clone: int, tmp_path: Path, capsys) -> None:
    with socket.socket() as listener:
        listener.bind((HOST, clone))
        listener.listen()
        assert run_main(tmp_path) == run.EXIT_PRECONDITION
    assert f"Port {clone} is already in use" in capsys.readouterr().err
    assert not (tmp_path / "landing").exists()


@pytest.mark.postgres
@pytest.mark.parametrize("variable, value, message", [
    ("AAP_RAW_DATA_DIR", "/nonexistent", "scripts/generate_raw_data.py"),
    ("AAP_API_TOKEN", "", "AAP_API_TOKEN"),
    ("POSTGRES_USER", "", "POSTGRES_USER"),
    ("AAP_API_BASE_URL", "http://example.com:8000", "must be a local http:// URL"),
    ("POSTGRES_DB", "aap_no_such_database", "Postgres is unreachable"),
])
def test_a_failed_precondition_exits_2_and_writes_nothing(
    clone: int, tmp_path: Path, monkeypatch, capsys, variable: str, value: str, message: str
) -> None:
    with monkeypatch.context() as patch:  # undone before `clone` drops its database with these same variables
        patch.setenv(variable, value)
        assert run_main(tmp_path) == run.EXIT_PRECONDITION
    assert message in capsys.readouterr().err
    assert not (tmp_path / "landing").exists()


# --- Failed stages: exit 1, and what they leave behind ---------------------------------


def raises(error: Exception):
    def stand_in(*args, **kwargs):
        raise error
    return stand_in


@pytest.mark.postgres
@pytest.mark.parametrize("function, error, stale", [
    ("run_extraction", ExtractionError("rejected"), "landing/ still holds the previous run"),
    ("load_staging", StagingError("rejected"), "staging rolled back to its previous rows"),
])
def test_a_failed_stage_exits_1_and_says_what_is_stale(
    clone: int, tmp_path: Path, monkeypatch, capsys, function: str, error: Exception, stale: str
) -> None:
    monkeypatch.setattr(run, function, raises(error))
    assert run_main(tmp_path) == run.EXIT_STAGE_FAILED
    assert stale in capsys.readouterr().err
    assert not port_in_use(HOST, clone)


def table_oids(database: str) -> dict[str, int]:
    """Table name -> oid in marts. dbt's rebuild gives a table a new oid; a table left alone keeps its own."""
    with connect_to(database) as connection:
        rows = connection.execute(
            "select c.relname, c.oid::int from pg_class c join pg_namespace n on n.oid = c.relnamespace "
            "where n.nspname = 'marts' and c.relkind = 'r'"
        ).fetchall()
    return dict(rows)


@pytest.mark.postgres
def test_sabotage_a_failed_model_keeps_its_previous_table(clone: int, tmp_path: Path, capfd) -> None:
    """D-046, checked on a real failed `dbt build`. A broken rpt_q1_velocity keeps its previous table and rows, its
    downstream model is skipped and keeps its own, and models that don't depend on it are still rebuilt."""
    project = copy_dbt_project(tmp_path / "dbt")
    (project / "models" / "metrics" / "rpt_q1_velocity.sql").write_text("select no_such_column from {{ ref('rpt_sprint_metrics') }}")
    before = table_oids(CLONE)

    assert run_main(tmp_path, project) == run.EXIT_STAGE_FAILED

    after = table_oids(CLONE)
    assert after["rpt_q1_velocity"] == before["rpt_q1_velocity"]
    assert after["rpt_q6_team_portfolio"] == before["rpt_q6_team_portfolio"]
    assert after["dim_issue"] != before["dim_issue"]
    assert after["rpt_q5_bug_to_story_ratio"] != before["rpt_q5_bug_to_story_ratio"]
    assert after["rpt_q3_breach_rate"] != before["rpt_q3_breach_rate"]
    assert all_counts(CLONE)["rpt"] == REPORTS
    assert "marts may be mixed" in capfd.readouterr().err
    assert not port_in_use(HOST, clone)


@pytest.mark.postgres
def test_sabotage_a_failed_data_test_leaves_the_model_rebuilt_and_skips_downstream(clone: int, tmp_path: Path, capfd) -> None:
    """The other way a dbt build fails: the model builds, and a test on it fails. The table is already replaced by
    then, so it holds this run's rows; only what is downstream of it keeps the previous table."""
    project = copy_dbt_project(tmp_path / "dbt")
    (project / "tests" / "assert_sabotage_always_fails.sql").write_text("select * from {{ ref('rpt_q1_velocity') }}")
    before = table_oids(CLONE)

    assert run_main(tmp_path, project) == run.EXIT_STAGE_FAILED

    after = table_oids(CLONE)
    assert after["rpt_q1_velocity"] != before["rpt_q1_velocity"]
    assert after["rpt_q6_team_portfolio"] == before["rpt_q6_team_portfolio"]


@pytest.mark.postgres
def test_a_failed_model_on_an_empty_database_leaves_no_table(tmp_path: Path, monkeypatch, capfd) -> None:
    """The last clause of the failure table: on an empty database a failed model has no previous table to keep."""
    connect_or_fail().close()
    recreate_database(CLONE)
    monkeypatch.setenv("POSTGRES_DB", CLONE)
    monkeypatch.setenv("AAP_API_BASE_URL", f"http://{HOST}:{free_port()}")
    monkeypatch.setenv("AAP_API_TOKEN", "test-token")
    project = copy_dbt_project(tmp_path / "dbt")
    (project / "models" / "metrics" / "rpt_q1_velocity.sql").write_text("select no_such_column from {{ ref('rpt_sprint_metrics') }}")
    try:
        assert run_main(tmp_path, project) == run.EXIT_STAGE_FAILED
        tables = table_oids(CLONE)
    finally:
        drop_database(CLONE)
    assert "rpt_q1_velocity" not in tables and "rpt_q6_team_portfolio" not in tables
    assert "rpt_sprint_metrics" in tables


# --- The mock API is always stopped ---------------------------------------------------


def test_the_mock_api_stops_when_the_block_raises() -> None:
    port = free_port()
    with pytest.raises(ZeroDivisionError):
        with mock_api_server(HOST, port, "test-token") as process:
            assert port_in_use(HOST, port)
            1 / 0
    assert process.poll() is not None
    assert not port_in_use(HOST, port)


def test_a_mock_api_that_cannot_start_is_reported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AAP_RAW_DATA_DIR", str(tmp_path))
    with pytest.raises(MockApiError, match="exited with code"):
        with mock_api_server(HOST, free_port(), "test-token"):
            pass


def test_a_mock_api_that_never_answers_is_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A readiness path that never answers 200: the wait runs out and the process is stopped anyway."""
    port = free_port()
    monkeypatch.setattr("pipeline.mock_api_server.READY_PATH", "/no-such-path")
    with pytest.raises(MockApiError, match="did not answer"):
        with mock_api_server(HOST, port, "test-token", ready_timeout=2):
            pass
    assert not port_in_use(HOST, port)


@pytest.mark.postgres
def test_ctrl_c_exits_130_and_stops_the_mock_api(clone: int, tmp_path: Path) -> None:
    """SIGINT while the mock API is up, as Ctrl-C would send it."""
    process = subprocess.Popen(
        [sys.executable, "-m", "pipeline.run", "--landing-dir", str(tmp_path / "landing")],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    deadline = time.monotonic() + 30
    while not port_in_use(HOST, clone) and time.monotonic() < deadline:
        time.sleep(0.05)
    process.send_signal(signal.SIGINT)
    _, error = process.communicate(timeout=30)
    assert process.returncode == run.EXIT_INTERRUPTED, error
    assert "interrupted" in error
    assert not port_in_use(HOST, clone)
    assert not (tmp_path / "landing").exists()
