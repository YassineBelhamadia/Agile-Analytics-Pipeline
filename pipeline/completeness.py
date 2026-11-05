"""Check that no records were lost between pipeline stages (D-044).

This is a production check, separate from the tests: the runner calls it after
each stage of a real run. It compares counts between adjacent stages only,
never with fixed numbers, so another seed passes too. The fixed SEED=22 numbers
belong to the tests.

Landed records are counted here from the raw JSON, without `ingestion.parse`,
so the check cannot share a bug with the code it checks.
"""

from __future__ import annotations

import json
from pathlib import Path

import psycopg
from psycopg import sql

from ingestion.extract import MANIFEST

STAGE_LANDING = "extraction"
STAGE_STAGING = "landing to staging"
STAGE_MARTS = "staging to marts"
STAGE_REPORTS = "marts to rpt_ tables"

SPRINT_FIELD_ID = "customfield_10020"
STAGING = "staging"
MARTS = "marts"

MARTS_FROM_STAGING = {
    "dim_team": "teams",
    "dim_user": "users",
    "dim_sprint": "sprints",
    "dim_issue": "issues",
    "fct_status_transitions": "status_transitions",
    "fct_sprint_membership": "sprint_memberships",
    "fct_capacity": "capacity",
}
"""Every dimension and fact, and the staging table it mirrors row for row."""


class CompletenessError(RuntimeError):
    """A stage holds a different number of records from the one before it."""


def _raise_if(stage: str, problems: list[str]) -> None:
    if problems:
        raise CompletenessError(f"Completeness check failed at stage '{stage}': " + "; ".join(problems))


def _compare(name: str, actual: int, source: str, expected: int, unit: str = "rows") -> list[str]:
    """One problem if the counts differ: both counts, and where each came from."""
    return [] if actual == expected else [f"{name} has {actual:,} rows, {source} has {expected:,} {unit}"]


def check_landing(landing_dir: Path | str) -> dict[str, int]:
    """Check the manifest against the files, and count the landed records.

    Returns:
        The landed counts, which `check_staging` compares with staging.

    Raises:
        CompletenessError: If a manifest entry has no file, a file has no
            manifest entry, a scratch directory was left behind, or an issue
            or sprint has no page of its own.
    """
    landing_dir = Path(landing_dir)
    manifest = landing_dir / MANIFEST
    if not manifest.is_file():
        _raise_if(STAGE_LANDING, [f"{manifest} does not exist"])

    listed = {entry["file"] for entry in json.loads(manifest.read_text())["files"]}
    on_disk = {p.relative_to(landing_dir).as_posix() for p in landing_dir.rglob("*") if p.is_file()} - {MANIFEST}
    leftovers = [f".{landing_dir.name}.{kind}" for kind in ("partial", "previous")]
    _raise_if(
        STAGE_LANDING,
        [f"{file} is in the manifest ({len(listed):,} entries) but not on disk ({len(on_disk):,} pages)" for file in sorted(listed - on_disk)]
        + [f"{file} is on disk ({len(on_disk):,} pages) but not in the manifest ({len(listed):,} entries)" for file in sorted(on_disk - listed)]
        + [f"{name} was left behind" for name in leftovers if (landing_dir.parent / name).exists()],
    )

    pages: dict[str, list[dict]] = {}
    for file in sorted(listed):
        pages.setdefault(file.split("/")[0], []).append(json.loads((landing_dir / file).read_text()))

    def records(folder: str, key: str) -> list[dict]:
        return [record for page in pages.get(folder, []) for record in page[key]]

    boards = records("boards", "values")
    sprints = records("board_sprints", "values")
    issues = records("search", "issues")
    histories = records("changelogs", "values")
    items = [item for history in histories for item in history["items"]]
    people = [history.get("author") for history in histories]
    people += [issue["fields"].get(role) for issue in issues for role in ("assignee", "reporter")]

    def folders(name: str) -> set[str]:
        return {file.split("/")[1] for file in listed if file.startswith(f"{name}/")}

    issue_keys, sprint_ids = {issue["key"] for issue in issues}, {str(sprint["id"]) for sprint in sprints}
    changelogs, sprint_lists = folders("changelogs"), folders("sprint_issues")
    _raise_if(
        STAGE_LANDING,
        ([] if changelogs == issue_keys else [f"{len(changelogs):,} changelog folders do not match {len(issue_keys):,} landed issues"])
        + ([] if sprint_lists == sprint_ids else [f"{len(sprint_lists):,} sprint_issues folders do not match {len(sprint_ids):,} landed sprints"]),
    )

    return {
        "manifest_entries": len(listed),
        "projects": len({board["location"]["projectKey"] for board in boards}),
        "users": len({person["accountId"] for person in people if person}),
        "boards": len(boards),
        "sprints": len(sprints),
        "capacity": len(records("capacity", "values")),
        "issues": len(issues),
        "status_items": sum(item["field"] == "status" for item in items),
        "sprint_items": sum(item.get("fieldId") == SPRINT_FIELD_ID for item in items),
        "sprint_issue_entries": len(records("sprint_issues", "issues")),
    }


