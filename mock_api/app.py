"""FastAPI app serving the Jira-shaped mock API.

This module is transport only. Every issue, changelog, sprint and board
body comes from mock_api/serializers.py; what lives here is routing,
lookups by Jira ID, pagination envelopes, the JQL subset (mock_api/jql.py)
and Jira-style errors. Per docs/domain-model.md the app keeps no
Jira-shaped copy of the data: payloads are built from the ground truth on
each request.

The ground truth is loaded once, at startup, from raw_data/ (or the
directory named by the AAP_RAW_DATA_DIR environment variable). If it is
missing the app refuses to start rather than serving empty results.

Run:
    uvicorn mock_api.app:app --port 8000
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
from collections.abc import Callable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from mock_api import ids, jql
from mock_api.config import ISSUE_TYPES, STATUS_IDS
from mock_api.index import GroundTruthIndex
from mock_api.serializers import (
    DEFAULT_CHANGELOG_PAGE_SIZE,
    serialize_board,
    serialize_changelog,
    serialize_issue,
    serialize_sprint,
)
from simulator.issues import Issue
from simulator.persistence import ENTITY_SPECS
from simulator.sprints import Sprint
from simulator.teams import Team

RAW_DATA_ENV = "AAP_RAW_DATA_DIR"
DEFAULT_RAW_DATA_DIR = Path(__file__).resolve().parent.parent / "raw_data"

SEARCH_DEFAULT_PAGE_SIZE = 50
"""Jira's documented default maxResults for GET /rest/api/3/search/jql."""

SEARCH_MAX_PAGE_SIZE = 5000
"""Jira documents the enhanced search as returning at most 5000 issues a page."""

AGILE_DEFAULT_PAGE_SIZE = 50
"""Documented for GET /rest/agile/1.0/board; assumed for the other agile lists."""

EMBEDDED_CHANGELOG_LIMIT = 100
"""Most recent histories included by GET issue with expand=changelog."""

SPRINT_STATE_ORDER = {"closed": 0, "active": 1, "future": 2}
"""Jira orders a board's sprints closed, then active, then future."""

UNAUTHENTICATED = "You are not authenticated. Authentication required to perform this operation."
ISSUE_NOT_FOUND = "Issue does not exist or you do not have permission to see it."
SPRINT_NOT_FOUND = "Sprint does not exist or you do not have permission to see it."
BOARD_NOT_FOUND = "Board does not exist or you do not have permission to see it."

OPEN_PATHS = frozenset({"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"})
"""Paths served without an Authorization header: FastAPI's own docs."""


# --- Errors -------------------------------------------------------------


class JiraError(Exception):
    """An error to return in Jira's ErrorCollection shape."""

    def __init__(self, status: int, messages: Sequence[str] = (), errors: dict[str, str] | None = None):
        super().__init__(status, messages)
        self.status = status
        self.messages = list(messages)
        self.errors = errors or {}


