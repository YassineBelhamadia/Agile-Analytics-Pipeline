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
from datetime import UTC, datetime, timedelta, timezone
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


class ParseError(ValueError):
    """The landing cannot be parsed as it is. Stops the load before any write."""


@dataclass(frozen=True)
class LandedPage:
    """One landed response page.

    Attributes:
        file: Path relative to the landing directory, as in the manifest.
        kind: Page kind from ENDPOINTS, e.g. `boards` or `changelog`.
        data: The page body, decoded.
    """

    file: str
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
        pages.append(LandedPage(entry["file"], kind, data))
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
