# Jira mock API

A FastAPI server that answers a small set of Jira Cloud REST endpoints from the simulator's ground truth. Nothing is stored in Jira's shape: each response is built on request by the serializers in `serializers.py`, which project the entities loaded from `raw_data/` (see `docs/domain-model.md`, "Core principle: ground truth vs. views").

Full reference, covering endpoints, pagination, JQL, errors, what is verified and what is assumed: [`docs/mock-api.md`](../docs/mock-api.md).

## 1. Generate the data

The server reads the seven JSON files in `raw_data/` at the repository root. They're generated, not committed:

```bash
uv sync
uv run python scripts/generate_raw_data.py
```

The run is seeded (`SEED` in `simulator/clock.py`), so this always produces the same dataset. If `raw_data/` is missing, the server refuses to start and tells you to run this.

To serve a different directory, set `AAP_RAW_DATA_DIR`.

## 2. Run the server

From the repository root:

```bash
uv run uvicorn mock_api.app:app --port 8000
```

Every endpoint requires an `Authorization` header with a `Basic` or `Bearer` scheme. Any non-empty credential is accepted. This is a mock, so nothing is checked against real accounts.

```bash
curl -s -H "Authorization: Basic $(printf 'me:token' | base64)" \
  "http://localhost:8000/rest/api/3/search/jql?jql=project%20%3D%20PHX&maxResults=5"
```

Interactive docs are at `http://localhost:8000/docs`. They're the only paths that need no auth.

## Endpoints

| Endpoint | Notes |
|---|---|
| `GET /rest/api/3/issue/{issueIdOrKey}` | Key or numeric ID. `fields` (names, `*all`, `*navigable`, `-name`) and `expand=changelog` (newest first). |
| `GET /rest/api/3/issue/{issueIdOrKey}/changelog` | `startAt`, `maxResults` (default 100). Oldest first. Status and Sprint histories. |
| `GET /rest/api/3/search/jql` | `jql`, `nextPageToken`, `maxResults` (default 50, max 5000), `fields`. Returns issue IDs only unless `fields` is given. No `startAt` or `total`. |
| `GET /rest/agile/1.0/board` | `startAt`, `maxResults` (default 50), `type`, `name`, `projectKeyOrId`. One Scrum board per team. |
| `GET /rest/agile/1.0/board/{boardId}/sprint` | `startAt`, `maxResults`, `state` (comma-separated `future`, `active`, `closed`). |
| `GET /rest/agile/1.0/sprint/{sprintId}` | |
| `GET /rest/agile/1.0/sprint/{sprintId}/issue` | `startAt`, `maxResults`, `jql` (same subset, filters within the sprint), `fields`. |

The old `GET /rest/api/3/search` isn't implemented: Atlassian has removed it in favor of `/search/jql`.

### Supported JQL

```
project = KEY [AND issuetype = X] [AND status = X] [ORDER BY created [ASC|DESC]]
```

Field names, keywords and values are case-insensitive. Quote values with spaces: `status = "In Progress"`. Anything else (OR, IN, !=, functions, other fields or sort keys) returns 400 with a message saying what wasn't understood. A search with no restriction is refused as unbounded.

### Errors

Errors use Jira's shape, `{"errorMessages": [...], "errors": {...}}`:

- **401:** missing or malformed `Authorization` header.
- **404:** unknown issue, sprint, board or path.
- **400:** bad parameters (keyed by parameter name in `errors`), unsupported JQL, or an invalid or mismatched `nextPageToken`.

## Capacity endpoint (our own design, not a Jira contract)

`GET /capacity` serves each team's headcount and PTO per sprint. Jira has no capacity API, so this endpoint and its row format are ours (`capacity.py`). It shares only the auth rule, the error body and the agile-style paging with the endpoints above.

| Parameter | Default | Notes |
|---|---|---|
| `team` | none | Project key, case-insensitive. Unknown key: 404. |
| `sprint` | none | Agile sprint ID, the same number as `/rest/agile/1.0/sprint/{sprintId}`. Unknown: 404. Not an integer: 400. |
| `startAt`, `maxResults` | `0`, `50` | Same rules as the agile endpoints. |

`GET /capacity?maxResults=1`:

```json
{"maxResults": 1, "startAt": 0, "total": 36, "isLast": false,
 "values": [{"team_key": "PHX", "sprint_id": 33292371, "headcount": 5, "pto_days": 0.00901022825809017}]}
```

- **Order:** rows are ordered by team, then by sprint start date.
- **`pto_days`:** passed through at full precision.
- **Field names:** snake_case on purpose, to keep this contract visibly separate from Jira's camelCase.
- **Mismatched filters:** a `team` plus a `sprint` belonging to another team returns an empty page, not an error.

## Checking it

```bash
uv run python -m mock_api.verify      # serializers vs. ground truth
uv run python -m mock_api.verify_app  # HTTP endpoints vs. serializers
```
