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

Steps 4.2 and 4.3: the business-question models. Their expected side is
recomputed here in plain Python from raw_data/*.json (json and datetime only),
without the answer key, the simulator, mock_api/ or ingestion/. Counts and
story points are compared exactly. Rates and averages are ratios of exact
counts, compared as fractions to 1e-12; float sums (capacity) to a relative
1e-12, since Postgres and Python may add them in a different order.

Sabotage runs edit a copy of dbt/ (`sabotaged_project`), never the real
project. Postgres tests build into marts_test (fixtures from test_dbt); the
real schemas are never touched.
"""

from __future__ import annotations

import json
import math
import shutil
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest
from psycopg import sql

from simulator.persistence import load_from_json
from simulator.sprint_report import compute_sprint_report
from test_dbt import DBT_DIR, MARTS, TEST_SCHEMA, failed_tests, run_dbt, run_then_test, warehouse  # noqa: F401
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


def sabotaged_project(tmp_path: Path, model: str, replacements: dict[str, str]) -> Path:
    """A copy of dbt/ with `model` edited. Each original text must occur exactly once, so a sabotage can't silently miss."""
    project = Path(shutil.copytree(DBT_DIR, tmp_path / "dbt", ignore=shutil.ignore_patterns("target", "logs")))
    path = project / "models" / "metrics" / f"{model}.sql"
    text = path.read_text()
    for original, broken in replacements.items():
        assert text.count(original) == 1, original
        text = text.replace(original, broken)
    path.write_text(text)
    return project


def build_sabotaged(tmp_path: Path, model: str, replacements: dict[str, str]) -> None:
    """`dbt run` on the sabotaged copy: the models build, and the reconciliation is what must catch the change."""
    result = run_dbt(tmp_path, "run", project_dir=sabotaged_project(tmp_path, model, replacements))
    assert result.returncode == 0, result.stdout


def build(tmp_path: Path) -> None:
    result = run_dbt(tmp_path, "build")
    assert result.returncode == 0, result.stdout
    assert failed_tests(tmp_path) == set()


def test_answer_key_covers_every_sprint() -> None:
    reports = answer_key()
    assert len(reports) == 36
    assert sum(r["total_count"] for r in reports.values()) == 320


@pytest.mark.postgres
def test_sprint_metrics_match_sprint_report_for_every_sprint(warehouse, tmp_path: Path) -> None:
    """Every value sprint_report reports (committed, scope added, completed, hit, bug and issue counts), all 36 sprints."""
    build(tmp_path)
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
    build_sabotaged(tmp_path, "rpt_sprint_metrics", CALENDAR_DAYS)

    mismatched = differences(answer_key(), warehouse_metrics(warehouse, MARTS))
    assert len(mismatched) == CALENDAR_DAY_MISMATCHES, sorted(mismatched)


# --- Step 4.2: business questions 1 to 3, expected side in plain Python ----------

WORKING_DAYS = 10
"""Working days per sprint (docs/domain-model.md, "Fixed sprint length")."""

TOLERANCE = Fraction(1, 10**12)


def utc(value: str) -> datetime:
    """A naive raw_data/ timestamp, read as UTC, at full precision."""
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def boundary(day: str) -> datetime:
    return datetime.combine(date.fromisoformat(day), SPRINT_BOUNDARY, tzinfo=UTC)


class Raw:
    """raw_data/ read once, with sprints named by (team key, sprint name) and ordered by start date."""

    def __init__(self) -> None:
        team_key = {t["team_id"]: t["key"] for t in raw("teams")}
        self.issues = {i["issue_id"]: i for i in raw("issues")}
        self.issue_team = {i["issue_id"]: team_key[i["team_id"]] for i in raw("issues")}
        self.sprints = {s["sprint_id"]: s for s in raw("sprints")}
        self.sprint_name = {s["sprint_id"]: (team_key[s["team_id"]], s["name"]) for s in raw("sprints")}
        self.memberships = raw("sprint_issue_memberships")
        self.transitions = raw("status_transitions")
        self.capacity = {c["sprint_id"]: c for c in raw("capacities")}
        by_team = defaultdict(list)
        for sprint in sorted(raw("sprints"), key=lambda s: s["start_date"]):
            by_team[team_key[sprint["team_id"]]].append(sprint["sprint_id"])
        self.team_sprints = dict(by_team)
        """Team key -> its sprint IDs in start order."""

    def members(self, sprint_id: str) -> list[dict]:
        return [self.issues[m["issue_id"]] for m in self.memberships if m["sprint_id"] == sprint_id]

    def completed(self, sprint_id: str) -> int:
        end = boundary(self.sprints[sprint_id]["end_date"])
        return sum(i["story_points"] for i in self.members(sprint_id) if i["resolutiondate"] and utc(i["resolutiondate"]) <= end)

    def as_of(self) -> datetime:
        """The last event timestamp: issue created or resolved, status transition, sprint join."""
        return max(
            [utc(i["created"]) for i in self.issues.values()]
            + [utc(i["resolutiondate"]) for i in self.issues.values() if i["resolutiondate"]]
            + [utc(t["timestamp"]) for t in self.transitions]
            + [utc(m["added_at"]) for m in self.memberships]
        )


def windows(raw_side: Raw):
    """(sprint ID, the sprint IDs in its 3-sprint window) for every sprint, team by team."""
    for sprint_ids in raw_side.team_sprints.values():
        for n, sprint_id in enumerate(sprint_ids):
            yield sprint_id, sprint_ids[max(0, n - 2): n + 1]


def close(actual, expected: Fraction) -> bool:
    return actual is not None and abs(Fraction(str(actual)) - expected) <= TOLERANCE


def read(connection, model: str, key: str) -> dict:
    """Rows of a marts_test model as dicts, keyed by `key` (a column, or a tuple of columns)."""
    cursor = connection.execute(sql.SQL("select * from {}").format(sql.Identifier(MARTS, model)))
    names = [column.name for column in cursor.description]
    rows = [dict(zip(names, values)) for values in cursor.fetchall()]
    keys = (key,) if isinstance(key, str) else key
    return {tuple(row[k] for k in keys) if len(keys) > 1 else row[key]: row for row in rows}


def by_sprint_name(rows: dict) -> dict:
    return {(row["team_key"], row["sprint_name"]): row for row in rows.values()}


# --- Q1: velocity ------------------------------------------------------------------


def expected_q1(raw_side: Raw) -> dict:
    expected = {}
    for sprint_id, window in windows(raw_side):
        end = boundary(raw_side.sprints[sprint_id]["end_date"])
        late = [i for i in raw_side.members(sprint_id) if i["resolutiondate"] and utc(i["resolutiondate"]) > end]
        expected[raw_side.sprint_name[sprint_id]] = {
            "velocity_points": raw_side.completed(sprint_id),
            "velocity_rolling_3": Fraction(sum(raw_side.completed(s) for s in window), len(window)),
            "sprints_in_window": len(window),
            "completed_after_end_points": sum(i["story_points"] for i in late),
            "completed_after_end_issues": len(late),
        }
    return expected


def q1_differences(connection) -> dict:
    expected, actual = expected_q1(Raw()), by_sprint_name(read(connection, "rpt_q1_velocity", "sprint_id"))
    assert actual.keys() == expected.keys()
    found = {}
    for sprint, values in expected.items():
        row = actual[sprint]
        wrong = [
            column for column, value in values.items()
            if not (close(row[column], value) if isinstance(value, Fraction) else row[column] == value)
        ]
        if wrong:
            found[sprint] = wrong
    return found


def test_q1_late_points_are_the_documented_no_carryover_figures() -> None:
    """65 member issues (343 points) resolve after their sprint ended: 28.1% of the 1,220 points resolved on members."""
    q1 = expected_q1(Raw())
    late = sum(v["completed_after_end_points"] for v in q1.values())
    assert (late, sum(v["completed_after_end_issues"] for v in q1.values())) == (343, 65)
    velocity = sum(v["velocity_points"] for v in q1.values())
    assert velocity == 877
    assert round(100 * late / (late + velocity), 1) == 28.1


@pytest.mark.postgres
def test_q1_velocity_matches_raw_data(warehouse, tmp_path: Path) -> None:
    build(tmp_path)
    assert q1_differences(warehouse) == {}


@pytest.mark.postgres
def test_sabotage_q1_a_four_sprint_window_fails(warehouse, tmp_path: Path) -> None:
    """Sabotage: a 4-sprint window. Only the rolling columns differ, from each team's sprint 4 on."""
    build_sabotaged(tmp_path, "rpt_q1_velocity", {"rows between 2 preceding": "rows between 3 preceding"})
    found = q1_differences(warehouse)
    assert len(found) == 27  # sprints 4 to 12 of each team; sprints 1 to 3 hold at most 3 sprints anyway
    assert {column for columns in found.values() for column in columns} == {"velocity_rolling_3", "sprints_in_window"}


# --- Q2: velocity against capacity ----------------------------------------------------


def expected_q2(raw_side: Raw) -> dict:
    expected = {}
    for sprint_id, window in windows(raw_side):
        capacity = raw_side.capacity[sprint_id]
        available = {s: raw_side.capacity[s]["headcount"] * WORKING_DAYS - raw_side.capacity[s]["pto_days"] for s in window}
        expected[raw_side.sprint_name[sprint_id]] = {
            "headcount": capacity["headcount"],
            "pto_days": capacity["pto_days"],
            "available_person_days": available[sprint_id],
            "completed_points": raw_side.completed(sprint_id),
            "points_per_person_day": raw_side.completed(sprint_id) / available[sprint_id],
            "points_per_person_day_rolling_3": sum(raw_side.completed(s) for s in window) / sum(available.values()),
            "sprints_in_window": len(window),
        }
    return expected


def q2_differences(connection) -> dict:
    expected = expected_q2(Raw())
    actual = by_sprint_name(read(connection, "rpt_q2_velocity_vs_capacity", "sprint_id"))
    assert actual.keys() == expected.keys()
    found = {}
    for sprint, values in expected.items():
        row = actual[sprint]
        wrong = [
            column for column, value in values.items()
            if not (math.isclose(row[column], value, rel_tol=1e-12) if isinstance(value, float) else row[column] == value)
        ]
        if wrong:
            found[sprint] = wrong
    return found


def test_raw_pto_never_exceeds_the_sprints_person_days() -> None:
    capacity = raw("capacities")
    assert len(capacity) == 36
    assert all(0 <= c["pto_days"] <= c["headcount"] * WORKING_DAYS for c in capacity)


@pytest.mark.postgres
def test_q2_velocity_vs_capacity_matches_raw_data(warehouse, tmp_path: Path) -> None:
    build(tmp_path)
    assert q2_differences(warehouse) == {}


@pytest.mark.postgres
def test_sabotage_q2_ignoring_pto_fails(warehouse, tmp_path: Path) -> None:
    """Sabotage: available capacity without PTO. Every sprint has some PTO, so all 36 differ, in the capacity columns only."""
    build_sabotaged(tmp_path, "rpt_q2_velocity_vs_capacity", {
        "capacity.headcount * {{ var('sprint_working_days') }} - capacity.pto_days as available_person_days":
        "capacity.headcount * {{ var('sprint_working_days') }} as available_person_days",
    })
    found = q2_differences(warehouse)
    assert len(found) == 36
    assert {c for columns in found.values() for c in columns} == {
        "available_person_days", "points_per_person_day", "points_per_person_day_rolling_3",
    }


@pytest.mark.postgres
def test_sabotage_pto_above_the_sprints_person_days_fails_the_pto_test(warehouse, tmp_path: Path) -> None:
    """Sabotage: one capacity row with more PTO than headcount x 10. Exactly the PTO test fails."""
    warehouse.execute(
        sql.SQL("update {} set pto_days = headcount * 10 + 0.5 where team_key = 'PHX' and sprint_id = "
                "(select min(sprint_id) from {} where team_key = 'PHX')").format(
            sql.Identifier(TEST_SCHEMA, "capacity"), sql.Identifier(TEST_SCHEMA, "capacity"))
    )
    assert run_then_test(tmp_path) == {"assert_pto_within_sprint_person_days"}


# --- Q3: deadline breach rate ---------------------------------------------------------

HEADLINE = {"Story": 20.4, "Task": 28.9, "Bug": 38.8}
"""Resolved-only breach rates in percent (docs/domain-model.md; D-020)."""


def expected_outcomes(raw_side: Raw) -> dict:
    """Issue key -> (team key, sprint name, commitment, outcome) for every issue with a duedate."""
    as_of_day = raw_side.as_of().date()
    first = {}
    for m in sorted(raw_side.memberships, key=lambda m: m["added_at"]):
        first.setdefault(m["issue_id"], m)
    outcomes = {}
    for issue in raw_side.issues.values():
        if issue["duedate"] is None:
            continue
        membership = first[issue["issue_id"]]
        start = boundary(raw_side.sprints[membership["sprint_id"]]["start_date"])
        commitment = "committed" if utc(membership["added_at"]) <= start else "scope_added"
        due = date.fromisoformat(issue["duedate"])
        if issue["resolutiondate"]:
            outcome = "late" if utc(issue["resolutiondate"]).date() > due else "on_time"
        else:
            outcome = "open_overdue" if due < as_of_day else "open_not_due"
        team, sprint = raw_side.sprint_name[membership["sprint_id"]]
        outcomes[issue["key"]] = (issue["issue_type"], team, sprint, commitment, outcome)
    return outcomes


def expected_breach_rates(outcomes: dict) -> dict:
    """(team key or ALL, issue type or 'All types', commitment or 'All') -> outcome counts, for every rollup."""
    cells = defaultdict(lambda: defaultdict(int))
    for issue_type, team, _, commitment, outcome in outcomes.values():
        for t in (team, "ALL"):
            for i in (issue_type, "All types"):
                for c in (commitment, "All"):
                    cells[(t, i, c)][outcome] += 1
    return {key: dict(counts) for key, counts in cells.items()}


def q3_differences(connection) -> dict:
    """Every outcome or rate cell that differs from raw_data/, plus the as-of instant."""
    raw_side = Raw()
    outcomes = expected_outcomes(raw_side)
    found = {}

    detail = read(connection, "rpt_q3_deadline_outcomes", "issue_key")
    actual = {k: (r["issue_type"], r["team_key"], r["sprint_name"], r["commitment"], r["outcome"]) for k, r in detail.items()}
    found.update({k: (outcomes.get(k), actual.get(k)) for k in outcomes.keys() | actual.keys() if outcomes.get(k) != actual.get(k)})

    as_of = read(connection, "rpt_data_as_of", "as_of_date")
    expected_as_of = raw_side.as_of()
    expected_as_of = expected_as_of.replace(microsecond=expected_as_of.microsecond // 1000 * 1000)
    if [row["as_of_at"] for row in as_of.values()] != [expected_as_of]:
        found["as_of"] = (expected_as_of, list(as_of))

    rates = read(connection, "rpt_q3_breach_rate", ("team_key", "issue_type", "commitment"))
    expected_cells = expected_breach_rates(outcomes)
    if rates.keys() != expected_cells.keys():
        found["cells"] = sorted(rates.keys() ^ expected_cells.keys())
    for cell in rates.keys() & expected_cells.keys():
        counts, row = expected_cells[cell], rates[cell]
        late, on_time, overdue = (counts.get(o, 0) for o in ("late", "on_time", "open_overdue"))
        expected_row = {
            "due_issue_count": sum(counts.values()), "resolved_count": late + on_time, "on_time_count": on_time,
            "late_count": late, "open_overdue_count": overdue, "open_not_due_count": counts.get("open_not_due", 0),
        }
        wrong = [column for column, value in expected_row.items() if row[column] != value]
        resolved_rate = Fraction(late, late + on_time) if late + on_time else None
        overdue_rate = Fraction(late + overdue, late + on_time + overdue) if late + on_time + overdue else None
        for column, value in (("breach_rate_resolved", resolved_rate), ("breach_rate_incl_overdue", overdue_rate)):
            if not (row[column] is None if value is None else close(row[column], value)):
                wrong.append(column)
        if wrong:
            found[cell] = wrong
    return found


def headline(connection) -> dict[str, float]:
    rates = read(connection, "rpt_q3_breach_rate", ("team_key", "issue_type", "commitment"))
    return {t: round(100 * float(rates[("ALL", t, "All")]["breach_rate_resolved"]), 1) for t in HEADLINE}


def test_q3_raw_data_gives_the_documented_headline() -> None:
    cells = expected_breach_rates(expected_outcomes(Raw()))
    rates = {t: round(100 * cells[("ALL", t, "All")]["late"] / (cells[("ALL", t, "All")]["late"] + cells[("ALL", t, "All")]["on_time"]), 1)
             for t in HEADLINE}
    assert rates == HEADLINE


@pytest.mark.postgres
def test_q3_breach_rates_match_raw_data_and_the_headline(warehouse, tmp_path: Path) -> None:
    build(tmp_path)
    assert q3_differences(warehouse) == {}
    assert headline(warehouse) == HEADLINE


@pytest.mark.postgres
def test_sabotage_q3_a_flipped_breach_comparison_fails(warehouse, tmp_path: Path) -> None:
    """Sabotage: late becomes resolved on or before the due date. The headline rates and the reconciliation fail."""
    build_sabotaged(tmp_path, "rpt_q3_deadline_outcomes", {
        "(issues.resolved_at at time zone 'UTC')::date > issues.duedate then 'late'":
        "(issues.resolved_at at time zone 'UTC')::date <= issues.duedate then 'late'",
    })
    assert headline(warehouse) == {"Story": 79.6, "Task": 71.1, "Bug": 61.2}
    found = q3_differences(warehouse)
    assert len([k for k in found if isinstance(k, str) and k != "as_of"]) == 313  # every resolved issue flips
    assert "as_of" not in found


@pytest.mark.postgres
def test_the_as_of_instant_follows_the_data(warehouse, tmp_path: Path) -> None:
    """A later event moves the as-of instant: it is derived, not hardcoded (D-036)."""
    later = datetime(2024, 7, 6, 12, 0, tzinfo=UTC)
    warehouse.execute(
        sql.SQL("update {} set transitioned_at = %s where changelog_id = (select max(changelog_id) from {})").format(
            sql.Identifier(TEST_SCHEMA, "status_transitions"), sql.Identifier(TEST_SCHEMA, "status_transitions")),
        (later,),
    )
    assert run_dbt(tmp_path, "run").returncode == 0
    assert [r["as_of_at"] for r in read(warehouse, "rpt_data_as_of", "as_of_date").values()] == [later]
