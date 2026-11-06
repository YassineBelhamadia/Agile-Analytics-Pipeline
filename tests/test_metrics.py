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
project. Tests that only read use `built_warehouse` (conftest.py), built once;
sabotage runs build into marts_test (`warehouse`, from test_dbt). The real
schemas are never touched.
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
from test_staging import RAW_DATA

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
        "issue created": [utc(i["created"]) for i in raw("issues")],  # Q5 periods (step 4.3)
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


def test_answer_key_covers_every_sprint() -> None:
    reports = answer_key()
    assert len(reports) == 36
    assert sum(r["total_count"] for r in reports.values()) == 320


@pytest.mark.postgres
def test_sprint_metrics_match_sprint_report_for_every_sprint(built_warehouse) -> None:
    """Every value sprint_report reports (committed, scope added, completed, hit, bug and issue counts), all 36 sprints."""
    expected, actual = answer_key(), warehouse_metrics(built_warehouse, MARTS)
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
def test_q1_velocity_matches_raw_data(built_warehouse) -> None:
    assert q1_differences(built_warehouse) == {}


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
def test_q2_velocity_vs_capacity_matches_raw_data(built_warehouse) -> None:
    assert q2_differences(built_warehouse) == {}


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
def test_q3_breach_rates_match_raw_data_and_the_headline(built_warehouse) -> None:
    assert q3_differences(built_warehouse) == {}
    assert headline(built_warehouse) == HEADLINE


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


# --- Step 5.3: rollup rows are flagged (D-047) -----------------------------------------

ROLLUP_SPLIT = {"rpt_q3_breach_rate": {False: 18, True: 30}, "rpt_q4_time_in_status": {False: 12, True: 4}}
"""Detail and rollup rows per table, from the group by cube and grouping sets (D-039, D-040)."""


def rollup_split(connection, model: str) -> dict[bool, int]:
    rows = connection.execute(
        sql.SQL("select is_rollup, count(*) from {} group by is_rollup").format(sql.Identifier(MARTS, model))
    ).fetchall()
    return dict(rows)


def detail_sum(connection, model: str, column: str) -> int:
    return connection.execute(
        sql.SQL("select sum({}) from {} where not is_rollup").format(sql.Identifier(column), sql.Identifier(MARTS, model))
    ).fetchone()[0]


@pytest.mark.postgres
def test_is_rollup_splits_the_rows_and_the_detail_rows_add_up(built_warehouse) -> None:
    assert {model: rollup_split(built_warehouse, model) for model in ROLLUP_SPLIT} == ROLLUP_SPLIT
    assert detail_sum(built_warehouse, "rpt_q3_breach_rate", "due_issue_count") == 320
    all_teams = built_warehouse.execute(
        sql.SQL("select sum(stay_count) from {} where team_key = 'ALL'").format(sql.Identifier(MARTS, "rpt_q4_time_in_status"))
    ).fetchone()[0]
    assert detail_sum(built_warehouse, "rpt_q4_time_in_status", "stay_count") == all_teams


@pytest.mark.postgres
def test_sabotage_all_teams_rows_not_flagged_fails_the_rollup_test(warehouse, tmp_path: Path) -> None:
    """Sabotage: the Q4 "All teams" rows are not flagged. The dbt test fails, and a sum over the detail rows doubles."""
    project = sabotaged_project(tmp_path, "rpt_q4_time_in_status", {"grouping(team_name) = 1                                               as is_rollup": "false as is_rollup"})
    assert run_dbt(tmp_path, "run", project_dir=project).returncode == 0
    assert run_dbt(tmp_path, "test", "--select", "assert_rollup_flag_matches_labels", project_dir=project).returncode != 0
    assert failed_tests(tmp_path) == {"assert_rollup_flag_matches_labels"}
    assert rollup_split(warehouse, "rpt_q4_time_in_status") == {False: 16}
    teams = warehouse.execute(
        sql.SQL("select sum(stay_count) from {} where team_key <> 'ALL'").format(sql.Identifier(MARTS, "rpt_q4_time_in_status"))
    ).fetchone()[0]
    assert detail_sum(warehouse, "rpt_q4_time_in_status", "stay_count") == 2 * teams


# --- Step 4.3: Q4 time per status --------------------------------------------------

HOURS_TOLERANCE = 1e-9
"""Hours. Far below the data's 1 ms resolution (2.8e-7 h), above float rounding in Postgres' percentile_cont."""


