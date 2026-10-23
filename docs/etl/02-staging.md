# 02. Staging

Status: in progress. Step 2.1 is done: teams, users, boards, sprints and capacity. Step 2.1b replaced drop-and-recreate with truncate and reload ([D-022](../decisions.md#d-022-staging-reloads-by-truncate-and-copy-drop-and-recreate-only-on---rebuild)). Issues, status transitions and sprint memberships come in step 2.2. The reconciliation with `raw_data/` comes in step 2.3 ([`../etl-plan.md`](../etl-plan.md)).

## Purpose

Loads the raw pages in `landing/` into typed tables in the Postgres `staging` schema. Staging is a clean mirror: Jira's field names and formats become typed, named columns, and nothing is interpreted, aggregated or joined. Business logic starts in dbt ([03-dbt.md](03-dbt.md)).

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

Every column is `not null` except the three sprint dates, which Jira leaves empty on future sprints.

**Parsing rules** (`ingestion/parse.py`):

- **Timestamps.** Exactly two formats are accepted: `YYYY-MM-DDTHH:MM:SS.mmm+0000` (platform API) and `...+00:00` (agile API), with any offset. Both become aware UTC datetimes, stored as `timestamptz`. Anything else, including a timestamp with no offset, fails the load rather than being guessed (`parse_timestamp`).
- **One row per entity.** An entity seen on several pages is stored once, and its `_source_file` is the first page in manifest order. A later page must agree with it, column for column, or the load fails naming both files (`_add`). This is how teams from boards and from issues are checked against each other.
- **IDs.** Project IDs arrive as a string on issues (`"18405660"`) and as a number on boards. Both are stored as `bigint`.
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

`uv run --env-file .env python -m pytest tests/test_staging.py -rs`: 24 tests, 0 skipped.

- **Landing.** The tests build their own landing in-process from the real `raw_data/`, as `tests/test_extraction.py` does.
- **Schema.** Tests marked `postgres` load into `staging_test`, which they drop before and after. They never touch `staging`.
- **Skips and failures.** Without the `POSTGRES_*` variables those tests skip with a reason, so a gate run must show zero skipped. With the variables set but Postgres unreachable, they fail (a fixture error, non-zero exit), never skip, with: `Postgres is unreachable at <host>:<port>. Start it with docker compose ...`.

| Test | What it proves |
|---|---|
| `test_both_timestamp_formats_parse_to_the_same_utc_instant` | `+0000` and `+00:00` give the same aware UTC instant. Other offsets are converted. |
| `test_unknown_timestamp_formats_are_refused` (4 cases) | No offset, `Z`, a space separator, or a non-ISO date each fail. |
| `test_unknown_request_path_is_refused` | A manifest path the extractor never requests fails. |
| `test_parsed_counts_match_the_shipped_dataset` | 3 teams, 12 users, 3 boards, 36 sprints, 36 capacity rows. |
| `test_parser_reads_the_manifest_not_the_directory` | An unlisted extra file is ignored. A listed file that is missing fails, naming it. |
| `test_conflicting_pages_are_refused` | A board whose project name disagrees with the issues' project fails the parse. |
| `test_loaded_counts_match_the_shipped_dataset` | The same counts, in Postgres. |
| `test_tables_have_typed_columns_primary_keys_and_no_foreign_keys` | Every column has its declared type. The primary keys are as specified, and there are no foreign keys. |
| `test_values_survive_the_round_trip` | Sprint instants are 09:00 UTC and 14 days long. `pto_days` equals the landed float exactly. Every `_source_file` is a manifest file. |
| `test_rerun_gives_identical_contents` | A second load gives identical rows on every column but `_loaded_at`, which has one value per run and changes between runs. |
| `test_sabotage_the_rerun_comparison_catches_a_changed_value` | **Sabotage:** changing one `pto_days` by 1e-9 makes that comparison fail. |
| `test_failed_parse_leaves_the_previous_tables_intact` | A truncated page fails the load, and every table, `_loaded_at` included, is unchanged. |
| `test_failure_mid_transaction_rolls_back_every_table` | A database error while loading capacity, after every table was truncated and the other four refilled, rolls all of them back. |
| `test_reload_keeps_views_that_depend_on_staging` | A view on `teams` survives a normal reload and still returns 3 rows. **Sabotage:** `--rebuild` (drop and recreate) is refused while the view exists, and the refusal leaves the view and every row unchanged. |
| `test_changed_definition_stops_the_load_until_rebuild` (5 cases) | A changed type, an added column, a dropped column, changed nullability or a dropped primary key stops a normal load with a message naming `--rebuild`, leaving the rows unchanged. `main --rebuild` then restores every declared definition. |
| `test_unreachable_postgres_fails_with_a_clear_message` | With nothing listening on the configured port, the Postgres connection helper fails the test with the "unreachable" message rather than skipping. |
| `test_cli_names_the_missing_variable_and_writes_nothing` | A missing variable is named, `--env-file .env` is suggested, and the exit code is 1. |

Three sabotage runs, each a throwaway pytest plugin that breaks one mechanism, show the tests guard what they claim:

- **`Connection.transaction` as a no-op:** only the mid-transaction test fails.
- **Every load forced to drop and recreate** (the 2.1 strategy): the view test and all 5 definition-change cases fail.
- **The definition check switched off:** all 5 definition-change cases fail.

**Gate run for step 2.1** on the real `landing/`, into `staging`, through the CLI:

- **Counts:** 3 / 12 / 3 / 36 / 36.
- **Rerun:** the md5 of every table's contents without `_loaded_at` is identical across two runs, and the md5 with `_loaded_at` differs.
- **Failed run:** a copy of `landing/` with a truncated capacity page exits 1, and every table's md5, `_loaded_at` included, is unchanged.

**Gate run for step 2.1b**, same setup, against the tables step 2.1 created:

- **Definition check:** passes on the existing tables, with content identical to the 2.1 load.
- **Rerun:** content identical, `_loaded_at` changed.
- **Failed run:** every table unchanged, `_loaded_at` included.
- **`--rebuild`:** content identical.

## Known limits

- **Not loaded yet:** issues, status transitions and sprint memberships (step 2.2). Until then, the status ID map and the `customfield_10016` mapping are not exercised.
- **No row-level reconciliation with `raw_data/` yet** (step 2.3).
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
