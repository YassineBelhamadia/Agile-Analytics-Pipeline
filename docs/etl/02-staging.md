# 02. Staging

Status: in progress. Step 2.1 is done: teams, users, boards, sprints and capacity. Issues, status transitions and sprint memberships come in step 2.2. The reconciliation with `raw_data/` comes in step 2.3 ([`../etl-plan.md`](../etl-plan.md)).

## Purpose

Loads the raw pages in `landing/` into typed tables in the Postgres `staging` schema. Staging is a clean mirror: Jira's field names and formats become typed, named columns, and nothing is interpreted, aggregated or joined. Business logic starts in dbt ([03-dbt.md](03-dbt.md)).

The code is two modules:

- `ingestion/parse.py`: pure functions from landed pages to rows, with no database access.
- `ingestion/load_staging.py`: connection, DDL, one transaction per run, and `COPY`.

## Inputs and outputs

**Inputs**

- **`landing/`,** written by `ingestion.extract` ([01-extraction.md](01-extraction.md)). It is read through `manifest.json`, never by listing files. Each manifest entry's request path says what the page holds (`parse.ENDPOINTS`). An unknown path, a listed file that is missing, or a page that is not valid JSON stops the load.
- **`POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`.** All five are required, with no defaults. The loader never reads `.env` itself; `uv run --env-file .env` puts them in the environment.

**Outputs**

- One table per entity in the target schema (default `staging`, created if missing), fully replaced on every successful run.
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

**Refresh.** `load_staging` parses every page before touching the database. Then, in one transaction:

1. `create schema if not exists`;
2. for each table in order (teams, users, boards, sprints, capacity): `drop table if exists`, `create table`, then `COPY`;
3. commit.

Postgres DDL is transactional, so any failure rolls back every table to its previous contents. The connection is autocommit, so the transaction block is a real `BEGIN`/`COMMIT`, not a savepoint. The loader refuses a connection that is already inside a transaction.

## How to run

From the repo root, with Postgres up (`docker compose --env-file .env -f warehouse/docker-compose.yml up -d --wait`) and `landing/` extracted:

```bash
uv run --env-file .env python -m ingestion.load_staging              # landing/ -> schema staging
uv run --env-file .env python -m ingestion.load_staging DIR --schema NAME
```

Prints the rows loaded per table and exits 0. On a parse error, a missing variable or a database error, it prints `Staging load failed: ...` to stderr and exits 1, and nothing is written.

## How it is verified

`uv run --env-file .env python -m pytest tests/test_staging.py -rs`: 17 tests, 0 skipped.

- **Landing.** The tests build their own landing in-process from the real `raw_data/`, as `tests/test_extraction.py` does.
- **Schema.** Tests marked `postgres` load into `staging_test`, which they drop before and after. They never touch `staging`.
- **Skips.** Without the `POSTGRES_*` variables those tests skip with a reason, so a gate run must show zero skipped.

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
| `test_failure_mid_transaction_rolls_back_every_table` | A database error while loading capacity, after the other four tables were already replaced, rolls all of them back. |
| `test_cli_names_the_missing_variable_and_writes_nothing` | A missing variable is named, `--env-file .env` is suggested, and the exit code is 1. |

The mid-transaction test was itself checked by sabotage. With `Connection.transaction` replaced by a no-op, it fails while the count and rerun tests still pass.

**Gate run for step 2.1** on the real `landing/`, into `staging`, through the CLI:

- **Counts:** 3 / 12 / 3 / 36 / 36.
- **Rerun:** the md5 of every table's contents without `_loaded_at` is identical across two runs, and the md5 with `_loaded_at` differs.
- **Failed run:** a copy of `landing/` with a truncated capacity page exits 1, and every table's md5, `_loaded_at` included, is unchanged.

## Known limits

- **Not loaded yet:** issues, status transitions and sprint memberships (step 2.2). Until then, the status ID map and the `customfield_10016` mapping are not exercised.
- **No row-level reconciliation with `raw_data/` yet** (step 2.3).
- **The whole landing is held in memory** while parsing. That is fine at 578 pages, not for a real site.
- **No incremental load.** Every run replaces every table.
- **Lock during the load.** `drop table` takes an exclusive lock, so readers of `staging` wait until the load commits.

## Options rejected and why

- **Foreign keys in staging.** Relationships are checked by dbt tests in step 3.4 instead ([D-019](../decisions.md#d-019-staging-has-primary-keys-but-no-foreign-keys)).
- **Reading `.env` in the loader,** as `scripts/check_warehouse.py` does. The spec says environment variables only, so where credentials come from stays visible in the command.
- **Lenient timestamp parsing** (e.g. `datetime.fromisoformat` on anything). It would accept a naive timestamp and silently assume a zone. Two strict formats keep any contract change loud.
- **Loading with `INSERT` per row, or via pandas or SQLAlchemy** (both are dependencies). `COPY` through psycopg is one round trip per table and keeps the types explicit in the DDL ([D-021](../decisions.md#d-021-postgres-driver-psycopg-3)).
- **Truncate and insert instead of drop and create.** A full drop and create also applies DDL changes on the next run. Both are transactional in Postgres.