def jira_error_response(
    status: int,
    messages: Sequence[str] = (),
    errors: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Build a response body in Jira's `{errorMessages, errors}` shape."""
    return JSONResponse(
        status_code=status,
        content={"errorMessages": list(messages), "errors": errors or {}},
        headers=headers,
    )


# --- Loading and lookups ------------------------------------------------


class Catalog:
    """The loaded ground truth plus lookups keyed by the IDs Jira clients use.

    Holds references into the index, never Jira-shaped copies of it.
    """

    def __init__(self, index: GroundTruthIndex) -> None:
        self.index = index
        collector = index.collector
        self.issues: list[Issue] = list(collector.issues)
        self.issues_by_jira_id = {ids.issue_id(i.issue_id): i for i in self.issues}
        self.sprints_by_jira_id = {ids.sprint_id(s.sprint_id): s for s in collector.sprints}
        self.teams: list[Team] = list(collector.teams)
        self.teams_by_board_id = {ids.board_id(t.team_id): t for t in self.teams}
        self.project_key = {t.team_id: t.key for t in self.teams}

        sprints_by_team: dict[str, list[Sprint]] = {t.team_id: [] for t in self.teams}
        for sprint in collector.sprints:
            sprints_by_team[sprint.team_id].append(sprint)
        self.sprints_by_team = {
            team_id: sorted(rows, key=lambda s: (SPRINT_STATE_ORDER.get(s.state, 3), s.start_date))
            for team_id, rows in sprints_by_team.items()
        }

        members: dict[str, list[tuple[Any, Issue]]] = {s.sprint_id: [] for s in collector.sprints}
        for membership in collector.sprint_issue_memberships:
            members[membership.sprint_id].append((membership.added_at, index.issues[membership.issue_id]))
        # No rank in the ground truth: order by when the issue joined.
        self.issues_by_sprint = {
            sprint_id: [issue for _, issue in sorted(rows, key=lambda r: (r[0], r[1].key))]
            for sprint_id, rows in members.items()
        }

        self.jql_values = {
            "project": [t.key for t in self.teams],
            "issuetype": list(ISSUE_TYPES),
            "status": list(STATUS_IDS),
        }

    def issue(self, id_or_key: str) -> Issue:
        if id_or_key.isdigit():
            found = self.issues_by_jira_id.get(id_or_key)
        else:
            found = self.index.issues_by_key.get(id_or_key.upper())
        if found is None:
            raise JiraError(404, [ISSUE_NOT_FOUND])
        return found

    def sprint(self, sprint_id: int) -> Sprint:
        found = self.sprints_by_jira_id.get(sprint_id)
        if found is None:
            raise JiraError(404, [SPRINT_NOT_FOUND])
        return found

    def board(self, board_id: int) -> Team:
        found = self.teams_by_board_id.get(board_id)
        if found is None:
            raise JiraError(404, [BOARD_NOT_FOUND])
        return found


def load_catalog(raw_data_dir: Path) -> Catalog:
    """Load and index the persisted ground truth, or explain why not.

    Raises:
        RuntimeError: If the directory or any entity file is missing.
    """
    missing = [spec.filename for spec in ENTITY_SPECS if not (raw_data_dir / spec.filename).is_file()]
    if not raw_data_dir.is_dir() or missing:
        what = "does not exist" if not raw_data_dir.is_dir() else f"is missing {', '.join(missing)}"
        raise RuntimeError(
            f"Cannot start the mock API: ground truth directory {raw_data_dir} {what}. "
            f"Generate it with `python scripts/generate_raw_data.py`, or point {RAW_DATA_ENV} "
            f"at a directory written by simulator.persistence.dump_to_json."
        )
    return Catalog(GroundTruthIndex.from_json(raw_data_dir))


# --- Request helpers ----------------------------------------------------


def split_list(values: Sequence[str] | None) -> list[str] | None:
    """Flatten repeated, comma-separated query values; None if absent."""
    if values is None:
        return None
    return [part.strip() for value in values for part in value.split(",") if part.strip()]


def project_fields(fields: dict, requested: list[str]) -> dict:
    """Apply a Jira `fields` parameter to a serialized `fields` object.

    `*all` and `*navigable` select every field (all fields here are
    navigable); a bare name selects that field; `-name` excludes one. A
    list of exclusions alone starts from every field, as Jira documents.
    Unknown names are ignored, as Jira does.
    """
    named = [t for t in requested if not t.startswith(("-", "*"))]
    excluded = {t[1:] for t in requested if t.startswith("-")}
    if any(t in ("*all", "*navigable") for t in requested) or not named:
        selected = dict(fields)
    else:
        selected = {name: fields[name] for name in named if name in fields}
    return {name: value for name, value in selected.items() if name not in excluded}


def issue_payload(issue: Issue, catalog: Catalog, requested: list[str] | None) -> dict:
    payload = serialize_issue(issue, catalog.index)
    if requested is not None:
        payload["fields"] = project_fields(payload["fields"], requested)
    return payload


def _token_digest(query: jql.JqlQuery) -> str:
    return hashlib.sha256(query.canonical().encode()).hexdigest()[:16]


def encode_page_token(query: jql.JqlQuery, offset: int) -> str:
    """An opaque token for the page starting at `offset` of this query.

    Deterministic: the same query and position always yield the same
    token, across calls and server restarts. It is bound to the query, so
    replaying it against a different JQL is rejected.
    """
    raw = json.dumps({"q": _token_digest(query), "o": offset}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_page_token(token: str, query: jql.JqlQuery) -> int:
    """Return the offset a token points at, or raise a 400."""
    try:
        padded = token + "=" * (-len(token) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        digest, offset = data["q"], data["o"]
        if not isinstance(offset, int) or offset < 0 or not isinstance(digest, str):
            raise ValueError
    except (binascii.Error, UnicodeDecodeError, ValueError, KeyError, TypeError):
        raise JiraError(400, errors={"nextPageToken": "The nextPageToken is invalid."}) from None
    if digest != _token_digest(query):
        raise JiraError(
            400, errors={"nextPageToken": "The nextPageToken was issued for a different JQL query."}
        )
    return offset


def agile_page(items: Sequence[Any], start_at: int, max_results: int, render: Callable[[Any], dict]) -> dict:
    """The agile API's offset page envelope: startAt, maxResults, total, isLast, values."""
    page = items[start_at : start_at + max_results]
    return {
        "maxResults": max_results,
        "startAt": start_at,
        "total": len(items),
        "isLast": start_at + max_results >= len(items),
        "values": [render(item) for item in page],
    }


# --- App ----------------------------------------------------------------


def create_app(raw_data_dir: Path | str | None = None) -> FastAPI:
    """Build the app. The ground truth is loaded when the app starts up.

    Args:
        raw_data_dir: Ground truth directory. Defaults to $AAP_RAW_DATA_DIR,
            then to raw_data/ at the repository root.
    """
    data_dir = Path(raw_data_dir or os.environ.get(RAW_DATA_ENV) or DEFAULT_RAW_DATA_DIR)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.catalog = load_catalog(data_dir)
        yield

    app = FastAPI(
        title="Agile Analytics Pipeline — Jira mock API",
        description="Jira Cloud-shaped endpoints projected from the simulator's ground truth.",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def require_authorization(request: Request, call_next):
        # Any non-empty Basic or Bearer credential is accepted: this is a
        # mock, and checking real credentials is out of scope.
        if request.url.path not in OPEN_PATHS:
            scheme, _, credentials = request.headers.get("authorization", "").partition(" ")
            if scheme.lower() not in ("basic", "bearer") or not credentials.strip():
                return jira_error_response(
                    401, [UNAUTHENTICATED], headers={"WWW-Authenticate": 'Basic realm="jira-mock"'}
                )
        return await call_next(request)

    @app.exception_handler(JiraError)
    async def handle_jira_error(request: Request, exc: JiraError) -> JSONResponse:
        return jira_error_response(exc.status, exc.messages, exc.errors)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI would answer 422 in its own shape; Jira answers 400 with
        # errors keyed by parameter.
        errors = {str(err["loc"][-1]): err["msg"] for err in exc.errors()}
        return jira_error_response(400, errors=errors)

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        message = (
            f"No resource matches {request.method} {request.url.path}."
            if exc.status_code == 404
            else str(exc.detail)
        )
        return jira_error_response(exc.status_code, [message])

    def get_catalog(request: Request) -> Catalog:
        return request.app.state.catalog

    # --- Platform API ---------------------------------------------------

    @app.get("/rest/api/3/issue/{issueIdOrKey}")
    def get_issue(
        issueIdOrKey: str,
        fields: list[str] | None = Query(None),
        expand: str | None = None,
        catalog: Catalog = Depends(get_catalog),
    ) -> dict:
        """Get an issue by key or numeric ID. Supports `fields` and `expand=changelog`."""
        issue = catalog.issue(issueIdOrKey)
        payload = issue_payload(issue, catalog, split_list(fields))
        if "changelog" in (split_list([expand]) if expand else []):
            # Newest first, as Jira documents for the expanded changelog —
            # the reverse of the changelog endpoint's order.
            total = serialize_changelog(issue, catalog.index)["total"]
            everything = serialize_changelog(issue, catalog.index, 0, max(total, 1))["values"]
            histories = list(reversed(everything))[:EMBEDDED_CHANGELOG_LIMIT]
            payload["changelog"] = {
                "startAt": 0,
                "maxResults": len(histories),
                "total": len(histories),
                "histories": histories,
            }
        return payload

    @app.get("/rest/api/3/issue/{issueIdOrKey}/changelog")
    def get_changelog(
        issueIdOrKey: str,
        startAt: int = Query(0, ge=0),
        maxResults: int = Query(DEFAULT_CHANGELOG_PAGE_SIZE, ge=1),
        catalog: Catalog = Depends(get_catalog),
    ) -> dict:
        """Get an issue's changelog, oldest first, paged by startAt/maxResults."""
        return serialize_changelog(catalog.issue(issueIdOrKey), catalog.index, startAt, maxResults)

    @app.get("/rest/api/3/search/jql")
    def search_jql(
        jql_query: str | None = Query(None, alias="jql"),
        nextPageToken: str | None = None,
        maxResults: int = Query(SEARCH_DEFAULT_PAGE_SIZE, ge=1),
        fields: list[str] | None = Query(None),
        catalog: Catalog = Depends(get_catalog),
    ) -> dict:
        """Enhanced JQL search, paged by nextPageToken. Returns issue IDs only unless `fields` is given."""
        try:
            query = jql.parse(jql_query, catalog.jql_values)
        except jql.JqlError as exc:
            raise JiraError(400, [str(exc)]) from None
        offset = decode_page_token(nextPageToken, query) if nextPageToken else 0
        size = min(maxResults, SEARCH_MAX_PAGE_SIZE)

        matching = jql.sort(
            query,
            [i for i in catalog.issues if jql.matches(query, i, catalog.project_key[i.team_id])],
        )
        page = matching[offset : offset + size]
        is_last = offset + size >= len(matching)

        requested = split_list(fields)
        if requested is None or requested == ["id"]:
            issues = [{"id": ids.issue_id(issue.issue_id)} for issue in page]
        else:
            issues = [issue_payload(issue, catalog, requested) for issue in page]

        body: dict = {"issues": issues, "isLast": is_last}
        if not is_last:
            body["nextPageToken"] = encode_page_token(query, offset + size)
        return body

    # --- Agile API ------------------------------------------------------

    @app.get("/rest/agile/1.0/board")
    def list_boards(
        startAt: int = Query(0, ge=0),
        maxResults: int = Query(AGILE_DEFAULT_PAGE_SIZE, ge=1),
        type: str | None = None,
        name: str | None = None,
        projectKeyOrId: str | None = None,
        catalog: Catalog = Depends(get_catalog),
    ) -> dict:
        """List boards, one Scrum board per team. Filters: type, name, projectKeyOrId."""
        teams = catalog.teams
        if type is not None:
            wanted = set(split_list([type]) or [])
            if not wanted <= {"scrum", "kanban", "simple"}:
                raise JiraError(400, errors={"type": "Valid values are scrum, kanban and simple."})
            teams = [t for t in teams if "scrum" in wanted]
        if name is not None:
            teams = [t for t in teams if name.lower() in serialize_board(t)["name"].lower()]
        if projectKeyOrId is not None:
            teams = [
                t
                for t in teams
                if projectKeyOrId.upper() == t.key or projectKeyOrId == ids.project_id(t.team_id)
            ]
        return agile_page(teams, startAt, maxResults, serialize_board)

    @app.get("/rest/agile/1.0/board/{boardId}/sprint")
    def list_board_sprints(
        boardId: int,
        startAt: int = Query(0, ge=0),
        maxResults: int = Query(AGILE_DEFAULT_PAGE_SIZE, ge=1),
        state: str | None = None,
        catalog: Catalog = Depends(get_catalog),
    ) -> dict:
        """List a board's sprints: closed, then active, then future. Filter: state."""
        team = catalog.board(boardId)
        sprints = catalog.sprints_by_team[team.team_id]
        if state is not None:
            wanted = set(split_list([state]) or [])
            if not wanted or not wanted <= set(SPRINT_STATE_ORDER):
                raise JiraError(400, errors={"state": "Valid values are future, active and closed."})
            sprints = [s for s in sprints if s.state in wanted]
        return agile_page(sprints, startAt, maxResults, serialize_sprint)

    @app.get("/rest/agile/1.0/sprint/{sprintId}")
    def get_sprint(sprintId: int, catalog: Catalog = Depends(get_catalog)) -> dict:
        """Get a sprint by ID."""
        return serialize_sprint(catalog.sprint(sprintId))

    @app.get("/rest/agile/1.0/sprint/{sprintId}/issue")
    def list_sprint_issues(
        sprintId: int,
        startAt: int = Query(0, ge=0),
        maxResults: int = Query(AGILE_DEFAULT_PAGE_SIZE, ge=1),
        jql_query: str | None = Query(None, alias="jql"),
        fields: list[str] | None = Query(None),
        catalog: Catalog = Depends(get_catalog),
    ) -> dict:
        """List the issues in a sprint. Optional jql (same subset) filters and re-orders them."""
        sprint = catalog.sprint(sprintId)
        issues = catalog.issues_by_sprint[sprint.sprint_id]
        if jql_query:
            try:
                query = jql.parse(jql_query, catalog.jql_values, require_restriction=False)
            except jql.JqlError as exc:
                raise JiraError(400, [str(exc)]) from None
            issues = [i for i in issues if jql.matches(query, i, catalog.project_key[i.team_id])]
            if query.ordered:
                issues = jql.sort(query, issues)
        requested = split_list(fields)
        page = issues[startAt : startAt + maxResults]
        return {
            "startAt": startAt,
            "maxResults": maxResults,
            "total": len(issues),
            "issues": [issue_payload(issue, catalog, requested) for issue in page],
        }

    return app


app = create_app()
