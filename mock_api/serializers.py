"""Projects the ground truth into Jira Cloud REST response shapes.

Per docs/domain-model.md, the mock API stores no Jira-shaped copy of the
data. Each function here takes ground-truth entities (via a
GroundTruthIndex) and builds, on demand, the JSON body a real Jira
endpoint would return:

- `serialize_issue`      -> GET /rest/api/3/issue/{issueIdOrKey}
- `serialize_changelog`  -> GET /rest/api/3/issue/{issueIdOrKey}/changelog
- `serialize_sprint`     -> GET /rest/agile/1.0/sprint/{sprintId}

Shapes follow Atlassian's published OpenAPI specs for the platform v3 and
Jira Software APIs. Fields the ground truth cannot supply (avatars,
email addresses, sprint goals) are left out rather than filled with
placeholders. Nothing here writes to a ground-truth object: every
payload is a freshly built dict.

Timestamps. The ground truth holds naive datetimes. They are read as UTC
— a choice, since the simulator models no timezone — and written with
millisecond precision and an explicit numeric offset, as Jira does.
Jira's documentation writes that offset two ways, and each API's own
style is followed: the platform API as `+0000`, the agile sprint object
as `+00:00`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

from mock_api import ids
from mock_api.config import (
    BASE_URL,
    ISSUE_TYPES,
    SPRINT_BOUNDARY_HOUR,
    SPRINT_FIELD,
    STATUS_IDS,
    STORY_POINTS_FIELD,
)
from mock_api.index import GroundTruthIndex
from simulator.issues import Issue, StatusTransition
from simulator.sprints import Sprint
from simulator.teams import Team
from simulator.users import User

PLATFORM_API = f"{BASE_URL}/rest/api/3"
AGILE_API = f"{BASE_URL}/rest/agile/1.0"

DEFAULT_CHANGELOG_PAGE_SIZE = 100
"""Jira's documented default `maxResults` for the changelog endpoint."""


class UnknownStatusError(ValueError):
    """A status in the ground truth has no ID in config.STATUS_IDS."""


# --- Formatting ---------------------------------------------------------


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def jira_datetime(value: datetime) -> str:
    """Format a datetime the way the platform API does.

    Microseconds are truncated, not rounded, to milliseconds: rounding
    could carry a 23:59:59.9996 into the next day.

    Args:
        value: A ground-truth datetime, read as UTC if naive.

    Returns:
        e.g. "2024-01-15T09:23:00.000+0000".
    """
    utc = _as_utc(value)
    return f"{utc:%Y-%m-%dT%H:%M:%S}.{utc.microsecond // 1000:03d}+0000"


def agile_datetime(value: datetime) -> str:
    """Format a datetime the way the agile sprint object does.

    Args:
        value: A ground-truth datetime, read as UTC if naive.

    Returns:
        e.g. "2024-01-22T09:00:00.000+00:00".
    """
    utc = _as_utc(value)
    return f"{utc:%Y-%m-%dT%H:%M:%S}.{utc.microsecond // 1000:03d}+00:00"


def _optional(value: datetime | None) -> str | None:
    return None if value is None else jira_datetime(value)


# --- Embedded objects ---------------------------------------------------


def status_id(name: str) -> str:
    """Return the fixed Jira ID for a status name.

    Args:
        name: Status name as it appears in the ground truth.

    Returns:
        The status ID.

    Raises:
        UnknownStatusError: If the status is not configured. Inventing an
            ID on the fly would silently break the guarantee that IDs are
            fixed across the instance.
    """
    try:
        return STATUS_IDS[name]
    except KeyError:
        raise UnknownStatusError(
            f"status {name!r} has no ID in mock_api.config.STATUS_IDS "
            f"(configured: {sorted(STATUS_IDS)})"
        ) from None


def _status(name: str) -> dict:
    sid = status_id(name)
    return {"self": f"{PLATFORM_API}/status/{sid}", "id": sid, "name": name}


def _issue_type(name: str) -> dict:
    try:
        spec = ISSUE_TYPES[name]
    except KeyError:
        raise ValueError(
            f"issue type {name!r} has no entry in mock_api.config.ISSUE_TYPES"
        ) from None
    return {
        "self": f"{PLATFORM_API}/issuetype/{spec['id']}",
        "id": spec["id"],
        "name": name,
        "subtask": False,
        "hierarchyLevel": spec["hierarchyLevel"],
    }


def _project(team: Team) -> dict:
    pid = ids.project_id(team.team_id)
    return {
        "self": f"{PLATFORM_API}/project/{pid}",
        "id": pid,
        "key": team.key,
        "name": team.name,
        "projectTypeKey": "software",
        "simplified": False,
    }


def _user(user: User) -> dict:
    account = ids.account_id(user.user_id)
    return {
        "self": f"{PLATFORM_API}/user?accountId={account}",
        "accountId": account,
        "accountType": "atlassian",
        "displayName": user.display_name,
        "active": True,
    }


def _optional_user(user_id: str | None, index: GroundTruthIndex) -> dict | None:
    return None if user_id is None else _user(index.users[user_id])


def _summary(issue: Issue) -> str:
    # The ground truth carries no summary text; this is derived so the
    # field is present and stable, not meant to read like a real ticket.
    return f"{issue.issue_type} {issue.key}"


def _issue_self(issue: Issue) -> str:
    return f"{PLATFORM_API}/issue/{ids.issue_id(issue.issue_id)}"


def _parent(parent: Issue) -> dict:
    return {
        "id": ids.issue_id(parent.issue_id),
        "key": parent.key,
        "self": _issue_self(parent),
        "fields": {
            "summary": _summary(parent),
            "status": _status(parent.status),
            "issuetype": _issue_type(parent.issue_type),
        },
    }


