# 02. Staging

Status: done (Session 2). All eight tables load, and they are reconciled with `raw_data/` row by row:

- **Step 2.1:** teams, users, boards, sprints and capacity.
- **Step 2.1b:** replaced drop-and-recreate with truncate and reload ([D-022](../decisions.md#d-022-staging-reloads-by-truncate-and-copy-drop-and-recreate-only-on---rebuild)).
- **Step 2.2:** issues, status transitions and sprint memberships.
- **Step 2.3:** the row-by-row reconciliation with `raw_data/` and the sabotage checks ([`../etl-plan.md`](../etl-plan.md)).

## Purpose

Loads the raw pages in `landing/` into typed tables in the Postgres `staging` schema. Staging is a clean mirror: Jira's field names and formats become typed, named columns, and nothing is interpreted, aggregated or joined. Business logic starts in dbt ([03-dbt-star-schema.md](03-dbt-star-schema.md)).

The code is two modules:

- `ingestion/parse.py`: pure functions from landed pages to rows, with no database access.
- `ingestion/load_staging.py`: connection, table definitions and their check, one transaction per run, `TRUNCATE` and `COPY`.

## Inputs and outputs

**Inputs**

- **`landing/`,** written by `ingestion.extract` ([01-extraction.md](01-extraction.md)). It is read through `manifest.json`, never by listing files. Each manifest entry's request path says what the page holds (`parse.ENDPOINTS`). An unknown path, a listed file that is missing, or a page that is not valid JSON stops the load.
- **`POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`.** All five are required, with no defaults. The loader never reads `.env` itself; `uv run --env-file .env` puts them in the environment.

**Outputs**

- One table per entity in the target schema (default `staging`, created if missing).
- Every successful run replaces all rows. The tables themselves stay in place, so views on them survive.
- A failed run changes nothing.

## Schema or contract

Every table also has `_source_file text not null` (the manifest `file` the row came from) and `_loaded_at timestamptz not null` (one value per run, shared by every table). Each table has a primary key and no foreign keys ([D-019](../decisions.md#d-019-staging-has-primary-keys-but-no-foreign-keys)).

| Table | Primary key | Columns | Source |
|---|---|---|---|
| `teams` | `project_id` | `project_id bigint`, `project_key text`, `project_name text` | board `location` and each issue's `fields.project` |
| `users` | `account_id` | `account_id text`, `display_name text` | issue `assignee` and `reporter`, changelog `author` |
| `boards` | `board_id` | `board_id bigint`, `board_name text`, `board_type text`, `project_id bigint`, `project_key text` | board list pages |
| `sprints` | `sprint_id` | `sprint_id bigint`, `board_id bigint` (`originBoardId`), `sprint_name text`, `state text`, `start_at`, `end_at`, `complete_at` (`timestamptz`, nullable) | board sprint pages |
| `capacity` | `team_key`, `sprint_id` | `team_key text`, `sprint_id bigint`, `headcount integer`, `pto_days double precision` | `/capacity` pages (our own schema, not Jira's) |
| `issues` | `issue_id`, unique `issue_key` | `issue_id bigint`, `issue_key text`, `issue_type text`, `project_id bigint`, `status_id bigint`, `status_name text`, `assignee_account_id text`, `reporter_account_id text`, `parent_issue_id bigint`, `story_points numeric` (`customfield_10016`), `created_at timestamptz`, `resolved_at timestamptz`, `duedate date`, `summary text` | search pages (`fields=*all`) |
| `status_transitions` | `changelog_id` | `changelog_id bigint` (history `id`), `issue_id bigint`, `author_account_id text`, `transitioned_at timestamptz` (history `created`), `from_status_id bigint`, `from_status_name text`, `to_status_id bigint`, `to_status_name text` | changelog items with `field = "status"` |
| `sprint_memberships` | `issue_id`, `sprint_id` | `issue_id bigint`, `sprint_id bigint`, `added_at timestamptz` (history `created`), `changelog_id bigint` | changelog items with `fieldId = "customfield_10020"` |

Columns are `not null` unless Jira can leave them empty:

- the three sprint dates (future sprints);
- assignee and reporter;
- parent;
- story points (Epics);
- resolution and due dates;
- a changelog author (automation).

**Parsing rules** (`ingestion/parse.py`):

- **Timestamps.** Exactly two formats are accepted: `YYYY-MM-DDTHH:MM:SS.mmm+0000` (platform API) and `...+00:00` (agile API), with any offset. Both become aware UTC datetimes, stored as `timestamptz`. Anything else, including a timestamp with no offset, fails the load rather than being guessed (`parse_timestamp`).
- **One row per entity.** An entity seen on several pages is stored once, and its `_source_file` is the first page in manifest order. A later page must agree with it, column for column, or the load fails naming both files (`_add`). This is how teams from boards and from issues are checked against each other.
- **IDs.** Numeric Jira IDs (project, issue, status, history, sprint) arrive as strings on platform payloads and as numbers on agile ones. All are stored as `bigint`.
- **Status names** come from the fixed map (`parse.STATUS_NAMES`: 10000 To Do, 10001 In Progress, 10002 Code Review, 10003 QA, 10004 Done), never from the payload. The payload's own name (`status.name`, `fromString`, `toString`) must agree, or the load fails. So does an ID not in the map (`status_name`, [D-023](../decisions.md#d-023-staging-changelog-and-status-rules)).
- **Story points.** `customfield_10016` becomes `story_points numeric`. Epics keep it null and stay in `issues`.
- **Due dates.** `duedate` must be `YYYY-MM-DD` and becomes a `date`.
- **Transitions.** One row per changelog history with a `status` item, keyed by the history ID. A history with two status items fails the load.
- **Memberships.** For each Sprint item, every sprint ID in `to` but not in `from` (comma-separated lists) is one membership, with `added_at` = the history's `created`. An item that removes a sprint fails the load: v1 staging models additions only, and the data has none.
- **Changelog issue.** A changelog page belongs to the issue whose key is in its manifest request path. That key must be one the search pages returned.
- **Other changelog fields** (none in the mock) are ignored.
- **Precision.** `pto_days` is stored as `double precision` with full precision kept.
- **No team on users.** The API exposes no user-to-team link, and inferring one would be business logic.

**Refresh** ([D-022](../decisions.md#d-022-staging-reloads-by-truncate-and-copy-drop-and-recreate-only-on---rebuild)). `load_staging` parses every page before touching the database. Then, in one transaction:

1. `create schema if not exists`.
2. For each table: if it is missing, `create table`. If it exists, compare its columns (name, type, nullability, order) and primary key with `TABLES`. On any difference, stop with a message naming `--rebuild`.
3. `truncate` every table in one statement.
4. `COPY` each table's rows.
5. Commit.

`TRUNCATE` is transactional in Postgres, so any failure rolls back to the previous rows. The connection is autocommit, so the transaction block is a real `BEGIN`/`COMMIT`, not a savepoint. The loader refuses a connection that is already inside a transaction.

**`--rebuild`.** Step 2 becomes `drop table` then `create table` for every table, for a real schema change. It never uses CASCADE: if a view depends on a table, the drop fails, the whole run rolls back, and the view is kept. The order for a schema change is: drop the dependent views (or let dbt recreate them), run `--rebuild`, then run dbt.

## How to run

From the repo root, with Postgres up (`docker compose --env-file .env -f warehouse/docker-compose.yml up -d --wait`) and `landing/` extracted:

```bash
uv run --env-file .env python -m ingestion.load_staging              # landing/ -> schema staging
uv run --env-file .env python -m ingestion.load_staging DIR --schema NAME
uv run --env-file .env python -m ingestion.load_staging --rebuild    # after changing a table definition
```

Prints the rows loaded per table and exits 0. On a parse error, a missing variable, a changed table definition (without `--rebuild`) or a database error, it prints `Staging load failed: ...` to stderr and exits 1. Nothing is written.

## How it is verified

`uv run --env-file .env python -m pytest tests/test_staging.py -rs`: 33 tests, 0 skipped.

- **Landing.** The tests build their own landing in-process from the real `raw_data/`, as `tests/test_extraction.py` does.
- **Schema.** Tests marked `postgres` load into `staging_test`, which they drop before and after. They never touch `staging`.
- **Skips and failures.** Without the `POSTGRES_*` variables those tests skip with a reason, so a gate run must show zero skipped. With the variables set but Postgres unreachable, they fail (a fixture error, non-zero exit), never skip, with: `Postgres is unreachable at <host>:<port>. Start it with docker compose ...`.

| Test | What it proves |
|---|---|
| `test_both_timestamp_formats_parse_to_the_same_utc_instant` | `+0000` and `+00:00` give the same aware UTC instant. Other offsets are converted. |
| `test_unknown_timestamp_formats_are_refused` (4 cases) | No offset, `Z`, a space separator, or a non-ISO date each fail. |
| `test_unknown_request_path_is_refused` | A manifest path the extractor never requests fails. |
| `test_parsed_counts_match_the_shipped_dataset` | 3 teams, 12 users, 3 boards, 36 sprints, 36 capacity rows, 530 issues, 1,488 status transitions, 320 sprint memberships. |
| `test_parser_reads_the_manifest_not_the_directory` | An unlisted extra file is ignored. A listed file that is missing fails, naming it. |
| `test_conflicting_pages_are_refused` | A board whose project name disagrees with the issues' project fails the parse. |
| `test_loaded_counts_match_the_shipped_dataset` | The same counts, in Postgres. |
| `test_tables_have_typed_columns_primary_keys_and_no_foreign_keys` | Every column has its declared type. The primary keys are as specified, and there are no foreign keys. |
| `test_values_survive_the_round_trip` | Sprint instants are 09:00 UTC and 14 days long. `pto_days` equals the landed float exactly. Every `_source_file` is a manifest file. |
| `test_rerun_gives_identical_contents` | A second load gives identical rows on every column but `_loaded_at`, which has one value per run and changes between runs. |
| `test_sabotage_the_rerun_comparison_catches_a_changed_value` | **Sabotage:** changing one `pto_days` by 1e-9 makes that comparison fail. |
| `test_failed_parse_leaves_the_previous_tables_intact` | A truncated page fails the load, and every table, `_loaded_at` included, is unchanged. |
| `test_failure_mid_transaction_rolls_back_every_table` | A database error while loading the last table, after every table was truncated and the other seven refilled, rolls all of them back. |
| `test_reload_keeps_views_that_depend_on_staging` | A view on `teams` survives a normal reload and still returns 3 rows. **Sabotage:** `--rebuild` (drop and recreate) is refused while the view exists, and the refusal leaves the view and every row unchanged. |
| `test_changed_definition_stops_the_load_until_rebuild` (6 cases) | A changed type, an added column, a dropped column, changed nullability, a dropped primary key or a dropped unique constraint stops a normal load with a message naming `--rebuild`, leaving the rows unchanged. `main --rebuild` then restores every declared definition. |
| `test_unreachable_postgres_fails_with_a_clear_message` | With nothing listening on the configured port, the Postgres connection helper fails the test with the "unreachable" message rather than skipping. |
| `test_cli_names_the_missing_variable_and_writes_nothing` | A missing variable is named, `--env-file .env` is suggested, and the exit code is 1. |
| `test_issue_counts_epics_and_due_dates` | 530 issues, 30 of them Epics. Every Epic has null story points and every other issue has some. 320 have a due date. 1,488 transitions, 320 memberships. |
| `test_every_timestamp_is_aware_utc_in_both_formats` | All 2,759 non-null timestamps, `+0000` issue and changelog fields and `+00:00` sprint dates, are aware UTC. |
| `test_transitions_replay_to_each_issue_status` | An independent check of the map: for every issue, transitions in time order chain from To Do and end at the issue's own status. |
| `test_sabotage_a_wrong_status_map_is_refused` | **Sabotage:** with Code Review and QA swapped in the fixed map, parsing fails against the payload's names. |
| `test_unknown_status_id_is_refused` | A changelog item with status `10099` fails the load. |
| `test_sprint_items_add_each_new_sprint_and_refuse_removals` | `"A"` → `"A, 999"` adds exactly one membership. An item that removes the sprint fails the load. |
| `test_changelog_of_an_unknown_issue_is_refused` | A changelog whose request path names an issue the search never returned fails the load. |
| `test_issue_values_round_trip` | PHX-29 reads back from Postgres with its landed type, status, points, both timestamps (milliseconds kept), due date and parent. Epics have no points, and 320 issues have a due date. |

Three sabotage runs, each a throwaway pytest plugin that breaks one mechanism, show the tests guard what they claim:

- **`Connection.transaction` as a no-op:** only the mid-transaction test fails.
- **Every load forced to drop and recreate** (the 2.1 strategy): the view test and all 5 definition-change cases fail.
- **The definition check switched off:** all 5 definition-change cases fail (step 2.1b; the unique case was added in 2.2).
- **Sprint items made to yield no IDs** (step 2.2): every count test fails, and so do the timestamp, membership and definition-change tests, which load real data. 11 tests in all.

**Gate run for step 2.1** on the real `landing/`, into `staging`, through the CLI:

- **Counts:** 3 / 12 / 3 / 36 / 36.
- **Rerun:** the md5 of every table's contents without `_loaded_at` is identical across two runs, and the md5 with `_loaded_at` differs.
- **Failed run:** a copy of `landing/` with a truncated capacity page exits 1, and every table's md5, `_loaded_at` included, is unchanged.

**Gate run for step 2.1b**, same setup, against the tables step 2.1 created:

- **Definition check:** passes on the existing tables, with content identical to the 2.1 load.
- **Rerun:** content identical, `_loaded_at` changed.
- **Failed run:** every table unchanged, `_loaded_at` included.
- **`--rebuild`:** content identical.

**Gate run for step 2.2**, same setup, on the schema left by 2.1b:

- **Existing tables:** the five pass the definition check with unchanged content. The three new tables are created.
- **Counts:** 530 issues (30 Epics, 0 with story points), 320 with a due date, 1,488 transitions, 320 memberships.
- **Status names:** none differ from the fixed map. The last data timestamp is 2024-07-05 15:35:28.261 UTC.
- **Rerun, failed run, `--rebuild`:** identical content on a rerun. A truncated changelog page exits 1 and leaves all eight tables unchanged. `--rebuild` gives identical content.

### Reconciliation with `raw_data/` (step 2.3)

`tests/test_staging_reconciliation.py`, 10 tests (8 from step 2.3, 2 from step 3.4).

**Independence.** The expected side is built from `raw_data/*.json` with plain `json` and `datetime`. It imports nothing from `mock_api/` (no serializer, no ID hashing) and calls no `ingestion.parse` helper, and a test checks that. The staging side is read from Postgres after a normal load.

**Join keys.** Staging holds Jira IDs and `raw_data/` holds internal ones, so the two sides meet on natural keys, each checked unique in `raw_data/`:

- team: project key;
- user: display name;
- issue: issue key;
- sprint: team key + sprint name.

Staging is read with left joins, so a dangling ID shows up as a mismatch. Rows are compared as multisets.

| Table | Compared with `raw_data/` | Not compared, and why |
|---|---|---|
| `teams` | project key, name | `base_headcount`: no API counterpart |
| `users` | display name | account ID (a hash of the raw ID); team: not exposed by the API |
| `boards` | one per team, by project key | `raw_data/` has no boards |
| `sprints` | team, name, state, start and end instant (the date at 09:00 UTC), `complete_at` (from step 3.4: the end instant when closed, else null; derived, see [D-032](../decisions.md#d-032-sprint-complete_at-is-reconciled-against-a-derived-value)) | none |
| `capacity` | team, sprint, headcount, `pto_days` (exact float) | none |
| `issues` | key, team, type, status, story points, due date, created, resolution date, parent, assignee, reporter | `summary`: synthetic in the mock, absent from `raw_data/` |
| `status_transitions` | issue, timestamp, from and to status, author | transition ID (a hash) |
| `sprint_memberships` | issue, sprint, `added_at` | changelog ID (a hash) |

**Timestamp rule.** The precision is not identical, so the rule is stated in the test and here:

- **The difference.** `raw_data/` timestamps are naive (UTC by convention, [`../mock-api.md`](../mock-api.md) section 8) with microseconds. The API truncates them to milliseconds.
- **The rule.** Each raw value is read as UTC and **truncated (floored) to milliseconds**, then compared for exact equality. Nothing else is loosened. Dates (`duedate`, sprint dates) and `pto_days` are compared exactly as they are.
- **Values affected:** 2,403 of the 2,651 raw timestamps:
  - 529 of 530 `created`;
  - 313 of 313 `resolutiondate`;
  - 1,485 of 1,488 transition timestamps;
  - 76 of 320 `added_at`. Memberships made at sprint planning are exactly 09:00:00.
- **Truncation, not rounding.** `test_timestamp_rule_is_truncation_to_milliseconds` checks that staging holds only whole milliseconds, that truncation matches all 2,651 values, and that rounding instead would mismatch 1,236 of them.

| Test | What it proves |
|---|---|
| `test_raw_natural_keys_are_unique` | The join keys identify one row each in `raw_data/`. |
| `test_every_staging_table_equals_raw_data_row_by_row` | All 8 tables equal `raw_data/` on the fields above, and every count matches. |
| `test_changelog_memberships_equal_the_sprint_issue_lists` | The 320 changelog-derived memberships and the 320 (sprint, issue) pairs on the landed sprint issue lists are the same set, checked in both directions. |
| `test_timestamp_rule_is_truncation_to_milliseconds` | The rule above, with its counts. |
| `test_sabotage_1_broken_timestamp_parsing_fails_the_reconciliation` (2 cases) | **Sabotage 1.** `+00:00` misread as +01:00 fails exactly `sprints`. `+0000` misread fails exactly `issues`, `status_transitions` and `sprint_memberships`. After `monkeypatch.undo()`, the reload reconciles cleanly. |
| `test_sabotage_2_dropping_one_landed_page_fails_the_counts` | **Sabotage 2.** Removing `changelogs/PHX-29/page-0000.json` and its manifest entry from a copy: the load succeeds, but the counts fail (1,488 → 1,484 transitions, 320 → 319 memberships). Reloading the full landing passes again. |
| `test_sabotage_3_a_changed_sprint_complete_at_or_state_fails_the_reconciliation` (2 cases) | **Sabotage 3** (step 3.4). Moving one sprint's `complete_at` by 1 ms, or setting its state to `active`, fails exactly `sprints` (one row missing, one extra). A reload reconciles cleanly. |
| `test_reconciliation_is_independent_of_the_api_and_the_parser` | No `mock_api` import. The expected side calls neither `parse.` nor `loader.`. |

Two more throwaway sabotage runs each fail the row-by-row test with an `issues` mismatch: one swaps an issue's assignee and reporter, the other adds 1 to one issue's story points.

**Gate run for step 2.3,** the same reconciliation run read-only against the real `staging` schema and `landing/`:

- **Row by row:** all 8 tables equal `raw_data/`, and no count differs.
- **Memberships:** 320 from changelogs and 320 from sprint lists, with 0 on one side only.
- **Suite:** `uv run --env-file .env python -m pytest -rs` gives 54 passed, 0 skipped.

## Known limits

- **Sprint removals are not modeled.** A Sprint item that takes an issue out of a sprint fails the load. The v1 data has none ([D-023](../decisions.md#d-023-staging-changelog-and-status-rules)).
- **Status changes beyond the fixed map fail.** A renamed or added status on a real site would need the map updated.
- **The whole landing is held in memory** while parsing. That is fine at 578 pages, not for a real site.
- **No incremental load.** Every run replaces every table.
- **Lock during the load.** `truncate` takes an exclusive lock, so readers of `staging` wait until the load commits.
- **Not MVCC-safe for old snapshots.** A concurrent transaction that started before the load (repeatable read) can see the truncated tables as empty. Not an issue for a single-user pipeline ([D-022](../decisions.md#d-022-staging-reloads-by-truncate-and-copy-drop-and-recreate-only-on---rebuild)).
- **`--rebuild` needs dependent views gone.** It refuses while a view depends on a table, by design.

## Options rejected and why

- **Foreign keys in staging.** Relationships are checked by dbt tests in step 3.4 instead ([D-019](../decisions.md#d-019-staging-has-primary-keys-but-no-foreign-keys)).
- **Reading `.env` in the loader,** as `scripts/check_warehouse.py` does. The spec says environment variables only, so where credentials come from stays visible in the command.
- **Lenient timestamp parsing** (e.g. `datetime.fromisoformat` on anything). It would accept a naive timestamp and silently assume a zone. Two strict formats keep any contract change loud.
- **Loading with `INSERT` per row, or via pandas or SQLAlchemy** (both are dependencies). `COPY` through psycopg is one round trip per table and keeps the types explicit in the DDL ([D-021](../decisions.md#d-021-postgres-driver-psycopg-3)).
- **Drop and recreate on every run** (step 2.1), `DROP ... CASCADE`, swapping tables by rename, `DELETE` instead of `TRUNCATE`, and incremental upserts. See [D-022](../decisions.md#d-022-staging-reloads-by-truncate-and-copy-drop-and-recreate-only-on---rebuild) for each option and why it lost to truncate and reload.
