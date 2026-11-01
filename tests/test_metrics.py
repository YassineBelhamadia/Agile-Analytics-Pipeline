"""Tests for the Session 4 metrics models in dbt/models/metrics/.

Step 4.1: rpt_sprint_metrics against the answer key. The expected side is
simulator/sprint_report.py run on raw_data/ (loaded with
simulator.persistence, which reads the JSON files and nothing else). Nothing
comes from mock_api/, ingestion/ or the warehouse. The warehouse side is read
from the marts layer after a dbt build. Both sides meet on team key + sprint
name.

Millisecond rule (D-024, D-035). The warehouse holds timestamps floored to
milliseconds; the answer key compares raw microsecond values. Sprint
boundaries are whole seconds (09:00 UTC), so flooring changes a comparison
with a boundary b only for a raw value strictly inside (b, b + 1 ms): it
floors onto b, and `> b` becomes `<= b`. A value exactly on b stays on b, and
any other value keeps its side. The boundary test counts such values.

Postgres tests build into marts_test (fixtures from test_dbt); the real
schemas are never touched.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pytest
from psycopg import sql

from simulator.persistence import load_from_json
from simulator.sprint_report import compute_sprint_report
from test_dbt import DBT_DIR, MARTS, failed_tests, run_dbt, warehouse  # noqa: F401  (fixture)
from test_staging import RAW_DATA, landing  # noqa: F401  (fixture)

SPRINT_BOUNDARY = time(9, 0)
"""Sprints start and end at 09:00 UTC on their dates (docs/domain-model.md, sprint metrics section)."""

MILLISECOND = timedelta(milliseconds=1)

REPORTED = ("committed", "scope_added", "completed", "hit", "bug_count", "total_count")
"""Every value compute_sprint_report returns besides the sprint name."""

CALENDAR_DAY_MISMATCHES = 17
"""Sprints whose figures change when calendar days replace exact instants (shipped data, SEED=22)."""


# --- Expected side: the answer key on raw_data/ ----------------------------------


def answer_key() -> dict[tuple[str, str], dict]:
    """(team key, sprint name) -> sprint_report's figures, for every sprint in raw_data/."""
    collector = load_from_json(RAW_DATA)
    team_keys = {team.team_id: team.key for team in collector.teams}
    reports = {}
    for sprint in collector.sprints:
        report = compute_sprint_report(sprint, collector)
        reports[(team_keys[sprint.team_id], report["name"])] = {name: report[name] for name in REPORTED}
    return reports


def warehouse_metrics(connection, schema: str) -> dict[tuple[str, str], dict]:
    """The same figures, read from rpt_sprint_metrics in `schema`."""
    rows = connection.execute(
        sql.SQL(
            "select team_key, sprint_name, committed_points, scope_added_points, completed_points, "
            "is_hit, bug_count, issue_count from {}"
        ).format(sql.Identifier(schema, "rpt_sprint_metrics"))
    ).fetchall()
    return {(team, name): dict(zip(REPORTED, values)) for team, name, *values in rows}


def differences(expected: dict, actual: dict) -> dict:
    """Sprint -> (expected, actual) for every sprint missing on one side or differing in any value."""
    return {
        sprint: (expected.get(sprint), actual.get(sprint))
        for sprint in expected.keys() | actual.keys()
        if expected.get(sprint) != actual.get(sprint)
    }


# --- Millisecond boundary check (raw_data/ only, no Postgres) --------------------


def raw(name: str) -> list[dict]:
    return json.loads((RAW_DATA / f"{name}.json").read_text())


def boundaries() -> set[datetime]:
    """Every sprint start and end instant, from raw_data/sprints.json."""
    return {
        datetime.combine(date.fromisoformat(sprint[field]), SPRINT_BOUNDARY, tzinfo=UTC)
        for sprint in raw("sprints")
        for field in ("start_date", "end_date")
    }