def _story_points(issue: Issue) -> int | None:
    if issue.issue_type == "Epic":
        if issue.story_points is not None:
            # Epics are unpointed by decision; a value here is a ground
            # truth bug, and dropping it quietly would hide that.
            raise ValueError(f"Epic {issue.key} has story_points={issue.story_points}")
        return None
    return issue.story_points


def _sprint_boundary(day: date) -> datetime:
    return datetime.combine(day, time(SPRINT_BOUNDARY_HOUR))


def _sprint_dates(sprint: Sprint) -> dict:
    dates = {
        "startDate": agile_datetime(_sprint_boundary(sprint.start_date)),
        "endDate": agile_datetime(_sprint_boundary(sprint.end_date)),
    }
    # The ground truth has no separate completion instant; a closed
    # sprint is taken to have been completed at its scheduled end.
    if sprint.state == "closed":
        dates["completeDate"] = dates["endDate"]
    return dates


def _sprint_field_entry(sprint: Sprint) -> dict:
    """One element of the sprint custom field array on an issue.

    This is a different shape from the agile API's sprint object: Jira
    documents it with `boardId` instead of `originBoardId`, and no `self`.
    """
    return {
        "id": ids.sprint_id(sprint.sprint_id),
        "name": sprint.name,
        "state": sprint.state,
        "boardId": ids.board_id(sprint.team_id),
        **_sprint_dates(sprint),
    }


# --- Endpoint payloads --------------------------------------------------


def serialize_issue(issue: Issue, index: GroundTruthIndex) -> dict:
    """Build the GET /rest/api/3/issue/{issueIdOrKey} body for one issue.

    `parent` is omitted, not null, for issues with no parent; the sprint
    field is null for issues never pulled into a sprint.

    Args:
        issue: The ground-truth issue.
        index: Lookups over the ground truth the issue belongs to.

    Returns:
        The Jira-shaped issue payload.
    """
    sprints = index.sprints_for(issue)
    fields: dict = {
        "summary": _summary(issue),
        "issuetype": _issue_type(issue.issue_type),
        "project": _project(index.teams[issue.team_id]),
        "status": _status(issue.status),
        "assignee": _optional_user(issue.assignee_id, index),
        "reporter": _optional_user(issue.reporter_id, index),
        "created": jira_datetime(issue.created),
        "duedate": None if issue.duedate is None else issue.duedate.isoformat(),
        "resolutiondate": _optional(issue.resolutiondate),
        STORY_POINTS_FIELD: _story_points(issue),
        SPRINT_FIELD: [_sprint_field_entry(s) for s in sprints] or None,
    }
    if issue.parent_id is not None:
        fields["parent"] = _parent(index.issues[issue.parent_id])

    return {
        "id": ids.issue_id(issue.issue_id),
        "key": issue.key,
        "self": _issue_self(issue),
        "fields": fields,
    }


def _history(transition: StatusTransition, index: GroundTruthIndex) -> dict:
    return {
        "id": ids.changelog_id(transition.transition_id),
        "author": _user(index.users[transition.author_id]),
        "created": jira_datetime(transition.timestamp),
        "items": [
            {
                "field": "status",
                "fieldtype": "jira",
                "fieldId": "status",
                "from": status_id(transition.from_status),
                "fromString": transition.from_status,
                "to": status_id(transition.to_status),
                "toString": transition.to_status,
            }
        ],
    }


def serialize_changelog(
    issue: Issue,
    index: GroundTruthIndex,
    start_at: int = 0,
    max_results: int = DEFAULT_CHANGELOG_PAGE_SIZE,
) -> dict:
    """Build the GET /rest/api/3/issue/{issueIdOrKey}/changelog body.

    One history per status transition, oldest first, as Jira documents
    for this endpoint. The response is a page; `total` counts every
    history on the issue, not just the ones on this page.

    Args:
        issue: The ground-truth issue.
        index: Lookups over the ground truth the issue belongs to.
        start_at: Index of the first history to return.
        max_results: Maximum number of histories on the page.

    Returns:
        The Jira-shaped changelog page.

    Raises:
        ValueError: If `start_at` is negative or `max_results` is below 1.
    """
    if start_at < 0 or max_results < 1:
        raise ValueError(f"invalid page: start_at={start_at}, max_results={max_results}")

    transitions = index.transitions_for(issue)
    page = transitions[start_at : start_at + max_results]
    is_last = start_at + max_results >= len(transitions)
    base = f"{PLATFORM_API}/issue/{issue.key}/changelog"

    payload: dict = {
        "self": f"{base}?startAt={start_at}&maxResults={max_results}",
        "maxResults": max_results,
        "startAt": start_at,
        "total": len(transitions),
        "isLast": is_last,
        "values": [_history(t, index) for t in page],
    }
    if not is_last:
        payload["nextPage"] = f"{base}?startAt={start_at + max_results}&maxResults={max_results}"
    return payload


def serialize_sprint(sprint: Sprint) -> dict:
    """Build the GET /rest/agile/1.0/sprint/{sprintId} body for one sprint.

    `goal` and `createdDate` are omitted: the ground truth has neither.

    Args:
        sprint: The ground-truth sprint.

    Returns:
        The Jira-shaped sprint payload.
    """
    sid = ids.sprint_id(sprint.sprint_id)
    return {
        "id": sid,
        "self": f"{AGILE_API}/sprint/{sid}",
        "state": sprint.state,
        "name": sprint.name,
        **_sprint_dates(sprint),
        "originBoardId": ids.board_id(sprint.team_id),
    }
