"""Parse landed API pages into staging rows. No database access.

Inputs: a landing directory written by `ingestion.extract`, read through its
manifest.json, never by listing files. The request path of each manifest
entry says what the page holds.

Outputs: plain row dicts per staging table, keyed by column name, each with
`_source_file` (the manifest `file` it came from) for lineage.

Staging is a clean mirror: values are typed and renamed, never interpreted.
Anything the parser does not recognise stops the load with a ParseError
instead of being guessed: an unknown request path, a timestamp in neither
Jira format, or two pages disagreeing about the same entity.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

from ingestion.extract import MANIFEST

ENDPOINTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("boards", re.compile(r"/rest/agile/1\.0/board")),
    ("board_sprints", re.compile(r"/rest/agile/1\.0/board/\d+/sprint")),
    ("search", re.compile(r"/rest/api/3/search/jql")),
    ("changelog", re.compile(r"/rest/api/3/issue/[^/]+/changelog")),
    ("sprint_issues", re.compile(r"/rest/agile/1\.0/sprint/\d+/issue")),
    ("capacity", re.compile(r"/capacity")),
)
"""Page kind for each request path the extractor makes, matched in full."""

_TIMESTAMP = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3})([+-])(\d{2}):?(\d{2})")
"""Both Jira styles: `...000+0000` (platform API) and `...000+00:00` (agile API)."""

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_CHANGELOG_KEY = re.compile(r"/rest/api/3/issue/([^/]+)/changelog")

STATUS_NAMES: dict[str, str] = {
    "10000": "To Do",
    "10001": "In Progress",
    "10002": "Code Review",
    "10003": "QA",
    "10004": "Done",
}
"""The fixed status ID map, the same for every team (CLAUDE.md, data contract facts)."""

STATUS_FIELD = "status"
SPRINT_FIELD_ID = "customfield_10020"
STORY_POINTS_FIELD = "customfield_10016"


class ParseError(ValueError):
    """The landing cannot be parsed as it is. Stops the load before any write."""


@dataclass(frozen=True)
class LandedPage:
    """One landed response page.

    Attributes:
        file: Path relative to the landing directory, as in the manifest.
        path: Request path that produced it, as in the manifest.
        kind: Page kind from ENDPOINTS, e.g. `boards` or `changelog`.
        data: The page body, decoded.
    """

    file: str
    path: str
    kind: str
    data: dict


def page_kind(path: str) -> str:
    """Return the page kind for a request path.

    Raises:
        ParseError: If the path is not one the extractor requests.
    """
    for kind, pattern in ENDPOINTS:
        if pattern.fullmatch(path):
            return kind
    raise ParseError(f"Unknown request path in the manifest: {path}")


def read_landing(landing_dir: Path | str) -> list[LandedPage]:
    """Read every page listed in the manifest, in manifest order.

    Raises:
        ParseError: If the manifest or a listed file is missing, or a file
            is not valid JSON.
    """
    landing_dir = Path(landing_dir)
    manifest_path = landing_dir / MANIFEST
    if not manifest_path.is_file():
        raise ParseError(f"No {MANIFEST} in {landing_dir}. Run `uv run python -m ingestion.extract` first.")
    entries = json.loads(manifest_path.read_text(encoding="utf-8"))["files"]
    pages = []
    for entry in entries:
        kind = page_kind(entry["path"])
        target = landing_dir / entry["file"]
        try:
            data = json.loads(target.read_bytes())
        except FileNotFoundError:
            raise ParseError(f"{entry['file']} is listed in the manifest but missing from {landing_dir}") from None
        except ValueError as exc:
            raise ParseError(f"{entry['file']} is not valid JSON: {exc}") from None
        pages.append(LandedPage(entry["file"], entry["path"], kind, data))
    return pages


def parse_timestamp(value: str | None) -> datetime | None:
    """Parse a Jira timestamp in either API's style to an aware UTC datetime.

    Args:
        value: e.g. `2024-01-25T11:10:00.767+0000` or
            `2024-01-22T09:00:00.000+00:00`, or None.

    Raises:
        ParseError: For any other format, including a timestamp without an
            offset, since its time zone would have to be guessed.
    """
    if value is None:
        return None
    match = _TIMESTAMP.fullmatch(value)
    if not match:
        raise ParseError(f"Timestamp in neither Jira format: {value!r}")
    local, sign, hours, minutes = match.groups()
    offset = timedelta(hours=int(hours), minutes=int(minutes)) * (-1 if sign == "-" else 1)
    return datetime.strptime(local, "%Y-%m-%dT%H:%M:%S.%f").replace(tzinfo=timezone(offset)).astimezone(UTC)


def parse_date(value: str | None) -> date | None:
    """Parse a Jira date (`duedate`, e.g. `2024-02-16`), or None.

    Raises:
        ParseError: For anything but YYYY-MM-DD.
    """
    if value is None:
        return None
    if not _DATE.fullmatch(value):
        raise ParseError(f"Date not in YYYY-MM-DD form: {value!r}")
    return date.fromisoformat(value)


def status_name(status_id: str, shown_as: str, where: str) -> str:
    """Map a status ID to its name with the fixed map, checking the payload agrees.

    Args:
        status_id: e.g. `10001`.
        shown_as: The name the payload gives (`status.name` or `toString`).
        where: Location for the error message.

    Raises:
        ParseError: If the ID is not in STATUS_NAMES, or the payload names
            it differently. The name is never taken from the payload.
    """
    name = STATUS_NAMES.get(status_id)
    if name is None:
        raise ParseError(f"Unknown status ID {status_id!r} ({shown_as!r}) in {where}; the fixed map has {sorted(STATUS_NAMES)}")
    if shown_as != name:
        raise ParseError(f"Status ID {status_id} is {name!r} in the fixed map but {shown_as!r} in {where}")
    return name


def _sprint_ids(value: str) -> set[int]:
    """Sprint IDs in a Sprint changelog item's `from` / `to` (comma separated, `""` when none)."""
    return {int(part) for part in value.split(",") if part.strip()}


