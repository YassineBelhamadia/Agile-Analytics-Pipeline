# ETL commit plan

This plan lists every commit for ETL Sessions 2 to 5. Each step gives its ID, scope, expected files, the gate that must pass before the commit, and the exact commit message. How a session runs (branches, gates, failure handling, review) is in [`workflow.md`](workflow.md).

Rules for this file:

- **Messages are verbatim.** A step's commit message is used exactly as written here. If it no longer fits the change, stop and ask. Do not edit it on the fly.
- **Provisional steps.** Sessions 4 and 5 are outlines (Session 3 was detailed and confirmed on 2026-10-31). Their messages are *provisional, finalized at session start*, and each of those sessions is detailed in this file (scope, files, gates, final messages) and confirmed before work begins.
- **Format.** Conventional Commits with type `feat`, `fix`, `refactor`, `docs`, `chore`, `test` or `ci`. The subject is 72 characters or fewer, then a blank line, then a 1–3 line body. Never mention Claude, and never add co-author or session trailers. `.githooks/commit-msg` enforces this ([`workflow.md`](workflow.md#hooks)).
- **Targets.** Counts come from the shipped dataset (SEED=22) in [`CLAUDE.md`](../CLAUDE.md#shipped-dataset-seed22-verification-targets).

| Step | Session | Commit subject | Status |
|---|---|---|---|
| 2.1 | 2 | `feat(staging): add staging schema and loader for reference entities` | done |
| 2.1b | 2 | `refactor(staging): truncate and reload instead of drop and recreate` | done |
| 2.2 | 2 | `feat(staging): load issues, status transitions and sprint memberships` | done |
| 2.3 | 2 | `test(staging): reconcile staging with raw_data and add sabotage checks` | done |
| 3.1 | 3 | `chore(dbt): initialize project with staging sources` | done |
| 3.2 | 3 | `feat(dbt): add team, user, sprint and issue dimensions` | done |
| 3.3 | 3 | `feat(dbt): add status transition fact and capacity model` | done |
| 3.4 | 3 | `test(dbt): add relationship and data quality tests` | done |
| 4.1 | 4 | `feat(metrics): add sprint metrics reconciled with sprint_report` | provisional |
| 4.2 | 4 | `feat(metrics): add business question models 1 to 3` | provisional |
| 4.3 | 4 | `feat(metrics): add business question models 4 to 6` | provisional |
| 5.1 | 5 | `feat(pipeline): add one-command pipeline run` | provisional |
| 5.2 | 5 | `test(pipeline): add pytest wrappers for verify scripts` | provisional |
| 5.3 | 5 | `ci: add GitHub Actions workflow` | provisional |
| 5.4 | 5 | `docs: finalize pipeline documentation and clean-run guide` | provisional |

---

## Session 2: staging

Branch: `feat/etl-session-2`.

**Goal.** Load the files under `landing/`, which `uv run python -m ingestion.extract` produces (see [`etl/01-extraction.md`](etl/01-extraction.md)), into typed tables in a `staging` schema in Postgres. Staging is a clean mirror with no business logic.

### Spec (applies to all three steps)

- **Input.** The parser reads `landing/manifest.json`, never filenames. Each manifest entry gives the file, the request path and the params. The path tells the parser what the page holds.
- **Tables.** One per entity: teams, users, boards, sprints, issues, status transitions, sprint memberships and capacity.
- **Sources per table.**
  - Teams come from projects: the `project` object on each issue, and the board `location`.
  - Users come from `assignee`, `reporter` and changelog `author`, deduplicated by `accountId`.
  - Boards and sprints come from the agile list pages.
  - Capacity comes from the `/capacity` pages, which use our own schema, not Jira's.
- **Field mapping.**
  - `customfield_10016` becomes `story_points`. Epics keep a null value.
  - Both timestamp formats, `+0000` (platform) and `+00:00` (agile), are parsed to UTC `timestamptz`.
  - `duedate` becomes a `date`.
  - Status IDs `10000`–`10004` map to names through the fixed map: To Do, In Progress, Code Review, QA, Done. An unknown ID fails the load loudly. It is never guessed from `toString`.
- **Derived rows.**
  - Transitions come from changelog items with `field = "status"`.
  - Memberships come from items with `fieldId = "customfield_10020"`, with `added_at` = the history's `created`.
  - Epics are kept as issues, with null story points.
- **Lineage.** Every table gets `_source_file` (the manifest `file`) and `_loaded_at` (one timestamp per run).
- **Keys.** Primary keys, no foreign keys. Relationships are checked later by dbt tests (decision in [`decisions.md`](decisions.md)).
- **Refresh.** Each run is a full refresh inside one transaction, and a failed run rolls back, leaving the previous rows as they were. From step 2.1b, tables are created once and checked against their definition, then truncated and refilled with `COPY`. `--rebuild` drops and recreates them, without CASCADE, for a schema change ([D-022](decisions.md#d-022-staging-reloads-by-truncate-and-copy-drop-and-recreate-only-on---rebuild)). Step 2.1 dropped and recreated on every run.
- **Connection.** From the `POSTGRES_*` environment variables only (`POSTGRES_HOST`, `POSTGRES_PORT` = 5433, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`). The loader does not read `.env` itself, so export the variables first (`set -a; . ./.env; set +a`) or use `uv run --env-file .env`. This differs from `scripts/check_warehouse.py`, which falls back to `.env`.
- **Driver.** psycopg 3, because it is the maintained PostgreSQL adapter for Python, its `COPY` support fits bulk loads, and `scripts/check_warehouse.py` already uses it. It is already a dependency (`psycopg[binary]>=3.3.6` in `pyproject.toml`, added with uv), so no `uv add` is needed.
- **Command.** `uv run python -m ingestion.load_staging`, reading `landing/` by default. The loader also takes a target schema (default `staging`), so tests can load into their own schema.
- **Tests.** Tests that need Postgres use a separate schema, `staging_test`, which they drop afterwards. They never touch `staging`. They are marked `postgres` and skip with a visible reason when `POSTGRES_*` is unset. A gate run must show zero skipped tests (`pytest -rs`).

Proposed tables. Column names may change at step start; keys and types are the contract.

| Table | Primary key | Main columns |
|---|---|---|
| `teams` | `project_id` | `project_key`, `project_name` |
| `users` | `account_id` | `display_name` |
| `boards` | `board_id` | `board_name`, `board_type`, `project_id`, `project_key` |
| `sprints` | `sprint_id` | `board_id`, `sprint_name`, `state`, `start_at`, `end_at`, `complete_at` |
| `capacity` | `team_key`, `sprint_id` | `headcount`, `pto_days` (double precision, full precision kept) |
| `issues` | `issue_id` | `issue_key` (unique), `issue_type`, `project_id`, `status_id`, `status_name`, `assignee_account_id`, `reporter_account_id`, `parent_issue_id`, `story_points`, `created_at`, `resolved_at`, `duedate`, `summary` |
| `status_transitions` | `changelog_id` | `issue_id`, `author_account_id`, `transitioned_at`, `from_status_id`, `from_status_name`, `to_status_id`, `to_status_name` |
| `sprint_memberships` | `issue_id`, `sprint_id` | `added_at`, `changelog_id` |

Every table also carries `_source_file` and `_loaded_at`. Users have no team column: the API exposes no user-to-team link, and inferring one would be business logic.

### Step 2.1: reference entities

- **Scope.** Staging schema, the transaction and connection handling, and the manifest-driven parser for teams, users, boards, sprints and capacity.
- **Expected files.**
  - `ingestion/load_staging.py`: entry point, connection, transaction, DDL, load.
  - `ingestion/parse.py`: pure functions from landed pages to rows, with no database access.
  - `tests/test_staging.py`
  - `docs/etl/02-staging.md`
  - `docs/decisions.md`: driver entry.
  - `CLAUDE.md`: command and status line.
- **Gate.**
  - Counts are 3 teams, 12 users, 36 sprints and 36 capacity rows. Boards: 3.
  - A rerun gives identical contents. Compared on every column except `_loaded_at`, which changes by design.
  - A failed run leaves the old tables intact. Test: load, then run a load that fails partway (for example a corrupted page in a copy of `landing/`), and check the first load's rows are unchanged.
- **Commit message:**

```
feat(staging): add staging schema and loader for reference entities

Loads teams, users, boards, sprints and capacity with typed columns, primary keys and lineage columns.
```

### Step 2.1b: truncate and reload

Added after 2.1 was approved. Session 2 now has four commits: 2.1, 2.1b, 2.2 and 2.3.

- **Scope.** Replace drop-and-recreate on every run with create-if-missing, a definition check, then `TRUNCATE` and `COPY`, in one transaction. Add `--rebuild` (drop and recreate, no CASCADE) for schema changes. Postgres tests fail with a clear message when the server is unreachable, instead of erroring with a raw traceback.
- **Expected files.**
  - `ingestion/load_staging.py`
  - `tests/test_staging.py`
  - `docs/etl/02-staging.md`
  - `docs/decisions.md`: D-022.
  - `docs/etl-plan.md`
  - `CLAUDE.md`: command.
- **Gate.**
  - All 2.1 gates still pass: counts, identical rerun, failed run leaves the tables intact, mid-transaction rollback, rerun-comparison sabotage.
  - A view on a staging table survives a reload. `--rebuild` is refused while it exists and changes nothing.
  - A changed table definition stops the load with a message naming `--rebuild`. `--rebuild` restores it.
  - Postgres unreachable: the tests fail, never skip, with a message naming the host and port and how to start Postgres.
  - On the real `staging`, the tables created by 2.1 pass the definition check with unchanged content.
- **Commit message:**

```
refactor(staging): truncate and reload instead of drop and recreate

Keeps staging tables in place so dbt models that depend on them survive a reload. A rebuild flag handles schema changes.
```

### Step 2.2: issues, transitions, memberships

- **Scope.** Issues from the search pages. Status transitions and sprint memberships from the changelog pages.
- **Expected files.**
  - `ingestion/parse.py`
  - `ingestion/load_staging.py`
  - `tests/test_staging.py`
  - `docs/etl/02-staging.md`
- **Gate.**
  - Counts are 530 issues (30 Epics), 1,488 transitions, 320 memberships and 320 issues with a duedate.
  - Every Epic has null `story_points`.
  - Every parsed timestamp is timezone-aware UTC, for both formats.
- **Commit message:**

```
feat(staging): load issues, status transitions and sprint memberships

Derives transitions and memberships from changelog items and parses both timestamp formats to UTC.
```

### Step 2.3: reconciliation and sabotage

- **Scope.** An independent check of staging against `raw_data/`, plus sabotage tests that prove the checks can fail.
- **Expected files.**
  - `tests/test_staging_reconciliation.py`
  - `docs/etl/02-staging.md`: the "How it is verified" section.
- **Gate.**
  - Staging transitions and memberships equal `raw_data/status_transitions.json` and `raw_data/sprint_issue_memberships.json` row by row after UTC normalization. This is the independent check, not through the API serializer: the test reads `raw_data/` directly and imports nothing from `mock_api/`.
    - Join keys, because staging holds Jira IDs and `raw_data/` holds internal IDs:
      - issue by `key` (via `raw_data/issues.json`);
      - user by `display_name`;
      - sprint by team key plus sprint name.
    - Transitions are compared on issue key, timestamp, from status, to status and author.
    - Normalization: `raw_data/` timestamps are naive UTC with microseconds, and the API truncates them to milliseconds. So raw values are read as UTC and truncated to milliseconds before comparing.
  - Changelog-derived memberships equal the sprint issue lists (`sprint_issues/` pages) in both directions.
  - Added to the gate by this plan: sprint `start_at` and `end_at` equal `raw_data/sprints.json` dates at 09:00 UTC. Sprint dates are the only `+00:00` fields. Without this check, sabotage 1 would not reach any reconciled value, because transitions and memberships use `+0000`.
  - Sabotage 1: break the `+00:00` parsing and the reconciliation fails. Breaking it means reading the offset wrongly (for example as +01:00). Simply ignoring the offset would still pass on a UTC machine.
  - Sabotage 2: drop one landed page and the counts fail. In a copy of `landing/`, delete one changelog page and its manifest entry. Then the load succeeds and the counts are what fail.
  - Revert both sabotages afterwards. They run as monkeypatched tests, so nothing needs reverting in the code.
  - Tests that need Postgres use a separate test schema and never touch `staging`.
- **Additions** (requested when 2.3 was approved to start; not in the original plan):
  - **Every table with a raw counterpart.**
    - Issues are compared field by field: key, type, status, story points, due date, resolution date, parent, assignee and reporter. Team and created were added too.
    - Capacity, teams, users and sprints are compared as well. Boards are checked one per team, since `raw_data/` has none.
    - The comparison is always against `raw_data/` directly, never through the API serializer or the parser's helpers.
  - **An explicit timestamp rule.**
    - Measure the precision of `raw_data/`. If values differ only below the millisecond, state the rule and how many values it affects, in the test and in `docs/etl/02-staging.md`. Never loosen the comparison silently.
    - Result: `raw_data/` has microseconds, so 2,403 of 2,651 timestamps are affected. Raw values are truncated (floored) to milliseconds, and the test shows rounding would fail on 1,236 of them.
  - **Sabotage 1 covers the parsing that affects the data,** for both offset styles: `+00:00` breaks only sprints; `+0000` breaks issues, transitions and memberships.
  - **Last commit of the session.** The docs, the plan status and the CLAUDE.md updates go in it.
- **Commit message:**

```
test(staging): reconcile staging with raw_data and add sabotage checks

Compares staging row by row with raw_data and checks that changelog and sprint-list memberships agree.
```

---

## Session 3: dbt star schema

Branch: `feat/etl-session-3`. Detailed and confirmed at session start (2026-10-31). At most 4 commits. Stop for Yassine's review after step 3.2 and after step 3.4.

**Goal.** A dbt Core project on top of the `staging` schema: light `stg_` views, then a star schema of natural-keyed dimensions and facts that Session 4's metrics read from. Stage page: [`etl/03-dbt-star-schema.md`](etl/03-dbt-star-schema.md).

### Spec (applies to all four steps)

Decisions already made at session start. Each is recorded, with the options considered, in [`decisions.md`](decisions.md) (D-026 to D-029).

- **Location and tools.**
  - The dbt project lives in `dbt/` at the repo root: dbt Core with the Postgres adapter, added with uv.
  - `dbt/profiles.yml` reads every credential from `POSTGRES_*` with `env_var()`, so no secret is committed.
  - Command: `uv run --env-file .env dbt <cmd> --project-dir dbt --profiles-dir dbt`.
- **Two layers.**
  - `stg_` models are light views, one per staging table, with consistent names, in a schema called `stg`.
  - Dimensions and facts are tables in a schema called `marts`.
  - dbt uses exactly these schema names: `generate_schema_name` is overridden.
- **Star schema.**
  - Dimensions: `dim_team`, `dim_user`, `dim_sprint`, `dim_issue`.
  - Facts:
    - `fct_status_transitions`: one row per status change;
    - `fct_sprint_membership`: an issue joining a sprint, with `added_at`;
    - `fct_capacity`: grain team and sprint.
  - Natural keys, no surrogate keys. No date dimension in v1: the exact timestamps carry what the metrics need.
- **Users and teams.** The API exposes no team on users. Each user's team is derived from the teams of the issues assigned to them. A test checks that every user maps to exactly one team (the v1 limitation in [`domain-model.md`](domain-model.md)).
- **Reload safety.** Staging tables persist across reloads ([D-022](decisions.md#d-022-staging-reloads-by-truncate-and-copy-drop-and-recreate-only-on---rebuild)), so dbt views over them must survive `load_staging`. A test proves it.
- **Ignored files.** `dbt/target/`, `dbt/logs/` and `dbt/dbt_packages/` go in `.gitignore`.
- **Tests never touch the real schemas.** As in Session 2, pytest loads staging into `staging_test`. dbt then builds into `stg_test` and `marts_test`, through two vars (`staging_schema`, `schema_suffix`).

### Step 3.1: dbt project and staging sources

- **Scope.** dbt Core and the Postgres adapter, the profile, the schema name override, the 8 staging tables as sources, and one `stg_` view per table.
- **Expected files.**
  - `pyproject.toml`, `uv.lock`, `.gitignore`
  - `dbt/dbt_project.yml`, `dbt/profiles.yml`, `dbt/macros/generate_schema_name.sql`
  - `dbt/models/staging/_staging__sources.yml`, `dbt/models/staging/stg_*.sql` (8)
  - `tests/test_dbt.py`
  - `docs/etl-plan.md` (this section), `docs/etl/03-dbt-star-schema.md` (purpose, layers, how to run, decisions so far), `docs/decisions.md`, `docs/stack.md`, `CLAUDE.md`
- **Gate.**
  - `dbt debug` passes.
  - `dbt build` creates the 8 `stg_` views.
  - Row counts through the views: 3 teams, 12 users, 3 boards, 36 sprints, 36 capacity, 530 issues, 1,488 transitions, 320 memberships.
  - After running `load_staging` again, the views still work.
  - Sabotage: a wrong credential variable makes `dbt debug` fail with a clear error.
- **Commit message:**

```
chore(dbt): initialize project with staging sources

Adds dbt Core with the Postgres adapter, a profile that reads credentials from the environment, and sources plus light stg_ views over the 8 staging tables.
```

### Step 3.2: dimensions

- **Scope.** `dim_team`, `dim_user`, `dim_sprint` and `dim_issue` as tables in `marts`, with unique, not null and relationship tests. Users get their team from the issues assigned to them.
- **Gate.**
  - Counts are 3, 12, 36 and 530.
  - Every key is unique and not null, and the relationships hold.
  - Each user maps to exactly one team.
  - Sabotage: delete or duplicate a staging row, show the specific test fails, then reload staging.
- **Commit message:**

```
feat(dbt): add team, user, sprint and issue dimensions

Adds dim_team, dim_user, dim_sprint and dim_issue as tables in the marts schema, with unique, not null and relationship tests. Users get their team from the issues assigned to them.
```

### Step 3.3: facts

- **Scope.** `fct_status_transitions`, `fct_sprint_membership` and `fct_capacity`, with relationship tests and accepted-value tests on status names.
- **Gate.**
  - Counts are 1,488, 320 and 36.
  - Joining the transition fact to all four dimensions returns exactly 1,488 rows: no lost rows, no duplicates.
  - Sabotage: an orphan row (an issue ID that doesn't exist) makes the relationship test fail.
- **Decided at the 3.2 review:**
  - The transition's sprint is `event_sprint_id`: the sprint of the issue's team whose window contained the transition, half-open (`start_at <= transitioned_at < end_at`), with a test that every transition matches exactly one window ([D-030](decisions.md#d-030-a-transitions-sprint-is-the-window-it-happened-in-event_sprint_id)).
  - Before building it, the 216 transitions where it differs from the membership sprint were broken down in [`etl/03-dbt-star-schema.md`](etl/03-dbt-star-schema.md). All are after the membership sprint ended.
- **Commit message:**

```
feat(dbt): add status transition fact and capacity model

Adds fct_status_transitions, fct_sprint_membership and fct_capacity with relationship and accepted-value tests on status names.
```

### Step 3.4: data quality tests and final docs

- **Scope.**
  - Row-count tests, a non-Epic story point test and a one-team-per-user test.
  - A check that sprint `complete_at` matches `raw_data/`. It is carried over from Session 2: `complete_at` in `staging.sprints` must match `raw_data/sprints.json`, with the same independent approach and timestamp rule as the existing reconciliation ([D-024](decisions.md#d-024-reconciliation-truncates-raw-timestamps-to-milliseconds)).
    - Found at step 3.1: `raw_data/sprints.json` has no completion field (`sprint_id`, `team_id`, `name`, `state`, `start_date`, `end_date` only). The mock API sets `completeDate` = `endDate` for closed sprints (`mock_api/serializers.py`, `_sprint_dates`). So the only raw counterpart is: closed means `end_date` at 09:00 UTC, otherwise null. Confirmed at the 3.2 review, with two additions: compare each sprint's state too, and document that `complete_at` is derived and used by no metric ([D-032](decisions.md#d-032-sprint-complete_at-is-reconciled-against-a-derived-value)).
  - Finalise [`etl/03-dbt-star-schema.md`](etl/03-dbt-star-schema.md): a Mermaid schema diagram, the grain of each table, the test list and the known limits.
- **Gate.**
  - `dbt test` passes in full.
  - At least three sabotage cases, each failing the right test: a null key, a duplicate key and a changed `complete_at` value.
  - The full pytest suite and the hook tests pass.
- **Commit message:**

```
test(dbt): add relationship and data quality tests

Adds row-count, non-Epic story point and one-team-per-user tests, plus a check that sprint complete_at matches raw_data.
```

**Final report after 3.4:** a summary table of the four commits, the final counts in `marts`, and what Session 4 (metrics) can rely on.

## Session 4: metrics and business questions (outline, to be detailed before the session starts)

Branch: `feat/etl-session-4`. Commit messages below are *provisional, finalized at session start*.

Metrics are SQL models in dbt, not Python.

- **4.1:** committed, scope added and completed per sprint, from exact timestamps (never calendar days).
  - Must match `simulator/sprint_report.py` exactly for all 36 sprints, checked by a small pytest.
  - This is the answer key and is never cut. A mismatch is a bug in one of the two, never a rounding detail.
  - Checked during planning: in the shipped data, no `added_at` or `resolutiondate` falls within 1 ms after a sprint boundary. So the API's millisecond truncation cannot flip any committed/scope-added or completed comparison.
  - `feat(metrics): add sprint metrics reconciled with sprint_report`
- **4.2 and 4.3:** the six business questions:
  1. velocity per sprint;
  2. velocity vs. capacity;
  3. deadline breach rate by issue type;
  4. time per status;
  5. bug-to-story ratio;
  6. cross-team comparison.

  Epics are excluded from time-based metrics.
  - `feat(metrics): add business question models 1 to 3`
  - `feat(metrics): add business question models 4 to 6`

Decisions already made:

- **Breach headline.** The breach rate headline counts resolved issues only, matching the answer key: Story 20.4%, Task 28.9%, Bug 38.8%. A separate "open and overdue" count is measured at the last data timestamp, 2024-07-05 15:35:28. Breach is split by committed vs. scope-added ([`domain-model.md`](domain-model.md), "Deadline (duedate) rule").
- **Bug-to-story ratio.** It counts all created issues, because 180 non-Epic issues never join a sprint.

## Session 5: pipeline, tests, CI, docs (outline, to be detailed before the session starts)

Branch: `feat/etl-session-5`. Commit messages below are *provisional, finalized at session start*.

- **5.1:** one command runs generate → serve → extract → load staging → dbt build, from an empty database.
  - `feat(pipeline): add one-command pipeline run`
- **5.2:** pytest wrappers around `mock_api.verify`, `mock_api.verify_app` and `scripts/check_warehouse.py`.
  - `test(pipeline): add pytest wrappers for verify scripts`
- **5.3:** GitHub Actions with a Postgres service container. First to cut if behind schedule.
  - `ci: add GitHub Actions workflow`
- **5.4:** finalize `docs/etl/` pages, README, and the clean-run guide.
  - `docs: finalize pipeline documentation and clean-run guide`
