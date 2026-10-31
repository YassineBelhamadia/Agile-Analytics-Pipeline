# 03. dbt star schema

Status: in progress (Session 3). Done so far:

- **Step 3.1:** the dbt project, the profile, the staging sources and the 8 `stg_` views.

Remaining: dimensions (3.2), facts (3.3), and data quality tests with the final docs (3.4). The plan is in [`../etl-plan.md`](../etl-plan.md).

## Purpose

Turns the typed staging mirror ([02-staging.md](02-staging.md)) into a star schema that Session 4's metrics and the dashboard read. dbt Core runs the SQL models in dependency order and holds the data tests next to them ([D-005](../decisions.md#d-005-locked-stack)).

The layers:

| Layer | Schema | Materialized as | What it does |
|---|---|---|---|
| Sources | `staging` | tables, loaded by `ingestion.load_staging` | not built by dbt, only declared |
| `stg_` models | `stg` | views, one per staging table | rename to one naming convention, nothing else |
| Dimensions and facts | `marts` | tables | the star schema (from step 3.2) |

## Inputs and outputs

**Inputs**

- The 8 tables in schema `staging`, declared as the dbt source `staging` (`dbt/models/staging/_staging__sources.yml`).
- `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, read by `dbt/profiles.yml` with `env_var()` and no defaults ([D-026](../decisions.md#d-026-dbt-core-112-with-dbt-postgres-111-in-dbt-profile-from-the-environment)).

**Outputs**

- Schema `stg`: 8 views, replaced by every `dbt build` or `dbt run`. They read staging live, so they show the current rows right after a reload with no dbt run.
- Schema `marts`: tables (from step 3.2).
- `dbt/target/` (compiled SQL, `manifest.json`, run results) and `dbt/logs/`, both gitignored.

## Schema or contract

**Schema names** ([D-027](../decisions.md#d-027-two-dbt-layers-with-exact-schema-names-stg-views-and-marts-tables)). dbt's built-in `generate_schema_name` would return `<profile schema>_<custom schema>`, for example `marts_stg`. `dbt/macros/generate_schema_name.sql` returns the custom schema as is. So `+schema: stg` (set for `models/staging/` in `dbt_project.yml`) builds into `stg`. A node with no custom schema falls back to the profile's `marts`.

The macro and the source also read two vars, both used only by the tests:

- `schema_suffix` (default empty) is appended to every built schema;
- `staging_schema` (default `staging`) is the source schema.

With `{staging_schema: staging_test, schema_suffix: _test}`, dbt reads `staging_test` and builds `stg_test` and `marts_test`.

**`stg_` views.** One per staging table, named `stg_<table>`. Each one renames keys to `<entity>_id` and keeps every other column as it is, `_source_file` and `_loaded_at` included.

| View | Renamed columns | Rows (SEED=22) |
|---|---|---|
| `stg_teams` | `project_id` → `team_id`, `project_key` → `team_key`, `project_name` → `team_name` | 3 |
| `stg_users` | `account_id` → `user_id` | 12 |
| `stg_boards` | `project_id` → `team_id`, `project_key` → `team_key` | 3 |
| `stg_sprints` | `state` → `sprint_state` | 36 |
| `stg_capacity` | none (`team_key` is a project key, as in `/capacity`) | 36 |
| `stg_issues` | `project_id` → `team_id`, `assignee_account_id` → `assignee_user_id`, `reporter_account_id` → `reporter_user_id` | 530 |
| `stg_status_transitions` | `author_account_id` → `author_user_id` | 1,488 |
| `stg_sprint_memberships` | none | 320 |

## How to run

From the repo root, with Postgres up and staging loaded ([02-staging.md](02-staging.md#how-to-run)):

```bash
uv run --env-file .env dbt debug --project-dir dbt --profiles-dir dbt   # profile and connection check
uv run --env-file .env dbt build --project-dir dbt --profiles-dir dbt   # build every model and run its tests
```

The subcommand comes before the flags (`dbt build --project-dir ...`). Flags placed before it are refused, with exit code 2.

After `ingestion.load_staging`, no dbt run is needed for the `stg_` views: they read the reloaded tables. The `marts` tables need `dbt build` again.

**`.env` is also read by dbt itself.** dbt-core 1.12 loads `.env` from the working directory or a parent, without overriding variables already set ([D-026](../decisions.md#d-026-dbt-core-112-with-dbt-postgres-111-in-dbt-profile-from-the-environment)). From the repo root, a command without `--env-file .env` still connects. The documented command keeps the flag, so it works the same where there is no `.env`, as in CI.

## How it is verified

`uv run --env-file .env python -m pytest tests/test_dbt.py -rs`: 5 tests, 0 skipped.

- **Isolation.** The Postgres tests load staging into `staging_test` and run dbt with the test vars, so dbt builds `stg_test` (and later `marts_test`). All three schemas are dropped before and after, and `staging`, `stg` and `marts` are never touched. dbt's target and log files go to a temporary directory.
- **No `.env`.** dbt runs from that temporary directory, so it finds no `.env` and depends on the process environment alone.

| Test | What it proves |
|---|---|
| `test_profile_takes_every_connection_value_from_the_environment` | host, port, user, password and dbname in `dbt/profiles.yml` are all `env_var('POSTGRES_*')`. |
| `test_dbt_debug_connects_with_the_environment` | `dbt debug` passes ("All checks passed!"). |
| `test_sabotage_a_wrong_credential_fails_dbt_debug_clearly` | **Sabotage:** a wrong `POSTGRES_PASSWORD` fails `dbt debug` with `password authentication failed`. Removing `POSTGRES_HOST` fails it with `Env var required but not provided: 'POSTGRES_HOST'`. |
| `test_stg_views_build_count_and_survive_a_staging_reload` | `dbt build --select staging` creates exactly the 8 views, with the shipped counts. After a normal `load_staging`, the views are the same objects (same oids) with the same counts. |
| `test_dbt_never_writes_outside_the_test_schemas` | With the test vars, every compiled model is in `stg_test` and every source in `staging_test`. |

**Gate run for step 3.1**, on the real schemas, through the documented commands:

- **`dbt debug`:** "All checks passed!", exit 0.
- **`dbt build`:** `PASS=8 WARN=0 ERROR=0`. The 8 views are in `stg`.
- **Counts through the views:** 3 teams, 12 users, 3 boards, 36 sprints, 36 capacity, 530 issues, 1,488 transitions, 320 memberships.
- **Reload:** after `uv run --env-file .env python -m ingestion.load_staging`, the view names, oids and counts are identical.
- **Sabotage:**
  - `POSTGRES_PASSWORD=wrong` gives `Connection test: [ERROR]` and `password authentication failed for user "aap"`, exit 1.
  - Without `POSTGRES_HOST`, run outside the repo, it gives `Env var required but not provided: 'POSTGRES_HOST'`, exit 2.

## Known limits

- **Global schema names.** `stg` and `marts` have no per-developer or per-target prefix, so two people sharing a database would overwrite each other's models ([D-027](../decisions.md#d-027-two-dbt-layers-with-exact-schema-names-stg-views-and-marts-tables)).
- **dbt reads `.env` on its own** from the working directory. A missing variable can be filled from a file the command doesn't name ([D-026](../decisions.md#d-026-dbt-core-112-with-dbt-postgres-111-in-dbt-profile-from-the-environment)).
- **A staging schema change needs the views rebuilt.** `load_staging --rebuild` refuses while the `stg_` views depend on staging ([D-022](../decisions.md#d-022-staging-reloads-by-truncate-and-copy-drop-and-recreate-only-on---rebuild)). Drop the `stg` schema first, rebuild, then `dbt build`.

## Options rejected and why

- **dbt v2, dbt-core 1.13 (beta), a profile in `~/.dbt/`, literal or defaulted credentials:** [D-026](../decisions.md#d-026-dbt-core-112-with-dbt-postgres-111-in-dbt-profile-from-the-environment).
- **dbt's default schema concatenation, `stg_` as tables, tests on the real schemas or a second database:** [D-027](../decisions.md#d-027-two-dbt-layers-with-exact-schema-names-stg-views-and-marts-tables).
- **Surrogate keys and a date dimension:** [D-028](../decisions.md#d-028-star-schema-with-natural-keys-and-no-date-dimension).
- **A hand-written user-to-team mapping, or picking the most frequent team:** [D-029](../decisions.md#d-029-a-users-team-is-derived-from-the-issues-assigned-to-them).