def relevant_timestamps() -> dict[str, list[datetime]]:
    """Raw values the metrics compare with a sprint boundary, read as UTC, at full precision."""
    def utc(value: str) -> datetime:
        return datetime.fromisoformat(value).replace(tzinfo=UTC)

    return {
        "membership added_at": [utc(m["added_at"]) for m in raw("sprint_issue_memberships")],
        "issue resolutiondate": [utc(i["resolutiondate"]) for i in raw("issues") if i["resolutiondate"]],
        "transition timestamp": [utc(t["timestamp"]) for t in raw("status_transitions")],
    }


def in_first_millisecond(values: list[datetime], instants: set[datetime]) -> list[datetime]:
    """Values strictly after a boundary but less than 1 ms after it: flooring would move them onto it."""
    return [v for v in values for b in instants if b < v < b + MILLISECOND]


def test_no_raw_timestamp_falls_in_the_first_millisecond_after_a_sprint_boundary() -> None:
    instants = boundaries()
    assert len(instants) == 13  # 12 contiguous sprints per team, all three teams on the same calendar
    for kind, values in relevant_timestamps().items():
        assert values, kind
        assert in_first_millisecond(values, instants) == [], kind


def test_sabotage_a_value_half_a_millisecond_after_a_boundary_is_caught() -> None:
    """Sabotage: a scope add 0.5 ms after a sprint start is caught, and flooring would flip it to committed."""
    start = min(boundaries())
    added_at = start + timedelta(microseconds=500)
    assert in_first_millisecond([added_at], boundaries()) == [added_at]
    floored = added_at.replace(microsecond=added_at.microsecond // 1000 * 1000)
    assert added_at > start and floored <= start


# --- Reconciliation with the warehouse ---------------------------------------------


def test_answer_key_covers_every_sprint() -> None:
    reports = answer_key()
    assert len(reports) == 36
    assert sum(r["total_count"] for r in reports.values()) == 320


@pytest.mark.postgres
def test_sprint_metrics_match_sprint_report_for_every_sprint(warehouse, tmp_path: Path) -> None:
    """Every value sprint_report reports (committed, scope added, completed, hit, bug and issue counts), all 36 sprints."""
    result = run_dbt(tmp_path, "build")
    assert result.returncode == 0, result.stdout
    assert failed_tests(tmp_path) == set()

    expected, actual = answer_key(), warehouse_metrics(warehouse, MARTS)
    assert len(actual) == 36
    assert differences(expected, actual) == {}


CALENDAR_DAYS = {
    "members.added_at <= sprints.start_at": "(members.added_at at time zone 'UTC')::date <= (sprints.start_at at time zone 'UTC')::date",
    "members.added_at > sprints.start_at": "(members.added_at at time zone 'UTC')::date > (sprints.start_at at time zone 'UTC')::date",
    "members.resolved_at <= sprints.end_at": "(members.resolved_at at time zone 'UTC')::date <= (sprints.end_at at time zone 'UTC')::date",
}
"""The original off-by-one-day bug: comparing dates instead of instants."""


@pytest.mark.postgres
def test_sabotage_calendar_days_fail_the_reconciliation(warehouse, tmp_path: Path) -> None:
    """Sabotage: rpt_sprint_metrics with calendar days, in a copy of the dbt project. The reconciliation fails."""
    project = Path(shutil.copytree(DBT_DIR, tmp_path / "dbt", ignore=shutil.ignore_patterns("target", "logs")))
    model = project / "models" / "metrics" / "rpt_sprint_metrics.sql"
    text = model.read_text()
    for exact, by_day in CALENDAR_DAYS.items():
        assert text.count(exact) == 1, exact
        text = text.replace(exact, by_day)
    model.write_text(text)

    result = run_dbt(tmp_path, "build", project_dir=project)
    assert result.returncode == 0, result.stdout

    mismatched = differences(answer_key(), warehouse_metrics(warehouse, MARTS))
    assert len(mismatched) == CALENDAR_DAY_MISMATCHES, sorted(mismatched)
