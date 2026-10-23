"""Reconcile staging with raw_data/ row by row (step 2.3), plus sabotage checks.

Independence. The expected side is built from raw_data/*.json with plain
`json` and `datetime` only: nothing from mock_api/ (no serializer, no ID
hashing) and no ingestion.parse helper. The staging side is read back from
Postgres after a normal load. Both sides meet on natural keys, because
staging holds Jira IDs and raw_data/ holds the simulator's internal ones:

- team: project key (raw `teams.key`);
- user: display name (unique in raw_data/);
- issue: issue key;
- sprint: team key + sprint name (unique in raw_data/).

Timestamp rule. raw_data/ timestamps are naive UTC with microseconds. The
API truncates them to milliseconds (floor, never rounding) and labels them
UTC. So every raw timestamp is read as UTC and truncated to milliseconds,
then compared for exact equality. In the shipped data this affects 2,403 of
2,651 raw timestamps; `test_timestamp_rule_is_truncation_to_milliseconds`
shows that rounding instead would fail on 1,236 of them.

Postgres tests load into `staging_test` only (fixtures from test_staging).
"""

from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import psycopg
import pytest

from ingestion import load_staging as loader
from ingestion import parse
from test_staging import RAW_DATA, TEST_SCHEMA, conn, landing, landing_copy  # noqa: F401  (fixtures)

SPRINT_BOUNDARY = time(9, 0)
"""Sprints start and end at 09:00 UTC on their dates (docs/domain-model.md, sprint metrics section)."""

MILLISECOND = timedelta(milliseconds=1)


# --- raw_data/ side ---------------------------------------------------------------


def raw(name: str) -> list[dict]:
    return json.loads((RAW_DATA / f"{name}.json").read_text())


