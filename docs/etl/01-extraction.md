# 01. Extraction

Status: done

## Purpose

Pulls everything the mock API serves and writes each response page to `landing/` exactly as received, plus a manifest of the request behind each file. It parses nothing. JSON is decoded only to follow pagination and to discover the next requests. Staging ([02-staging.md](02-staging.md)) is the first stage that interprets the data.

The code is two modules:

- `ingestion/client.py` (`JiraClient`): one authenticated GET with retries, plus the two pagination styles.
- `ingestion/extract.py` (`run_extraction`): the discovery order, the landing layout, the manifest and the swap into place.

## Inputs and outputs

**Inputs**

- **Mock API.** It must be running, e.g. `uv run uvicorn mock_api.app:app --port 8000`.
- **`AAP_API_BASE_URL`.** The API's base URL. Required, and must be an absolute `http://` or `https://` URL with a host. Anything else fails before the first request, with an error naming the variable.
- **`AAP_API_TOKEN`.** Sent as `Authorization: Bearer <token>`. Required and non-empty. The mock accepts any non-empty value.
- **Where they come from.** The process environment only. Both are listed in `.env.example`, so a copied `.env` works once it is passed with `uv run --env-file .env`. The client never reads `.env` itself ([D-025](../decisions.md#d-025-extraction-settings-come-from-the-environment-only-supplied-by---env-file-env)). Setting them inline also works.
- **Landing directory (optional).** The first command-line argument. Default: `landing/` at the repo root.

**Outputs**

`landing/` (gitignored) is fully replaced on every successful run. The layout:

| Path | Request | Pages (shipped data) |
|---|---|---|
| `boards/page-NNNN.json` | `GET /rest/agile/1.0/board` | 1 |
| `board_sprints/<boardId>/page-NNNN.json` | `GET /rest/agile/1.0/board/{boardId}/sprint` | 3 (one per board) |
| `search/<projectKey>/page-NNNN.json` | `GET /rest/api/3/search/jql` with `jql=project = <KEY> ORDER BY created ASC`, `fields=*all` | 7 |
| `changelogs/<issueKey>/page-NNNN.json` | `GET /rest/api/3/issue/{key}/changelog` | 530 (one per issue, including the 210 with no history) |
| `sprint_issues/<sprintId>/page-NNNN.json` | `GET /rest/agile/1.0/sprint/{sprintId}/issue` | 36 |
| `capacity/page-NNNN.json` | `GET /capacity` (our own endpoint, not Jira's) | 1 |
| `manifest.json` | none (written by the extractor) | 1 |

That is 578 pages plus the manifest, 579 files at the default page sizes. Page numbers start at `0000` within each folder.

## Schema or contract

**Manifest.** `{"files": [{"file": <path relative to landing/>, "path": <request path>, "params": <query parameters sent>}, ...]}`, in request order. Staging reads this, never filenames.

**Discovery order** (`run_extraction`). Each step uses only what earlier responses returned:

1. Boards give board IDs and project keys (`location.projectKey`).
2. Each board's sprints give sprint IDs.
3. One JQL search per project key, in board order, gives the issue keys. The API rejects unbounded JQL, so there is one search per project.
4. Each issue's changelog, from the dedicated endpoint, not `expand=changelog`, which is capped at 100 histories.
5. Each sprint's issue list.
6. Capacity.

**Default page sizes** (`PageSizes`, the `maxResults` sent). Smaller values only add pages:

| boards | sprints | search | changelog | sprint_issues | capacity |
|---|---|---|---|---|---|
| 50 | 50 | 100 | 100 | 50 | 50 |

**Client behavior** (`JiraClient`):

- **Every request** carries `Authorization: Bearer <token>` and `Accept: application/json`. The default timeout is 10 s (`for_base_url`).
- **Retries.** Up to 3 attempts on a 5xx or a transport error (refused, reset, timed out), waiting 0.5 s and then 1 s.
- **No retry on 4xx.** Any 4xx, or any other non-200 status below 500, raises `ExtractionError` at once. A 401 message names `AAP_API_TOKEN`. Other errors include the API's `errorMessages` or `errors`.
- **Offset paging** (`offset_pages`, used by agile, changelog and capacity):
  - The next `startAt` advances by the number of items actually returned.
  - Stops on `isLast`. When a page has no `isLast` (the sprint issue list), stops once `total` items have been seen.
  - Raises if a page has neither flag, or if a non-last page is empty.
- **Token paging** (`token_pages`, used by search): passes `nextPageToken` back until `isLast`. Raises if `isLast` is missing, or if a non-last page has no token.
- **Raw bytes.** Each page keeps the response bytes exactly as the server sent them (`Page.body`). Those bytes are what gets landed.

**Replacement.**

1. The run writes into a scratch sibling, `.landing.partial`.
2. On any exception, the scratch directory is deleted and the previous `landing/` is untouched.
3. On success, `landing/` is renamed to `.landing.previous`, the scratch directory is renamed to `landing/`, and `.landing.previous` is deleted.

## How to run

From the repo root, with `raw_data/` generated (`uv run python scripts/generate_raw_data.py`) and `.env` copied from `.env.example`:

```bash
uv run uvicorn mock_api.app:app --port 8000                # terminal 1
uv run --env-file .env python -m ingestion.extract         # terminal 2; optional arg: landing dir
```

Without `--env-file .env` (and without the variables set inline), the run fails with `Missing AAP_API_BASE_URL and AAP_API_TOKEN`.

Prints `Landed raw responses in <dir>` and a page count per folder. Exits 0 on success. On an `ExtractionError` (for example a missing variable, a malformed base URL or a 401), it prints `Extraction failed: ...` to stderr and exits 1.

## How it is verified

`uv run python -m pytest tests/test_extraction.py`: 18 tests (13 functions; two parametrized, three and four ways). They run the whole extraction in-process: FastAPI's `TestClient` is an `httpx.Client`, so it is injected into `JiraClient` with no server started. They use the real `raw_data/`, and fail if it is missing.

| Test | What it proves |
|---|---|
| `test_landed_counts_match_the_shipped_dataset` | The landed files hold 3 boards, 36 sprints, 530 issues, 36 capacity rows, 1,488 status and 320 Sprint changelog items, and 320 sprint-issue entries. |
| `test_small_pages_land_the_same_records` | With page sizes 1–7, every endpoint needs several pages and the counts still match. Search returns the same issues in the same order. |
| `test_every_landed_issue_equals_the_serializer` | All 530 landed issues equal `serialize_issue`, and 320 have a `duedate`. |
| `test_landing_files_are_the_raw_responses` | The manifest lists every page. Replaying each manifest request returns the landed bytes exactly. |
| `test_second_run_is_byte_identical` | A rerun into the same directory is byte-identical and leaves no `.landing*` scratch directories. |
| `test_sabotage_stopping_one_page_early_fails_the_count_check` | **Sabotage:** a client that drops the last page of every multi-page stream fails the count check (35 capacity rows, fewer than 530 issues). |
| `test_missing_environment_fails_loudly` | Missing variables raise an error naming them, and `main` exits 1. |
| `test_env_example_lists_the_extraction_settings` | `.env.example` lists both variables, so a copied `.env` is enough with `--env-file .env`. |
| `test_malformed_base_url_fails_before_any_request[4 URLs]` | A base URL with no scheme, a broken scheme, a non-http scheme or no host raises an error naming `AAP_API_BASE_URL` and showing a valid example. |
| `test_empty_token_is_refused_before_any_request` | A blank token is refused when the client is built. |
| `test_rejected_token_fails_loudly_and_keeps_the_previous_landing` | A 401 raises an error naming `AAP_API_TOKEN`. The previous landing is unchanged, and no scratch directory is left. |
| `test_transient_errors_are_retried` | A 503 or a connection error is retried. Three 500s give up after 3 attempts. |
| `test_client_errors_are_never_retried[401/404/400]` | Each 4xx is tried exactly once. |

## Known limits

- **The swap is two renames, not atomic.** A crash between them leaves no `landing/`, only `.landing.previous`. That directory is not restored automatically. The next run deletes it before swapping.
- **A non-JSON 200 response raises a decode error, not `ExtractionError`.** `main` does not catch it, so the run stops with a traceback. Scratch is still cleaned up.
- **Unknown projects are not found.** Projects are discovered only through boards. A project with no board would not be searched.
- **One request after another.** Requests run sequentially, with no concurrency and no rate-limit (429) handling beyond the generic 4xx failure.
- **No incremental mode.** Every run extracts everything.

## Options rejected and why

- **`expand=changelog` on search or issue.** Capped at 100 histories. See [D-017](../decisions.md#d-017-dedicated-changelog-endpoint-not-expandchangelog).
- **The old `GET /rest/api/3/search`.** Removed by Atlassian, so the mock returns 404 ([`mock-api.md`](../mock-api.md) section 3.3).
- **One unbounded search.** Rejected by the API. See [D-016](../decisions.md#d-016-one-jql-search-per-project).
- **Parsing during extraction.** The raw layer stays an untouched record that staging can be rebuilt from. See [D-018](../decisions.md#d-018-raw-landing-files-are-kept-untouched).
- **Landing raw JSON in Postgres `jsonb` instead of files.** Not chosen. Rationale to confirm with Yassine ([D-018](../decisions.md#d-018-raw-landing-files-are-kept-untouched)).