def _add(table: dict, key, row: dict, entity: str) -> None:
    """Keep the first row seen for `key`; a later row must agree with it."""
    first = table.get(key)
    if first is None:
        table[key] = row
        return
    differences = {
        column: (first[column], row[column]) for column in row if column != "_source_file" and first[column] != row[column]
    }
    if differences:
        raise ParseError(
            f"{entity} {key} differs between {first['_source_file']} and {row['_source_file']}: {differences}"
        )


def _user(table: dict, user: dict | None, source: str) -> None:
    if user is not None:
        _add(table, user["accountId"], {"account_id": user["accountId"], "display_name": user["displayName"], "_source_file": source}, "User")


def parse_reference(pages: list[LandedPage]) -> dict[str, list[dict]]:
    """Rows for the reference tables: teams, users, boards, sprints, capacity.

    - Teams come from projects: board `location` and each issue's `project`.
    - Users come from issue `assignee` and `reporter`, and changelog `author`.
    - Boards and sprints come from the agile list pages.
    - Capacity comes from the /capacity pages (our own schema, not Jira's).

    An entity seen on several pages is stored once, with the first page as
    its `_source_file`.

    Raises:
        ParseError: If two pages disagree about the same entity, or a sprint
            date is in neither timestamp format.
    """
    teams: dict = {}
    users: dict = {}
    boards: dict = {}
    sprints: dict = {}
    capacity: dict = {}

    for page in pages:
        source = page.file
        if page.kind == "boards":
            for board in page.data["values"]:
                location = board["location"]
                project_id = int(location["projectId"])
                _add(teams, project_id, {
                    "project_id": project_id,
                    "project_key": location["projectKey"],
                    "project_name": location["projectName"],
                    "_source_file": source,
                }, "Team")
                _add(boards, board["id"], {
                    "board_id": board["id"],
                    "board_name": board["name"],
                    "board_type": board["type"],
                    "project_id": project_id,
                    "project_key": location["projectKey"],
                    "_source_file": source,
                }, "Board")
        elif page.kind == "board_sprints":
            for sprint in page.data["values"]:
                _add(sprints, sprint["id"], {
                    "sprint_id": sprint["id"],
                    "board_id": sprint["originBoardId"],
                    "sprint_name": sprint["name"],
                    "state": sprint["state"],
                    "start_at": parse_timestamp(sprint.get("startDate")),
                    "end_at": parse_timestamp(sprint.get("endDate")),
                    "complete_at": parse_timestamp(sprint.get("completeDate")),
                    "_source_file": source,
                }, "Sprint")
        elif page.kind == "search":
            for issue in page.data["issues"]:
                fields = issue["fields"]
                project = fields["project"]
                project_id = int(project["id"])
                _add(teams, project_id, {
                    "project_id": project_id,
                    "project_key": project["key"],
                    "project_name": project["name"],
                    "_source_file": source,
                }, "Team")
                _user(users, fields.get("assignee"), source)
                _user(users, fields.get("reporter"), source)
        elif page.kind == "changelog":
            for history in page.data["values"]:
                _user(users, history.get("author"), source)
        elif page.kind == "capacity":
            for row in page.data["values"]:
                key = (row["team_key"], row["sprint_id"])
                _add(capacity, key, {
                    "team_key": row["team_key"],
                    "sprint_id": row["sprint_id"],
                    "headcount": row["headcount"],
                    "pto_days": row["pto_days"],
                    "_source_file": source,
                }, "Capacity row")

    return {
        "teams": list(teams.values()),
        "users": list(users.values()),
        "boards": list(boards.values()),
        "sprints": list(sprints.values()),
        "capacity": list(capacity.values()),
    }