def truncate_ms(value: datetime) -> datetime:
    return value.replace(microsecond=value.microsecond // 1000 * 1000)


def round_ms(value: datetime) -> datetime:
    """Rounding, the rule the API does NOT use; only for showing the difference."""
    return truncate_ms(value + timedelta(microseconds=500))


def raw_instant(value: str | None, rule=truncate_ms) -> datetime | None:
    """A naive raw_data/ timestamp, read as UTC, at the API's millisecond precision."""
    return None if value is None else rule(datetime.fromisoformat(value).replace(tzinfo=UTC))


def sprint_instant(day: str) -> datetime:
    return datetime.combine(date.fromisoformat(day), SPRINT_BOUNDARY, tzinfo=UTC)


def expected_rows(rule=truncate_ms) -> dict[str, list[tuple]]:
    """Every comparable staging row, built from raw_data/ alone, keyed by natural keys."""
    teams = {t["team_id"]: t for t in raw("teams")}
    users = {u["user_id"]: u["display_name"] for u in raw("users")}
    sprints = {s["sprint_id"]: s for s in raw("sprints")}
    issues = {i["issue_id"]: i for i in raw("issues")}
    team_key = lambda team_id: teams[team_id]["key"]  # noqa: E731
    sprint_ref = lambda sprint_id: (team_key(sprints[sprint_id]["team_id"]), sprints[sprint_id]["name"])  # noqa: E731
    user = lambda user_id: None if user_id is None else users[user_id]  # noqa: E731
    return {
        "teams": [(t["key"], t["name"]) for t in teams.values()],
        "users": [(name,) for name in users.values()],
        "boards": [(t["key"],) for t in teams.values()],  # no boards in raw_data/; the mock serves one per team
        "sprints": [
            (*sprint_ref(s["sprint_id"]), s["state"], sprint_instant(s["start_date"]), sprint_instant(s["end_date"]))
            for s in sprints.values()
        ],
        "capacity": [
            (team_key(c["team_id"]), *sprint_ref(c["sprint_id"]), c["headcount"], c["pto_days"]) for c in raw("capacities")
        ],
        "issues": [
            (
                i["key"], team_key(i["team_id"]), i["issue_type"], i["status"], i["story_points"],
                None if i["duedate"] is None else date.fromisoformat(i["duedate"]),
                raw_instant(i["created"], rule), raw_instant(i["resolutiondate"], rule),
                None if i["parent_id"] is None else issues[i["parent_id"]]["key"],
                user(i["assignee_id"]), user(i["reporter_id"]),
            )
            for i in issues.values()
        ],
        "status_transitions": [
            (issues[t["issue_id"]]["key"], raw_instant(t["timestamp"], rule), t["from_status"], t["to_status"], user(t["author_id"]))
            for t in raw("status_transitions")
        ],
        "sprint_memberships": [
            (issues[m["issue_id"]]["key"], *sprint_ref(m["sprint_id"]), raw_instant(m["added_at"], rule))
            for m in raw("sprint_issue_memberships")
        ],
    }


# --- staging side ------------------------------------------------------------------

STAGING_QUERIES = {
    "teams": "select project_key, project_name from {s}.teams",
    "users": "select display_name from {s}.users",
    "boards": "select project_key from {s}.boards",
    "sprints": """select b.project_key, s.sprint_name, s.state, s.start_at, s.end_at
                  from {s}.sprints s left join {s}.boards b using (board_id)""",
    "capacity": """select c.team_key, b.project_key, s.sprint_name, c.headcount, c.pto_days
                   from {s}.capacity c left join {s}.sprints s using (sprint_id) left join {s}.boards b on b.board_id = s.board_id""",
    "issues": """select i.issue_key, t.project_key, i.issue_type, i.status_name, i.story_points, i.duedate,
                        i.created_at, i.resolved_at, p.issue_key, a.display_name, r.display_name
                 from {s}.issues i
                 left join {s}.teams t on t.project_id = i.project_id
                 left join {s}.issues p on p.issue_id = i.parent_issue_id
                 left join {s}.users a on a.account_id = i.assignee_account_id
                 left join {s}.users r on r.account_id = i.reporter_account_id""",
    "status_transitions": """select i.issue_key, x.transitioned_at, x.from_status_name, x.to_status_name, u.display_name
                             from {s}.status_transitions x
                             left join {s}.issues i using (issue_id)
                             left join {s}.users u on u.account_id = x.author_account_id""",
    "sprint_memberships": """select i.issue_key, b.project_key, s.sprint_name, m.added_at
                             from {s}.sprint_memberships m
                             left join {s}.issues i using (issue_id)
                             left join {s}.sprints s using (sprint_id)
                             left join {s}.boards b on b.board_id = s.board_id""",
}
"""Each staging table in the shape of `expected_rows`. Left joins, so a dangling ID shows up as a mismatch."""

COMPARED = {
    "teams": "project key, name (base_headcount has no API counterpart)",
    "users": "display name (account IDs are hashes of the raw IDs; their team has no API counterpart)",
    "boards": "one per team, by project key (raw_data/ has no boards)",
    "sprints": "team, name, state, start and end instants (complete_at has no raw counterpart)",
    "capacity": "team, sprint, headcount, pto_days exactly",
    "issues": "key, team, type, status, story points, due date, created, resolution date, parent, assignee, reporter "
              "(summary is synthetic in the mock and absent from raw_data/)",
    "status_transitions": "issue, timestamp, from and to status, author",
    "sprint_memberships": "issue, sprint, added_at",
}


def staging_rows(connection: psycopg.Connection, schema: str) -> dict[str, list[tuple]]:
    rows = {}
    for table, query in STAGING_QUERIES.items():
        rows[table] = [tuple(row) for row in connection.execute(query.format(s=schema)).fetchall()]
    return rows


def differences(expected: list[tuple], actual: list[tuple], limit: int = 5) -> list[str]:
    """Rows missing from staging, or extra in it, as a multiset comparison. Empty when equal."""
    missing = Counter(expected) - Counter(actual)
    extra = Counter(actual) - Counter(expected)
    return [f"missing {row}" for row in list(missing.elements())[:limit]] + [f"extra {row}" for row in list(extra.elements())[:limit]]


def reconcile(connection: psycopg.Connection, schema: str, rule=truncate_ms) -> dict[str, list[str]]:
    """Differences per table between staging and raw_data/; every list is empty when they agree."""
    expected, actual = expected_rows(rule), staging_rows(connection, schema)
    return {table: differences(expected[table], actual[table]) for table in STAGING_QUERIES}


def count_differences(connection: psycopg.Connection, schema: str) -> dict[str, tuple[int, int]]:
    """(raw_data count, staging count) for every table whose counts differ."""
    expected = {table: len(rows) for table, rows in expected_rows().items()}
    found = {}
    for table in loader.TABLES:
        found[table.name] = connection.execute(f"select count(*) from {schema}.{table.name}").fetchone()[0]
    return {table: (expected[table], found[table]) for table in expected if expected[table] != found[table]}


def sprint_list_memberships(landing_dir: Path) -> set[tuple[int, int]]:
    """(sprint ID, issue ID) from the landed sprint issue lists, read straight from the manifest and pages."""
    pairs = set()
    for entry in json.loads((landing_dir / "manifest.json").read_text())["files"]:
        match = re.fullmatch(r"/rest/agile/1\.0/sprint/(\d+)/issue", entry["path"])
        if match:
            for issue in json.loads((landing_dir / entry["file"]).read_bytes())["issues"]:
                pairs.add((int(match.group(1)), int(issue["id"])))
    return pairs


# --- Tests ---------------------------------------------------------------------------


def test_raw_natural_keys_are_unique() -> None:
    """The join keys must identify one row each in raw_data/, or a reconciliation could pass by accident."""
    assert len({u["display_name"] for u in raw("users")}) == len(raw("users")) == 12
    assert len({(s["team_id"], s["name"]) for s in raw("sprints")}) == len(raw("sprints")) == 36
    assert len({i["key"] for i in raw("issues")}) == len(raw("issues")) == 530
    assert len({t["key"] for t in raw("teams")}) == len(raw("teams")) == 3


@pytest.mark.postgres
def test_every_staging_table_equals_raw_data_row_by_row(conn, landing: Path) -> None:
    loader.load_staging(conn, landing, schema=TEST_SCHEMA)
    assert count_differences(conn, TEST_SCHEMA) == {}
    assert reconcile(conn, TEST_SCHEMA) == {table: [] for table in STAGING_QUERIES}


@pytest.mark.postgres
def test_changelog_memberships_equal_the_sprint_issue_lists(conn, landing: Path) -> None:
    """Both directions: every changelog-derived membership is on its sprint's issue list, and vice versa."""
    loader.load_staging(conn, landing, schema=TEST_SCHEMA)
    from_changelog = {tuple(row) for row in conn.execute(f"select sprint_id, issue_id from {TEST_SCHEMA}.sprint_memberships")}
    from_lists = sprint_list_memberships(landing)
    assert len(from_lists) == 320
    assert from_changelog - from_lists == set()
    assert from_lists - from_changelog == set()


@pytest.mark.postgres
def test_timestamp_rule_is_truncation_to_milliseconds(conn, landing: Path) -> None:
    """The rule is exact: staging holds whole milliseconds, truncation matches all, rounding would not."""
    loader.load_staging(conn, landing, schema=TEST_SCHEMA)
    raw_values = (
        [i["created"] for i in raw("issues")]
        + [i["resolutiondate"] for i in raw("issues") if i["resolutiondate"]]
        + [t["timestamp"] for t in raw("status_transitions")]
        + [m["added_at"] for m in raw("sprint_issue_memberships")]
    )
    parsed = [datetime.fromisoformat(value) for value in raw_values]
    sub_millisecond = sum(value.microsecond % 1000 != 0 for value in parsed)
    rounding_differs = sum(value.microsecond % 1000 >= 500 for value in parsed)
    assert (len(raw_values), sub_millisecond, rounding_differs) == (2651, 2403, 1236)

    stored = [row[0] for row in conn.execute(
        f"""select created_at from {TEST_SCHEMA}.issues union all select resolved_at from {TEST_SCHEMA}.issues where resolved_at is not null
            union all select transitioned_at from {TEST_SCHEMA}.status_transitions union all select added_at from {TEST_SCHEMA}.sprint_memberships"""
    )]
    assert all(value.microsecond % 1000 == 0 for value in stored)
    as_utc = [value.replace(tzinfo=UTC) for value in parsed]
    assert Counter(truncate_ms(v) for v in as_utc) == Counter(stored)
    mismatched = sum((Counter(round_ms(v) for v in as_utc) - Counter(stored)).values())
    assert mismatched == rounding_differs
    assert any(table for table, found in reconcile(conn, TEST_SCHEMA, rule=round_ms).items() if found)


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("suffix", "broken_tables"),
    [("+00:00", {"sprints"}), ("+0000", {"issues", "status_transitions", "sprint_memberships"})],
    ids=["agile +00:00", "platform +0000"],
)
def test_sabotage_1_broken_timestamp_parsing_fails_the_reconciliation(
    conn, landing: Path, monkeypatch: pytest.MonkeyPatch, suffix: str, broken_tables: set[str]
) -> None:
    """Misread one offset style as +01:00. The tables holding that style must fail; then revert and pass."""
    real_parse = parse.parse_timestamp

    def misread(value):
        result = real_parse(value)
        return result - timedelta(hours=1) if value is not None and value.endswith(suffix) else result

    monkeypatch.setattr(parse, "parse_timestamp", misread)
    loader.load_staging(conn, landing, schema=TEST_SCHEMA)
    failing = {table for table, found in reconcile(conn, TEST_SCHEMA).items() if found}
    assert failing == broken_tables

    monkeypatch.undo()  # revert the sabotage
    loader.load_staging(conn, landing, schema=TEST_SCHEMA)
    assert reconcile(conn, TEST_SCHEMA) == {table: [] for table in STAGING_QUERIES}