def floor_ms(value: datetime) -> datetime:
    """The contract's millisecond rule (D-024): the warehouse holds raw timestamps floored to milliseconds."""
    return value.replace(microsecond=value.microsecond // 1000 * 1000)


def hours(start: datetime, end: datetime) -> Fraction:
    delta = floor_ms(end) - floor_ms(start)
    return Fraction(delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds, 3_600_000_000)


def expected_stays(raw_side: Raw) -> dict:
    """(issue key, stay number) -> (team, status, visit, kind, duration hours or None) for every non-Epic issue."""
    as_of = raw_side.as_of()
    by_issue = defaultdict(list)
    for t in raw_side.transitions:
        by_issue[t["issue_id"]].append(t)
    stays = {}
    for issue in raw_side.issues.values():
        if issue["issue_type"] == "Epic":
            continue
        history = sorted(by_issue[issue["issue_id"]], key=lambda t: t["timestamp"])
        entries = [(history[0]["from_status"] if history else issue["status"], utc(issue["created"]))]
        entries += [(t["to_status"], utc(t["timestamp"])) for t in history]
        visits = defaultdict(int)
        for n, (status, entered) in enumerate(entries):
            visits[status] += 1
            if n + 1 < len(entries):
                kind, length = "completed", hours(entered, entries[n + 1][1])
            elif status == "Done":
                kind, length = "terminal", None
            else:
                kind, length = "open", hours(entered, as_of)
            stays[(issue["key"], n)] = (raw_side.issue_team[issue["issue_id"]], status, visits[status], kind, length)
    return stays


def percentile(values: list[Fraction], p: Fraction) -> Fraction:
    """Continuous percentile with linear interpolation, as Postgres' percentile_cont."""
    ordered = sorted(values)
    position = p * (len(ordered) - 1)
    lower = math.floor(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (position - lower) * (ordered[upper] - ordered[lower])


def expected_time_in_status(stays: dict) -> dict:
    """(team key or ALL, status) -> statistics of completed stays and counts of open ones."""
    groups = defaultdict(list)
    for (key, _), (team, status, visit, kind, length) in stays.items():
        if kind == "terminal":
            continue
        for t in (team, "ALL"):
            groups[(t, status)].append((key, visit, kind, length))
    expected = {}
    for cell, rows in groups.items():
        done = [r for r in rows if r[2] == "completed"]
        durations = [r[3] for r in done]
        ages = [r[3] for r in rows if r[2] == "open"]
        expected[cell] = {
            "stay_count": len(done),
            "issue_count": len({r[0] for r in done}),
            "repeat_visit_count": sum(1 for r in done if r[1] > 1),
            "mean_hours": sum(durations) / len(durations),
            "median_hours": percentile(durations, Fraction(1, 2)),
            "p90_hours": percentile(durations, Fraction(9, 10)),
            "open_stay_count": len(ages),
            "open_median_age_hours": percentile(ages, Fraction(1, 2)) if ages else None,
        }
    return expected


def near(actual, expected) -> bool:
    if expected is None or actual is None:
        return actual is None and expected is None
    if isinstance(expected, Fraction):
        return abs(float(actual) - float(expected)) <= HOURS_TOLERANCE
    return actual == expected


def q4_differences(connection) -> dict:
    stays = expected_stays(Raw())
    found = {}
    detail = read(connection, "rpt_q4_status_stays", ("issue_key", "stay_number"))
    actual = {k: (r["team_key"], r["status_name"], r["visit_number"], r["stay_kind"],
                  r["duration_hours"] if r["stay_kind"] == "completed" else r["open_age_hours"]) for k, r in detail.items()}
    for key in stays.keys() | actual.keys():
        e, a = stays.get(key), actual.get(key)
        if e is None or a is None or e[:4] != a[:4] or not near(a[4], e[4]):
            found[key] = (e, a)

    summary = read(connection, "rpt_q4_time_in_status", ("team_key", "status_name"))
    expected = expected_time_in_status(stays)
    if summary.keys() != expected.keys():
        found["cells"] = sorted(summary.keys() ^ expected.keys())
    for cell in summary.keys() & expected.keys():
        wrong = [column for column, value in expected[cell].items() if not near(summary[cell][column], value)]
        if wrong:
            found[cell] = wrong
    return found


def test_q4_raw_histories_start_in_to_do_and_never_leave_done() -> None:
    """The assumptions the stay model rests on, checked on raw_data/: no ties, a To Do start, no exit from Done."""
    by_issue = defaultdict(list)
    for t in raw("status_transitions"):
        by_issue[t["issue_id"]].append(t)
    for history in by_issue.values():
        assert len({t["timestamp"] for t in history}) == len(history)
        history.sort(key=lambda t: t["timestamp"])
        assert history[0]["from_status"] == "To Do"
        assert all(a["to_status"] == b["from_status"] for a, b in zip(history, history[1:]))
        assert all(t["from_status"] != "Done" for t in history)


@pytest.mark.postgres
def test_q4_time_in_status_matches_raw_data(built_warehouse) -> None:
    """All 1,988 stays and all 16 statistic cells, with durations in calendar hours."""
    assert q4_differences(built_warehouse) == {}


@pytest.mark.postgres
def test_sabotage_q4_starting_the_first_stay_at_the_first_transition_fails(warehouse, tmp_path: Path) -> None:
    """Sabotage: the first stay starts at the first transition instead of creation. Every To Do stay collapses to 0 h."""
    build_sabotaged(tmp_path, "rpt_q4_status_stays", {
        "issues.created_at as entered_at": "coalesce(first_transition.transitioned_at, issues.created_at) as entered_at",
    })
    found = q4_differences(warehouse)
    to_do_cells = {cell for cell in found if isinstance(cell[0], str) and cell[1] == "To Do" and len(cell) == 2}
    assert to_do_cells == {("ALL", "To Do"), ("PHX", "To Do"), ("NMD", "To Do"), ("ATL", "To Do")}
    assert not any(cell[1] in ("In Progress", "Code Review", "QA") for cell in found if isinstance(cell[1], str))


# --- Q5: bug-to-story ratio ---------------------------------------------------------


def expected_q5(raw_side: Raw) -> dict:
    """(team key, period number) -> bug, story and task counts, by the sprint window each issue was created in."""
    expected = {}
    for team, sprint_ids in raw_side.team_sprints.items():
        windows_ = [(0, None, boundary(raw_side.sprints[sprint_ids[0]]["start_date"]))]
        windows_ += [(n, boundary(raw_side.sprints[s]["start_date"]), boundary(raw_side.sprints[s]["end_date"]))
                     for n, s in enumerate(sprint_ids, start=1)]
        for number, start, end in windows_:
            created = [i for i in raw_side.issues.values()
                       if raw_side.issue_team[i["issue_id"]] == team
                       and (start is None or utc(i["created"]) >= start) and utc(i["created"]) < end]
            counts = {t: sum(1 for i in created if i["issue_type"] == t) for t in ("Bug", "Story", "Task")}
            expected[(team, number)] = {"bug_count": counts["Bug"], "story_count": counts["Story"], "task_count": counts["Task"]}
    for team in raw_side.team_sprints:
        bugs = stories = 0
        for number in range(13):
            cell = expected[(team, number)]
            bugs, stories = bugs + cell["bug_count"], stories + cell["story_count"]
            cell["bug_to_story_ratio"] = Fraction(cell["bug_count"], cell["story_count"]) if cell["story_count"] else None
            cell["cumulative_bug_to_story_ratio"] = Fraction(bugs, stories) if stories else None
    return expected


def q5_differences(connection) -> dict:
    expected = expected_q5(Raw())
    actual = read(connection, "rpt_q5_bug_to_story_ratio", ("team_key", "period_number"))
    if actual.keys() != expected.keys():
        return {"cells": sorted(actual.keys() ^ expected.keys())}
    found = {}
    for cell, values in expected.items():
        wrong = [
            column for column, value in values.items()
            if not ((actual[cell][column] is None) if value is None
                    else close(actual[cell][column], value) if isinstance(value, Fraction)
                    else actual[cell][column] == value)
        ]
        if wrong:
            found[cell] = wrong
    return found


def test_q5_periods_cover_every_created_issue() -> None:
    """13 periods per team hold all 500 non-Epic issues: none is created outside them."""
    expected = expected_q5(Raw())
    assert len(expected) == 39
    assert sum(c["bug_count"] + c["story_count"] + c["task_count"] for c in expected.values()) == 500


@pytest.mark.postgres
def test_q5_bug_to_story_ratio_matches_raw_data(built_warehouse) -> None:
    assert q5_differences(built_warehouse) == {}


@pytest.mark.postgres
def test_sabotage_q5_counting_only_scheduled_issues_fails(warehouse, tmp_path: Path) -> None:
    """Sabotage: count only issues that joined a sprint (the option D-020 rejected). The counts and ratios differ."""
    build_sabotaged(tmp_path, "rpt_q5_bug_to_story_ratio", {
        "where issue_type in ('Bug', 'Story', 'Task')":
        "where issue_type in ('Bug', 'Story', 'Task') and issue_id in (select issue_id from {{ ref('fct_sprint_membership') }})",
    })
    found = q5_differences(warehouse)
    assert found
    assert {c for columns in found.values() for c in columns} >= {"bug_count", "story_count", "bug_to_story_ratio"}


# --- Q6: portfolio ------------------------------------------------------------------


def expected_q6(connection) -> dict:
    """Team key -> the portfolio values, read from rpt_sprint_metrics and the Q1 to Q5 tables and combined here."""
    sprints = read(connection, "rpt_sprint_metrics", "sprint_id").values()
    q1 = read(connection, "rpt_q1_velocity", "sprint_id").values()
    q2 = read(connection, "rpt_q2_velocity_vs_capacity", "sprint_id").values()
    q3 = read(connection, "rpt_q3_breach_rate", ("team_key", "issue_type", "commitment"))
    q4 = read(connection, "rpt_q4_time_in_status", ("team_key", "status_name"))
    q5 = read(connection, "rpt_q5_bug_to_story_ratio", ("team_key", "period_number")).values()
    expected = {}
    for team in ("PHX", "NMD", "ATL"):
        mine = lambda rows: [r for r in rows if r["team_key"] == team]  # noqa: E731
        s, v, c, r = mine(sprints), mine(q1), mine(q2), mine(q5)
        breach = q3[(team, "All types", "All")]
        expected[team] = {
            "sprint_count": len(s),
            "mean_headcount": Fraction(sum(x["headcount"] for x in c), len(c)),
            "available_person_days": sum(x["available_person_days"] for x in c),
            "pto_share": sum(x["pto_days"] for x in c) / sum(x["headcount"] * WORKING_DAYS for x in c),
            "velocity_per_sprint": Fraction(sum(int(x["velocity_points"]) for x in v), len(v)),
            "points_per_person_day": float(sum(x["completed_points"] for x in c)) / sum(x["available_person_days"] for x in c),
            "hit_count": sum(1 for x in s if x["is_hit"]),
            "hit_rate": Fraction(sum(1 for x in s if x["is_hit"]), len(s)),
            "breach_rate_resolved": breach["breach_rate_resolved"],
            "breach_rate_incl_overdue": breach["breach_rate_incl_overdue"],
            "open_overdue_count": breach["open_overdue_count"],
            "median_hours_to_do": q4[(team, "To Do")]["median_hours"],
            "median_hours_in_progress": q4[(team, "In Progress")]["median_hours"],
            "median_hours_code_review": q4[(team, "Code Review")]["median_hours"],
            "median_hours_qa": q4[(team, "QA")]["median_hours"],
            "bug_to_story_ratio": Fraction(sum(x["bug_count"] for x in r), sum(x["story_count"] for x in r)),
        }
    return expected


def q6_differences(connection) -> dict:
    expected = expected_q6(connection)
    actual = read(connection, "rpt_q6_team_portfolio", "team_key")
    assert actual.keys() == expected.keys()
    found = {}
    for team, values in expected.items():
        wrong = []
        for column, value in values.items():
            got = actual[team][column]
            if isinstance(value, Fraction):
                ok = close(got, value)
            elif isinstance(value, float):
                ok = math.isclose(got, value, rel_tol=1e-12)
            else:
                ok = got == value
            if not ok:
                wrong.append(column)
        if wrong:
            found[team] = wrong
    return found


@pytest.mark.postgres
def test_q6_portfolio_equals_the_q1_to_q5_values(built_warehouse) -> None:
    assert q6_differences(built_warehouse) == {}


@pytest.mark.postgres
def test_sabotage_q6_averaging_the_sprint_ratios_fails(warehouse, tmp_path: Path) -> None:
    """Sabotage: capacity use as the mean of the sprint ratios instead of the capacity-weighted total. All 3 teams differ."""
    build_sabotaged(tmp_path, "rpt_q6_team_portfolio", {
        "sum(completed_points) / sum(available_person_days) as points_per_person_day":
        "avg(points_per_person_day) as points_per_person_day",
    })
    assert q6_differences(warehouse) == {team: ["points_per_person_day"] for team in ("PHX", "NMD", "ATL")}