def parse_issues(pages: list[LandedPage]) -> dict[str, list[dict]]:
    """Rows for issues, status transitions and sprint memberships.

    - Issues come from the search pages. `customfield_10016` becomes
      `story_points` (null for Epics, kept as null), and the status ID is
      named through the fixed map.
    - Status transitions come from changelog items with `field = "status"`,
      one row per history.
    - Sprint memberships come from changelog items with
      `fieldId = "customfield_10020"`. Each sprint ID in `to` but not in
      `from` is one membership, with `added_at` = the history's `created`.
    - A changelog page's issue is the key in its request path, matched to
      an issue from the search pages.
    - Changelog items for any other field are ignored.

    Raises:
        ParseError: On an unknown status ID or a name that disagrees with the
            fixed map, a changelog for an issue the search did not return, a
            history with more than one status item, an issue removed from a
            sprint (not modeled in v1), a malformed date or timestamp, or two
            pages disagreeing about the same issue or membership.
    """
    issues: dict = {}
    transitions: dict = {}
    memberships: dict = {}

    for page in pages:
        if page.kind != "search":
            continue
        for issue in page.data["issues"]:
            fields = issue["fields"]
            where = f"{page.file} ({issue['key']})"
            status = fields["status"]
            parent = fields.get("parent")
            assignee = fields.get("assignee")
            reporter = fields.get("reporter")
            _add(issues, int(issue["id"]), {
                "issue_id": int(issue["id"]),
                "issue_key": issue["key"],
                "issue_type": fields["issuetype"]["name"],
                "project_id": int(fields["project"]["id"]),
                "status_id": int(status["id"]),
                "status_name": status_name(status["id"], status["name"], where),
                "assignee_account_id": assignee["accountId"] if assignee else None,
                "reporter_account_id": reporter["accountId"] if reporter else None,
                "parent_issue_id": int(parent["id"]) if parent else None,
                "story_points": fields.get(STORY_POINTS_FIELD),
                "created_at": parse_timestamp(fields["created"]),
                "resolved_at": parse_timestamp(fields.get("resolutiondate")),
                "duedate": parse_date(fields.get("duedate")),
                "summary": fields["summary"],
                "_source_file": page.file,
            }, "Issue")
    issue_ids = {row["issue_key"]: issue_id for issue_id, row in issues.items()}

    for page in pages:
        if page.kind != "changelog":
            continue
        key = _CHANGELOG_KEY.fullmatch(page.path).group(1)
        if key not in issue_ids:
            raise ParseError(f"{page.file} is the changelog of {key}, which no search page returned")
        issue_id = issue_ids[key]
        for history in page.data["values"]:
            where = f"{page.file} (history {history['id']})"
            created = parse_timestamp(history["created"])
            author = history.get("author")
            status_items = [item for item in history["items"] if item["field"] == STATUS_FIELD]
            if len(status_items) > 1:
                raise ParseError(f"{where} has {len(status_items)} status items; expected at most one")
            for item in status_items:
                _add(transitions, int(history["id"]), {
                    "changelog_id": int(history["id"]),
                    "issue_id": issue_id,
                    "author_account_id": author["accountId"] if author else None,
                    "transitioned_at": created,
                    "from_status_id": int(item["from"]),
                    "from_status_name": status_name(item["from"], item["fromString"], where),
                    "to_status_id": int(item["to"]),
                    "to_status_name": status_name(item["to"], item["toString"], where),
                    "_source_file": page.file,
                }, "Status transition")
            for item in history["items"]:
                if item.get("fieldId") != SPRINT_FIELD_ID:
                    continue
                before, after = _sprint_ids(item["from"] or ""), _sprint_ids(item["to"] or "")
                if before - after:
                    raise ParseError(f"{where} removes {key} from sprint(s) {sorted(before - after)}; v1 staging models additions only")
                for sprint_id in sorted(after - before):
                    _add(memberships, (issue_id, sprint_id), {
                        "issue_id": issue_id,
                        "sprint_id": sprint_id,
                        "added_at": created,
                        "changelog_id": int(history["id"]),
                        "_source_file": page.file,
                    }, "Sprint membership")

    return {
        "issues": list(issues.values()),
        "status_transitions": list(transitions.values()),
        "sprint_memberships": list(memberships.values()),
    }


def parse_all(pages: list[LandedPage]) -> dict[str, list[dict]]:
    """Rows for every staging table: `parse_reference` plus `parse_issues`."""
    return parse_reference(pages) | parse_issues(pages)