def _count(conn: psycopg.Connection, schema: str, table: str, where: str = "true") -> int:
    query = sql.SQL("select count(*) from {} where " + where).format(sql.Identifier(schema, table))
    return conn.execute(query).fetchone()[0]


def check_staging(conn: psycopg.Connection, landed: dict[str, int]) -> dict[str, int]:
    """Compare each staging table with the landed records it comes from.

    `sprint_memberships` equals both landed sources because v1 has no
    carryover: an issue never leaves a sprint. A version with carryover
    changes this rule.
    """
    sources = [
        ("teams", "landing", landed["projects"], "distinct board project keys"),
        ("users", "landing", landed["users"], "distinct account IDs"),
        ("boards", "landing", landed["boards"], "boards"),
        ("sprints", "landing", landed["sprints"], "sprints"),
        ("capacity", "landing", landed["capacity"], "capacity rows"),
        ("issues", "landing", landed["issues"], "issues"),
        ("status_transitions", "landing", landed["status_items"], "status changelog items"),
        ("sprint_memberships", "landing", landed["sprint_items"], "Sprint changelog items"),
        ("sprint_memberships", "landing", landed["sprint_issue_entries"], "sprint-issue entries"),
    ]
    counts = {table: _count(conn, STAGING, table) for table, *_ in sources}
    _raise_if(STAGE_STAGING, [
        problem
        for table, source, expected, unit in sources
        for problem in _compare(f"{STAGING}.{table}", counts[table], source, expected, unit)
    ])
    return counts


def check_marts(conn: psycopg.Connection) -> dict[str, int]:
    """Compare every dimension and fact with its staging table."""
    counts = {mart: _count(conn, MARTS, mart) for mart in MARTS_FROM_STAGING}
    _raise_if(STAGE_MARTS, [
        problem
        for mart, table in MARTS_FROM_STAGING.items()
        for problem in _compare(f"{MARTS}.{mart}", counts[mart], f"{STAGING}.{table}", _count(conn, STAGING, table))
    ])
    return counts


def check_reports(conn: psycopg.Connection) -> dict[str, int]:
    """Compare every rpt_ table with the marts it is built from."""
    sprints, teams = _count(conn, MARTS, "dim_sprint"), _count(conn, MARTS, "dim_team")
    due = _count(conn, MARTS, "dim_issue", "duedate is not null")
    transitions = _count(conn, MARTS, "fct_status_transitions")
    non_epics = _count(conn, MARTS, "dim_issue", "issue_type <> 'Epic'")
    sources = [
        ("rpt_sprint_metrics", "marts.dim_sprint", sprints, "rows"),
        ("rpt_data_as_of", "the single as-of instant", 1, "row"),
        ("rpt_q1_velocity", "marts.dim_sprint", sprints, "rows"),
        ("rpt_q2_velocity_vs_capacity", "marts.dim_sprint", sprints, "rows"),
        ("rpt_q3_deadline_outcomes", "marts.dim_issue", due, "issues with a due date"),
        ("rpt_q4_status_stays", "marts.fct_status_transitions + non-Epic issues", transitions + non_epics, "stays"),
        ("rpt_q5_bug_to_story_ratio", "marts.dim_sprint + marts.dim_team", sprints + teams, "periods"),
        ("rpt_q6_team_portfolio", "marts.dim_team", teams, "rows"),
    ]
    counts = {table: _count(conn, MARTS, table) for table, *_ in sources}
    counts["rpt_q3_breach_rate"] = _count(conn, MARTS, "rpt_q3_breach_rate")
    counts["rpt_q4_time_in_status"] = _count(conn, MARTS, "rpt_q4_time_in_status")

    problems = [
        problem
        for table, source, expected, unit in sources
        for problem in _compare(f"{MARTS}.{table}", counts[table], source, expected, unit)
    ]
    grand_total = conn.execute(
        sql.SQL("select coalesce(sum(due_issue_count), 0) from {} where team_key = 'ALL' and issue_type = 'All types' and commitment = 'All'")
        .format(sql.Identifier(MARTS, "rpt_q3_breach_rate"))
    ).fetchone()[0]
    if grand_total != due:
        problems.append(f"the {MARTS}.rpt_q3_breach_rate grand total counts {grand_total:,} issues, marts.dim_issue has {due:,} issues with a due date")
    if counts["rpt_q4_time_in_status"] == 0:
        problems.append(f"{MARTS}.rpt_q4_time_in_status has 0 rows, marts.rpt_q4_status_stays has {counts['rpt_q4_status_stays']:,} rows")
    _raise_if(STAGE_REPORTS, problems)
    return counts