@pytest.mark.postgres
def test_sabotage_2_dropping_one_landed_page_fails_the_counts(conn, landing: Path, landing_copy: Path) -> None:
    """Remove one changelog page and its manifest entry from a copy: the load succeeds, the counts don't."""
    dropped = "changelogs/PHX-29/page-0000.json"
    histories = json.loads((landing_copy / dropped).read_bytes())["values"]
    status_items = sum(item["field"] == "status" for h in histories for item in h["items"])
    sprint_items = sum(item.get("fieldId") == "customfield_10020" for h in histories for item in h["items"])
    manifest = json.loads((landing_copy / "manifest.json").read_text())
    manifest["files"] = [entry for entry in manifest["files"] if entry["file"] != dropped]
    (landing_copy / "manifest.json").write_text(json.dumps(manifest))
    (landing_copy / dropped).unlink()

    loader.load_staging(conn, landing_copy, schema=TEST_SCHEMA)
    assert count_differences(conn, TEST_SCHEMA) == {
        "status_transitions": (1488, 1488 - status_items),
        "sprint_memberships": (320, 320 - sprint_items),
    }
    assert status_items == 4 and sprint_items == 1

    loader.load_staging(conn, landing, schema=TEST_SCHEMA)  # revert: the full landing passes again
    assert count_differences(conn, TEST_SCHEMA) == {}


def test_reconciliation_is_independent_of_the_api_and_the_parser() -> None:
    """The expected side must not reach mock_api/ or ingestion.parse."""
    source = Path(__file__).read_text()
    imports = [line for line in source.splitlines() if line.startswith(("import ", "from "))]
    assert not any("mock_api" in line for line in imports)
    expected_side = source.split("# --- raw_data/ side")[1].split("# --- staging side")[0]
    assert "parse." not in expected_side and "loader." not in expected_side
