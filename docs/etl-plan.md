# ETL commit plan

This plan lists every commit for ETL Sessions 2 to 5. Each step gives its ID, scope, expected files, the gate that must pass before the commit, and the exact commit message. How a session runs (branches, gates, failure handling, review) is in [`workflow.md`](workflow.md).

Rules for this file:

- **Messages are verbatim.** A step's commit message is used exactly as written here. If it no longer fits the change, stop and ask. Do not edit it on the fly.
- **Confirmed steps.** Session 3 was detailed and confirmed on 2026-10-31, Session 4 on 2026-11-01, Session 5 on 2026-11-06. No session starts before its steps are confirmed.
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
| 4.1 | 4 | `feat(metrics): add sprint metrics reconciled with sprint_report` | done |
| 4.2 | 4 | `feat(metrics): add business question models 1 to 3` | done |
| 4.3 | 4 | `feat(metrics): add business question models 4 to 6` | done |
| 5.1 | 5 | `feat(pipeline): add one-command pipeline run with completeness checks` | done |
| 5.2 | 5 | `test(pipeline): add pytest wrappers for the mock API verify scripts` | done |
| 5.3 | 5 | `feat(metrics): flag rollup rows in breach rate and time in status` | proposed |
| 5.4 | 5 | `ci: add GitHub Actions workflow with a Postgres service container` | proposed, first to cut |
| 5.5 | 5 | `docs: finalize pipeline documentation and clean-run guide` | proposed |

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

## Session 4: metrics and business questions

Branch: `feat/etl-session-4`. Detailed and confirmed at session start (2026-11-01). At most 4 commits. Stop for Yassine's review after step 4.1 and after step 4.3. Stage page: [`etl/04-metrics.md`](etl/04-metrics.md); one page per business question in [`metrics/`](metrics/).

### Spec (applies to all three steps)

Decisions already made at session start. Each is recorded, with the options considered, in [`decisions.md`](decisions.md) (D-033 onwards).

- **Where.** Metrics are SQL models in dbt, in `dbt/models/metrics/`, built as tables in schema `marts`. They follow the naming and test conventions already in `dbt/` ([D-033](decisions.md#d-033-metrics-are-dbt-sql-models-built-as-tables-in-marts)).
- **Exact timestamps, never calendar days.** Committed is `added_at <= dim_sprint.start_at`, scope added is `added_at > start_at`, completed is `dim_issue.resolved_at <= dim_sprint.end_at`. Which issues belong to a sprint comes from `fct_sprint_membership`. `complete_at` is not used.
- **The answer key.** The warehouse must match `simulator/sprint_report.py` exactly for all 36 sprints. A mismatch is a bug in one side, never a rounding detail. `sprint_report.py` is never edited to make a check pass.
- **Millisecond floor.** Warehouse timestamps are floored to milliseconds ([D-024](decisions.md#d-024-reconciliation-truncates-raw-timestamps-to-milliseconds)). A check shows that no relevant raw timestamp falls within the first millisecond after a sprint boundary, where flooring could flip a comparison. The result is documented.
- **Breach rate.** The headline counts resolved issues only, matching the answer key: Story 20.4%, Task 28.9%, Bug 38.8%. A separate "open and overdue" count is measured at the last data timestamp, derived from the data and never hardcoded. The deadline rule is the one in [`domain-model.md`](domain-model.md).
- **Bug-to-story ratio** counts all created issues, because 180 non-Epic issues never join a sprint.
- **Time-based metrics** exclude Epics. Durations are in calendar hours (weekends included), and that limit is documented.
- **v1 limits in the metric docs:** no carryover (66 issues finish after their own sprint ends, which explains part of the breach rate and makes velocity read lower than real throughput), one team per user, scope creep not scaled to team size.
- **Tidy outputs for the dashboard.** Every business-question model has a documented grain, carries the team name and the sprint name or number, and needs no further join in the BI tool. Every model and column has a description in its dbt `schema.yml`.
- **Independent checks.** Expected values are recomputed in plain Python from `raw_data/`, never through the pipeline's code or the mock API. Every step has at least one sabotage check. Postgres tests build into `marts_test`, as in Session 3.

### Step 4.1: sprint metrics reconciled with `sprint_report`

- **Scope.** A per-sprint model with committed, scope added and completed story points from exact timestamps, plus every other value the answer key reports, and a pytest that reconciles all 36 sprints with `sprint_report.py`.
- **Expected files.**
  - `dbt/dbt_project.yml`, `dbt/models/metrics/rpt_sprint_metrics.sql`, `dbt/models/metrics/_metrics__sprint.yml`, two singular tests in `dbt/tests/`
  - `tests/test_metrics.py`, `tests/test_dbt.py` (`run_dbt` takes a project directory, for sabotage copies)
  - `docs/etl/04-metrics.md`, `docs/etl/README.md`, `docs/etl-plan.md` (this section), `docs/decisions.md`, `CLAUDE.md`
- **Gate.**
  - Committed, scope added and completed match `sprint_report.py` for all 36 sprints, on every value it reports, including `hit` and the bug and issue counts.
  - The reconciliation reads the expected values by calling the answer key on `raw_data/`, and the warehouse values from the marts layer.
  - Sabotage: calendar days instead of exact timestamps (the original off-by-one-day bug) fail the reconciliation on at least one sprint.
  - The sub-millisecond boundary check passes, with its own sabotage.
- **Commit message:**

```
feat(metrics): add sprint metrics reconciled with sprint_report

Adds a per-sprint model with committed, scope added and completed story points from exact timestamps, plus a pytest that reconciles all 36 sprints with sprint_report.py.
```

### Step 4.2: business questions 1 to 3

- **Scope.**
  - Q1: velocity per team and sprint, with a 3-sprint rolling average.
  - Q2: velocity against available capacity, using the capacity definitions in the docs. If they are ambiguous, stop and ask.
  - Q3: breach rate by issue type, and by team, plus the open-and-overdue count.
- **Decided at the 4.1 review:**
  - Q2 uses available person-days = headcount × 10 − pto_days, after a read-only check that the simulator turns headcount and PTO into capacity the same way ([D-037](decisions.md#d-037-available-capacity-is-headcount--10-working-days--pto-days)).
  - Q2 outputs, per team and sprint: headcount, PTO days, available person-days, completed points, points per available person-day and the 3-sprint rolling figure. A test checks PTO never exceeds headcount × 10. The Q2 page relates the result to each team's tuned realization factor.
  - The Q1 page states the points that count in no sprint (343 points, 65 issues) as a share of all completed points.
  - The hardcoded as-of time in D-020 is replaced by one derived from the data ([D-036](decisions.md#d-036-the-as-of-instant-is-derived-from-the-data)).
- **Expected files.**
  - `dbt/models/metrics/`: `rpt_data_as_of`, `rpt_q1_velocity`, `rpt_q2_velocity_vs_capacity`, `rpt_q3_deadline_outcomes`, `rpt_q3_breach_rate`, `_metrics__questions_1_3.yml`; `dbt/dbt_project.yml` (var `sprint_working_days`); two singular tests in `dbt/tests/`
  - `tests/test_metrics.py`
  - `docs/metrics/q1-velocity.md`, `q2-velocity-vs-capacity.md`, `q3-breach-rate.md`; `docs/etl/04-metrics.md`, `docs/decisions.md` (D-020 amended, D-036 to D-039), this file, `CLAUDE.md`
- **Gate.**
  - Q3 reproduces 20.4%, 28.9% and 38.8% on resolved issues.
  - All three questions are recomputed independently in plain Python from `raw_data/` and compared.
  - Sabotage per question, for example a flipped breach comparison failing the rates and the test.
- **Docs.** One short page per question in `docs/metrics/` (`q1-velocity.md`, `q2-velocity-vs-capacity.md`, `q3-breach-rate.md`): the question in plain words, the formula and data used, the assumptions, how it was verified, and an example result.
- **Commit message:**

```
feat(metrics): add business question models 1 to 3

Adds velocity per sprint, velocity against capacity and deadline breach rate by issue type, each with an independent check against raw_data.
```

### Step 4.3: business questions 4 to 6

- **Scope.**
  - Q4: time per status. Each stay in a status lasts from the transition into it (or creation, for the first status) to the next transition. A bounce back to an earlier status is another visit. Epics are excluded. The treatment of the still-open final stay of unresolved issues is stated and justified. Mean, median and 90th percentile by status and team.
  - Q5: bug-to-story ratio by team over time, at a time grain chosen from the data's span and justified.
  - Q6: one row per team combining velocity, capacity use, breach rate, time per status, bug-to-story ratio and the sprint hit rate, normalized fairly (per available person-day where applicable), with the normalization limits documented.
- **Decided at the 4.1 review:** record the calendar-hours and Epic-exclusion rules when Q4 is built ([D-040](decisions.md#d-040-time-based-metrics-use-calendar-hours-exclude-epics-and-keep-open-stays-out-of-the-statistics)).
- **Expected files.**
  - `dbt/models/metrics/`: `rpt_q4_status_stays`, `rpt_q4_time_in_status`, `rpt_q5_bug_to_story_ratio`, `rpt_q6_team_portfolio`, `_metrics__questions_4_6.yml`; two singular tests in `dbt/tests/`
  - `tests/test_metrics.py` (the millisecond check also covers issue creation times, which Q5 compares with sprint windows)
  - `tests/test_dbt.py` and `docs/etl/03-dbt-star-schema.md`: three Session 3 tests whose staging edits the new metric tests also catch (the gate's first run, recorded in [`etl/04-metrics.md`](etl/04-metrics.md))
  - `docs/metrics/q4-time-per-status.md`, `q5-bug-to-story.md`, `q6-portfolio.md`, `README.md`; `docs/metrics/q1-velocity.md` (link to Q6); `docs/etl/04-metrics.md`, `docs/etl/README.md`, `docs/decisions.md` (D-040 to D-042), this file, `CLAUDE.md`
- **Gate.**
  - Q4 and Q5 are recomputed independently from `raw_data/` and match.
  - Q6 equals the corresponding values from Q1 to Q5, team by team.
  - Sabotage per question, as in 4.2.
- **Docs.** `docs/metrics/q4-time-per-status.md`, `q5-bug-to-story.md`, `q6-portfolio.md` in the same format, plus `docs/metrics/README.md` as an index.
- **Commit message:**

```
feat(metrics): add business question models 4 to 6

Adds time per status, bug-to-story ratio and the cross-team portfolio comparison, each with an independent check against raw_data.
```

**Spare (4th) commit:** only if a gate needs a follow-up fix, with a Conventional Commits message and the reason given in the report.

**Final report after 4.3:** a table of the commits; the key results (the 36-sprint reconciliation, the three breach rates, a one-line headline per question); what the dashboard layer can rely on (model names, grains, columns); every assumption made.

## Session 5: pipeline, tests, CI, docs

Branch: `feat/etl-session-5`. Detailed on 2026-11-03, confirmed on 2026-11-06. Stage page: [`etl/05-pipeline.md`](etl/05-pipeline.md).

**Goal.** One command takes an empty database to the built and tested `rpt_` tables, checks after each stage that nothing was lost between stages, and is documented well enough that a fresh clone reproduces every documented count. The test suite also covers the mock API's two verification scripts, and CI runs the suite on every push.

**Paths marked (new)** do not exist yet and are created by the step that names them. Every other file path and command below exists on `main` (checked on 2026-11-03). Names of scratch directories, databases and volumes (`.landing.partial`, `aap_pipeline_test`, `marts_next`, `aap-cleanrun_pgdata`) are explained where they appear.

### Current state (checked on 2026-11-03, before the session)

- **Running the pipeline takes four manual steps** ([`README.md`](../README.md#quick-start), steps 4 and 5): start `uvicorn` in a second terminal, then `python -m ingestion.extract`, `python -m ingestion.load_staging` and `dbt build`. Nothing checks that the stages agree with each other; only the tests do, on their own schemas.
- **Exit codes today.** `ingestion.extract` and `ingestion.load_staging` exit 1 on their own errors, `dbt build` exits non-zero on any failure, and `mock_api.verify` and `mock_api.verify_app` return 1 from `main` on a failed check ([`etl/01-extraction.md`](etl/01-extraction.md#how-to-run), [`etl/02-staging.md`](etl/02-staging.md#how-to-run)).
- **What a failure leaves behind today.**
  - Extraction writes into `.landing.partial` and swaps it in only on success, so a failed run keeps the previous `landing/` ([`etl/01-extraction.md`](etl/01-extraction.md#schema-or-contract), "Replacement").
  - Staging loads in one transaction, so a failed load keeps the previous rows ([D-022](decisions.md#d-022-staging-reloads-by-truncate-and-copy-drop-and-recreate-only-on---rebuild)).
  - dbt rebuilds model by model, so a failed `dbt build` can leave `marts` with some tables rebuilt and others from the previous run (see D-046 below).
- **Ten `rpt_` tables, not six.** The six business questions are answered by ten models in `marts`: `rpt_sprint_metrics`, `rpt_data_as_of` and eight `rpt_q*` models ([`etl/04-metrics.md`](etl/04-metrics.md#business-question-models)). "The `rpt_` tables" below means all ten.
- **Totals rows are mixed with detail rows in two tables, with no column to tell them apart.**
  - `rpt_q3_breach_rate` (48 rows): 18 detail rows and 30 rollup rows labelled "All teams", "All types" or "All" (`group by cube`, [D-039](decisions.md#d-039-breach-rates-ship-as-a-table-with-labelled-rollups-plus-an-issue-level-table)).
  - `rpt_q4_time_in_status` (16 rows): 12 team rows and 4 "All teams" rows (`grouping sets`).
  - The other eight have no totals rows. Q5's period 0, "Before Sprint 1", is a real period, not a total.
  - A BI tool that sums a column by default (Power BI does for numeric columns) double-counts unless the report filters on the labels. Step 5.3 adds the column.
- **The test suite takes about 8 minutes, says the README** ([`README.md`](../README.md#quick-start)). Measured at 6 minutes on 2026-11-03 (D-045).
- **No CI.** There is no `.github/` directory.
- **`docs/etl/05-pipeline.md` is a stub** with empty headings.

### Decisions (D-043 to D-047)

Proposed here and approved on 2026-11-06. Each is recorded in [`decisions.md`](decisions.md) by the step named in brackets.

#### D-043: the pipeline starts the mock API as a subprocess and always stops it (step 5.1)

- **Question.** How does the one-command run start the mock API, and how does it clean up on failure?
- **Options.**
  - **A. `uvicorn` as a subprocess** on the host and port of `AAP_API_BASE_URL` (recommended). The extraction then goes through real HTTP and the same settings a manual run uses ([D-025](decisions.md#d-025-extraction-settings-come-from-the-environment-only-supplied-by---env-file-env)). It costs a readiness wait and process cleanup.
  - **B. In process, with FastAPI's `TestClient`** injected into `JiraClient`, as the tests do. No port and no process. But the production run would then be the test fixture, and it would never prove that the client works over a socket (timeouts, retries, the base URL).
  - **C. `uvicorn.Server` in a background thread.** Real HTTP with no second process, but the server shares the runner's process, and stopping it cleanly from another thread is harder to get right than terminating a child process.
  - **D. Keep starting it by hand.** Not one command.
- **Recommendation: A**, with these rules:
  - **Refuse a busy port.** If something already listens on the API port, the run stops before writing anything (exit 2). It never extracts from a server it did not start, which could be serving different data.
  - **Readiness.** Poll an authenticated `GET /rest/agile/1.0/board?maxResults=1` until it answers 200, for at most 15 seconds. If the process exits first or the time runs out, stop with exit 1 and say so.
  - **Cleanup.** The server runs inside a context manager. On leaving it, for any reason including an exception or Ctrl-C, the runner sends SIGTERM, waits up to 10 seconds, then kills. The server is stopped as soon as extraction ends, so it is down during the load and the dbt build.
  - **`raw_data/` is a precondition, not a stage.** The run checks it exists and, if not, stops with exit 2 and the command that creates it, `uv run python scripts/generate_raw_data.py`. It never generates it: the simulator plays the source system, and a pipeline that rebuilds its own source would hide a missing or changed source. This changes the old outline, which listed "generate" as the first stage.

#### D-044: the completeness check is its own module and compares counts between adjacent stages (step 5.1)

- **Question.** Where does the runtime completeness check live, and what exactly does it compare?
- **Options for where.**
  - **A. A separate module, `pipeline/completeness.py` (new)**, called by the runner after each stage (recommended). It reads landing files and the database with its own code, so it cannot share a bug with the code it checks.
  - **B. dbt tests.** They cannot see `landing/`, and the existing `expected_row_count` tests compare with fixed shipped-dataset numbers, not with the previous stage.
  - **C. Inside each stage** (extraction or the loader returning and asserting counts). The check would then use the same parsing as the stage it checks.
  - **D. pytest.** Tests run on `staging_test` and `marts_test`, not on the production run.
- **What it compares.** Only counts between stages, never fixed numbers: the fixed SEED=22 numbers belong to the tests and the acceptance step, and a different seed must still pass this check. On a mismatch it raises one error naming the stage, the measure, both counts and where each came from, for example `Completeness check failed after staging: status_transitions has 1,487 rows, landing has 1,488 status changelog items`.
  - **After extraction** (`landing/` only):
    - Every manifest entry has its file, and every file under `landing/` except `manifest.json` is in the manifest (578 entries, 579 files today).
    - No `.landing.partial` or `.landing.previous` is left.
    - One changelog folder per landed issue, and one `sprint_issues` folder per landed sprint.
    - Landed record counts, read from the raw JSON: boards, sprints, issues, capacity rows, status changelog items, Sprint changelog items, sprint-issue entries.
  - **After the staging load:**
    - `boards`, `sprints`, `issues` and `capacity` rows equal the landed counts.
    - `status_transitions` equals the status changelog items.
    - `sprint_memberships` equals both the Sprint changelog items and the sprint-issue entries. This equality holds because v1 has no carryover: an issue never leaves a sprint ([`domain-model.md`](domain-model.md)). A v1.1 with carryover changes this rule.
    - `teams` equals the distinct project keys of the boards. `users` equals the distinct account IDs among assignees, reporters and changelog authors.
  - **After `dbt build`:**
    - Dimensions and facts equal their staging tables: `dim_team`, `dim_user`, `dim_sprint`, `dim_issue`, `fct_status_transitions`, `fct_sprint_membership`, `fct_capacity`.
    - `rpt_sprint_metrics`, `rpt_q1_velocity` and `rpt_q2_velocity_vs_capacity` have one row per `dim_sprint` row; `rpt_q6_team_portfolio` one per `dim_team` row; `rpt_data_as_of` exactly one.
    - `rpt_q3_deadline_outcomes` has one row per `dim_issue` row with a due date, and the grand-total row of `rpt_q3_breach_rate` counts the same issues.
    - `rpt_q4_status_stays` = transitions + non-Epic issues (each issue's first stay starts at creation).
    - `rpt_q5_bug_to_story_ratio` = sprints + teams (each team's sprints plus its "Before Sprint 1" period).
    - `rpt_q4_time_in_status` is not empty.
    - Checked on the current `marts` (2026-11-03): 1,988 = 1,488 + 500, 39 = 36 + 3, and 320 due issues in both Q3 tables.
- **Recommendation: A**, with the comparisons above. It complements the tests and does not replace them: it proves a given run lost nothing, while the tests prove the values are right.

#### D-045: the test suite's runtime, and a fast subset (step 5.2)

- **Question.** Why does the full suite take minutes, and does CI need a fast subset?
- **Measured on 2026-11-03** (`uv run --env-file .env python -m pytest -rs --durations=30`, this machine, Postgres already up):
  - **103 passed, 0 skipped, in 359 s (5 min 58 s).** The README's "about 8 minutes" was measured on another run; the step that touches the README re-measures it.
  - **The 30 slowest tests take 312 s of the 359**, and all of them are in `tests/test_dbt.py` (14 tests, 178 s) and `tests/test_metrics.py` (16 tests, 134 s). The slowest is `test_sabotage_a_deleted_staging_user_fails_the_user_relationships` (22.8 s); the other sabotage tests in `tests/test_dbt.py` take 14 to 15 s each, the reconciliation tests in `tests/test_metrics.py` about 10 s each.
  - **The 56 tests marked `postgres` take about 345 s; the other 47 take 14 s**, with or without the `POSTGRES_*` variables (`pytest -m "not postgres"`, 0 skipped).
- **Why it is slow.** Every Postgres test starts from scratch: the `warehouse` fixture in `tests/test_dbt.py` is function-scoped, so each test drops the test schemas, reloads `staging_test`, and runs dbt as a subprocess on the whole project (`dbt build`, or `dbt run` then `dbt test` for sabotage cases, `run_then_test`). About 30 tests each pay for a full dbt start-up and build (5 to 15 s), and roughly a dozen of them only read the same freshly built tables.
- **Options.**
  - **A. A session-scoped, built `marts_test`** for tests that only read it (the dimension and fact counts, the 36-sprint reconciliation, the Q1 to Q6 comparisons with `raw_data/`). One build instead of about a dozen. Sabotage tests keep their own fixture, because they change staging or the dbt project. Which tests are read-only is checked one by one at step start.
  - **B. `dbt build --select`** only the models a sabotage needs. Faster sabotage runs, but each test then depends on knowing the lineage, and a too-narrow selector could stop a sabotage from reaching the test that should catch it.
  - **C. A new `slow` marker** for a fast subset. Not needed: `-m "not postgres"` already is one (47 tests, 14 s, no database), it just isn't documented.
  - **D. Parallel tests** (`pytest-xdist`). A new dependency, and the Postgres tests share the schema names `staging_test`, `stg_test`, `marts_test`, so they would need one set of schemas per worker.
- **Recommendation.**
  - **Fix: A**, in step 5.2, with the runtime before and after recorded. Expected saving: about ten builds of about 10 s, so between 1.5 and 2 minutes. Measured at the step: about 93 s ([D-045](decisions.md#d-045-tests-that-only-read-share-one-warehouse-built-once)).
  - **Fast subset:** document `-m "not postgres"` as the quick local check. It is not a gate. It grew to 56 tests and about 25 s with Session 5's tests.
  - **CI runs the full suite**, not a subset. The reconciliation with `simulator/sprint_report.py` is a Postgres test and is never cut, so a subset that skips it would make CI prove less than the README claims. A run of about 6 minutes per push is acceptable for one contributor.

#### D-046: exit codes, and what a failed run leaves behind (step 5.1)

- **Question.** What does the runner return, and what state is left in `landing/`, `staging` and `marts` when a stage fails?
- **Options for exit codes.**
  - **A. 0 or 1 for everything.** Simple, but a script or CI log cannot tell "Postgres is down" from "a row went missing".
  - **B. One code per stage** (10 extraction, 20 staging, ...). Precise, but it duplicates what the error message already says, and every new stage needs a number.
  - **C. One code per kind of failure** (recommended):
    - `0`: every stage and every completeness check passed;
    - `1`: a stage failed (mock API did not start, extraction error, staging error, `dbt build` failure);
    - `2`: a precondition failed before anything was written (a missing variable, `raw_data/` missing, the API port busy, Postgres unreachable);
    - `3`: a completeness check failed;
    - `130`: interrupted with Ctrl-C (the shell convention), after the same cleanup.
- **Options for what is left behind.**
  - **A. Leave each stage's own guarantee in place, and say exactly what is stale** (recommended).
  - **B. Roll back every stage on any failure.** It would need a copy of `landing/`, `staging` and `marts` before each run, and `marts` cannot be rolled back across dbt's per-model transactions without building into a shadow schema and swapping it.
  - **C. A shadow schema swap for `marts`** (build into `marts_next`, swap on success). Atomic for the dashboard, but it duplicates the warehouse and changes the dbt schema contract ([D-027](decisions.md#d-027-two-dbt-layers-with-exact-schema-names-stg-views-and-marts-tables)). A v1.1 candidate, not v1.
- **Recommendation: C for codes, A for state.** The runner prints, on failure, which stages finished and what is now stale:

  | Failed at | `landing/` | `staging` | `marts` | Exit |
  |---|---|---|---|---|
  | preflight | untouched | untouched | untouched | 2 |
  | mock API start or extraction | previous run's, unchanged (scratch removed) | untouched | untouched | 1 |
  | staging load | this run's | previous rows, rolled back | untouched (now behind `landing/`) | 1 |
  | `dbt build`, a model fails | this run's | this run's | mixed: the failed model and everything downstream keep their previous tables (absent on an empty database); every other model is rebuilt, including those that run after the failure | 1 |
  | `dbt build`, a data test fails | this run's | this run's | mixed: the tested model is already rebuilt with this run's rows; everything downstream of it keeps its previous table | 1 |
  | a completeness check | as written by the stages that passed | as written | as written | 3 |

  After a completeness failure nothing is rolled back: the data that failed the check is what is needed to investigate it. The mock API is stopped in every row. The two `dbt build` rows were checked at step 5.1 on real failed builds, comparing table oids. The assumption that a failed model keeps its previous table held. The original single row was incomplete: it missed that unrelated models still rebuild, and that a failed data test leaves the tested model already rebuilt ([D-046](decisions.md#d-046-exit-codes-by-kind-of-failure-and-each-stage-keeps-its-own-guarantee)).

#### D-047: rollup rows carry an `is_rollup` flag (step 5.3)

- **Question.** How does a BI tool tell totals rows from detail rows in `rpt_q3_breach_rate` and `rpt_q4_time_in_status`?
- **Options.**
  - **A. Keep the labels only** and document the filter. A dashboard author who forgets it gets doubled sums, silently.
  - **B. Split each table** into a detail model and a totals model. Removing rows from an existing model is a breaking change to the dashboard contract ([D-033](decisions.md#d-033-metrics-are-dbt-sql-models-built-as-tables-in-marts)), and Q3's seven rollup levels would need several models.
  - **C. A text `row_level` column** naming the grouping (for example "team × type", "all teams × type"). Precise for Q3's eight grouping sets, but a filter on free text is easier to get wrong than on a boolean.
  - **D. A boolean `is_rollup` column** (recommended), `true` on every row where at least one dimension is a rollup label. Adding a column breaks nothing. The labels stay for display.
- **Recommendation: D**, with a test that `is_rollup` is true exactly when a dimension holds a rollup label, and the counts 18/30 (Q3) and 12/4 (Q4).

### Spec (applies to all steps)

- **Where the code lives.** A new top-level package `pipeline/` (new), run as `uv run --env-file .env python -m pipeline.run` (new). It calls the existing stage functions (`ingestion.extract.run_extraction`, `ingestion.load_staging.load_staging`) in process, and `dbt build` as a subprocess, the way `tests/test_dbt.py` runs dbt. It is orchestration code, not ingestion, so it gets its own package. The pre-commit docs check (`.githooks/pre-commit`) covers `ingestion/`, `warehouse/` and `dbt/` only, so step 5.1 adds `pipeline/` to it, with hook test cases.
- **No orchestrator.** One command, run in order, as [D-006](decisions.md#d-006-orchestration-and-the-dbt-vs-plain-sql-write-up-are-deferred) decided.
- **Settings.** The environment only, as every stage already does: `AAP_API_BASE_URL`, `AAP_API_TOKEN` and `POSTGRES_*`, supplied by `--env-file .env`. No new variable.
- **Real schemas, test database.** The production run writes `landing/`, `staging`, `stg` and `marts`. Its tests never touch them: they create a throwaway database on the same server (`aap_pipeline_test`), point `POSTGRES_DB` at it, run there, and drop it afterwards. This gives a truly empty database with the real schema names. The `POSTGRES_USER` role is a superuser in the official image, so it can create databases (checked: `rolsuper` and `rolcreatedb` are true for it).
- **Counts.** The targets are the shipped dataset's ([`CLAUDE.md`](../CLAUDE.md#shipped-dataset-seed22-verification-targets)). The `rpt_` row counts are those of [`etl/04-metrics.md`](etl/04-metrics.md#business-question-models): `rpt_sprint_metrics` 36, then in that page's table order 1, 36, 36, 320, 48, 1,988, 16, 39, 3.
- **Gates need 0 skipped tests** (`pytest -rs`), as in every session.
- **Commit count.** [`workflow.md`](workflow.md#one-session-one-branch) allows at most 4 commits per session. This plan has 5 when CI is kept. **Yassine confirms an exception for Session 5 at session start, or CI (5.4) is cut**, which brings it back to 4. The other steps stay separate: each is a different kind of change, with its own gate and its own review.

### Step 5.1: one-command pipeline with completeness checks

- **Goal.** `uv run --env-file .env python -m pipeline.run` goes from an empty database (and no `landing/`) to the ten `rpt_` tables built and tested, with the completeness checks of D-044 and the exit codes and cleanup of D-043 and D-046.
- **Scope.**
  - Preflight: variables, `raw_data/`, a free API port, a Postgres connection. Nothing is written before it passes.
  - The mock API context manager (D-043), extraction, check, stop the API, staging load, check, `dbt build`, check.
  - A summary at the end: counts per stage and the time each stage took.
  - Optional `--landing-dir` and `--dbt-project-dir` arguments (defaults `landing/` and `dbt/`), so tests never overwrite the real landing and can run a sabotaged copy of the dbt project.
- **Expected files.**
  - `pipeline/__init__.py`, `pipeline/run.py`, `pipeline/mock_api_server.py`, `pipeline/completeness.py` (all new)
  - `tests/test_pipeline.py` (new)
  - `.githooks/pre-commit`, `scripts/test_hooks.sh`: `pipeline/` joins the docs check, with one reject and one accept case
  - `docs/etl/05-pipeline.md` (purpose, inputs and outputs, contract, how to run), `docs/etl/README.md` (stage 5 "in progress"), `docs/decisions.md` (D-043, D-044, D-046), `docs/workflow.md` (the docs check paths), this file, `CLAUDE.md` (pipeline command, layout line)
- **Gate.**
  - `tests/test_pipeline.py`, on the throwaway database: one run from empty exits 0, and every staging, dimension, fact and `rpt_` count equals the shipped targets. A second run exits 0 with identical counts.
  - The real run: `uv run --env-file .env python -m pipeline.run` exits 0, prints the summary, and `dbt build` inside it ends `PASS=251 WARN=0 ERROR=0` (today's figure, [`README.md`](../README.md#quick-start)).
  - The mock API is gone after every run, passed or failed: its port is free again (checked in the tests).
  - `sh scripts/test_hooks.sh` passes with the two new cases (30 expected).
  - The full suite passes, 0 skipped.
- **Sabotage.**
  - **Completeness after extraction:** in a copy of the landing, delete one changelog page but keep its manifest entry. The check fails and names the file.
  - **Completeness after staging and after dbt:** delete one row from `staging.status_transitions` in the test database, and separately one row from `marts.fct_sprint_membership`. Each check fails, names the table and gives both counts.
  - **Exit codes:** a busy API port exits 2 and writes no `landing/`. A broken model in a copy of `dbt/` exits 1, and the mock API is not left running.
  - **dbt state after a failure** (backs the D-046 table): with a model broken in a copy of `dbt/`, the failing model's table in the test database still holds the previous run's rows.
- **Docs in the same commit.** `docs/etl/05-pipeline.md` first sections; `docs/etl/README.md`; `docs/workflow.md` (hook paths); `CLAUDE.md` (command).
- **Decisions to record.** D-043, D-044, D-046.
- **Commit message:**

```
feat(pipeline): add one-command pipeline run with completeness checks

Runs extraction, staging load and dbt build from an empty database, starting and stopping the mock API itself. Compares counts across landing, staging and marts after each stage and fails loudly on any gap.
```

### Step 5.2: pytest wrappers for the verify scripts, and a faster suite

- **Goal.** `pytest` alone covers everything that verifies the project: the mock API's two scripts join the suite. The suite gets faster by building the test warehouse once for the tests that only read it, and the existing fast subset is documented (D-045).
- **Scope.**
  - `tests/test_mock_api_verify.py` (new): calls `mock_api.verify.main()` and `mock_api.verify_app.main()` on `raw_data/`, asserts each returns 0 and prints its final `ALL ... PASSED` line.
  - D-045's fix: a session-scoped fixture that builds the warehouse once, in its own read-only database, for the tests that purely read. A shared `marts_test` in the main database would be dropped by the first `warehouse` test. Tests that change data keep their own schemas. The build's pass/fail assertions move into the fixture; the others don't change.
  - A `--reverse` option, to run the suite backwards.
  - D-045's fast subset, `pytest -m "not postgres"`, documented. No new marker.
  - Dropped from the old outline: a wrapper for `scripts/check_warehouse.py`. Every Postgres test already connects through `connect_or_fail`, which fails with the same message when the server is down, and `--persistence` restarts the container, which a test must never do.
- **Expected files.**
  - `tests/test_mock_api_verify.py` (new), `tests/conftest.py` (new); `tests/test_dbt.py`, `tests/test_metrics.py`, `tests/test_staging.py`, `tests/test_staging_reconciliation.py`, `tests/test_pipeline.py` (the shared fixture and helpers)
  - `docs/etl/05-pipeline.md` ("How it is verified"), `docs/etl/03-dbt-star-schema.md`, `docs/etl/04-metrics.md`, `docs/mock-api.md` (section 7, the scripts now also run under pytest), `docs/decisions.md` (D-045), this file, `CLAUDE.md`
- **Gate.**
  - Both wrappers pass. `uv run python -m mock_api.verify` and `uv run python -m mock_api.verify_app` still run on their own and print the same last lines.
  - The full suite passes, 0 skipped, with 5 more tests than after step 5.1 (2 wrappers, 2 sabotage cases, the read-only guard). Its runtime is recorded before and after the fixture change, and is lower after.
  - The full suite also passes in reverse order (`--reverse`), with the same counts.
  - The tests that move to the shared fixture keep their assertions unchanged (the diff shows fixture changes only), and every sabotage test still fails the check it targets, so sharing the build hides nothing.
  - `pytest -m "not postgres"` passes with no `POSTGRES_*` set, 0 skipped.
- **Sabotage.**
  - `mock_api.verify`: monkeypatch the `serialize_issue` that `mock_api.verify` imports, to shift every due date by one day. `main` returns 1 and prints `FAILED:`, and the wrapper's assertion fails.
  - `mock_api.verify_app`: monkeypatch the `serialize_issue` that `mock_api.app` imports, to drop `customfield_10016`. The HTTP responses no longer equal the serializer, `main` returns 1.
  - The exact patches are confirmed at step start; each must make `main` return 1, not raise.
- **Docs in the same commit.** As listed above.
- **Decisions to record.** D-045.
- **Commit message:**

```
test(pipeline): add pytest wrappers for the mock API verify scripts

Runs mock_api.verify and mock_api.verify_app inside pytest, with a sabotage case each, and builds the test warehouse once for the tests that only read it.
```

### Step 5.3: flag rollup rows for the dashboard

- **Goal.** A BI tool can keep totals out of its sums by filtering on one boolean (D-047).
- **Scope.** `is_rollup` on `rpt_q3_breach_rate` and `rpt_q4_time_in_status`, described in `_metrics__questions_1_3.yml` and `_metrics__questions_4_6.yml`, `not_null`, and a singular test that it is true exactly on rows with a rollup label. Row counts and every existing value are unchanged.
- **Expected files.**
  - `dbt/models/metrics/rpt_q3_breach_rate.sql`, `dbt/models/metrics/rpt_q4_time_in_status.sql`, `dbt/models/metrics/_metrics__questions_1_3.yml`, `dbt/models/metrics/_metrics__questions_4_6.yml`, `dbt/tests/assert_rollup_flag_matches_labels.sql` (new)
  - `tests/test_metrics.py` (Q3 and Q4 reconciliations also check the flag)
  - `docs/etl/04-metrics.md`, `docs/metrics/q3-breach-rate.md`, `docs/metrics/q4-time-per-status.md`, `docs/decisions.md` (D-047), this file
- **Gate.**
  - `dbt build` passes. The flag splits the rows 18/30 (Q3) and 12/4 (Q4).
  - Summing `due_issue_count` over `is_rollup = false` gives 320, the number of issues with a due date.
  - The Q3 and Q4 reconciliations with `raw_data/` still pass. The full suite passes, 0 skipped.
- **Sabotage.** In a copy of `dbt/`, set `is_rollup` to false on the Q4 "All teams" rows. The new singular test fails, and the sum over `is_rollup = false` no longer matches the detail rows.
- **Docs in the same commit.** As listed above. The pages say which rows the dashboard should keep for each visual.
- **Decisions to record.** D-047. D-039 gets a pointer to it.
- **Commit message:**

```
feat(metrics): flag rollup rows in breach rate and time in status

Adds is_rollup to rpt_q3_breach_rate and rpt_q4_time_in_status, so a BI tool can keep totals out of its sums, with a test that it matches the rollup labels.
```

### Step 5.4: GitHub Actions CI (first to cut)

**This is the first step to cut if time runs short** (the cut order in [`CLAUDE.md`](../CLAUDE.md#current-phase-etl-build-target-date-to-be-set-steps-and-gates-docsetl-planmd)). Without it the repo still has everything else: the one-command run, the full suite with its fast subset, and the clean-run acceptance done by hand and recorded with its date in the clean-run guide. The README status table then says "CI: deferred", and nothing runs the tests on push.

- **Goal.** Every push and pull request runs the test suite against a Postgres service container, with the same commands as a local run.
- **Scope.** One workflow: check out, install uv and `uv sync`, `uv run python scripts/generate_raw_data.py`, the mock API's verify scripts, then the suite D-045 chooses for CI, with a `postgres:18.6` service container (the same image as [`warehouse/docker-compose.yml`](../warehouse/docker-compose.yml)) and `POSTGRES_*` and `AAP_API_*` set in the job's environment. No secret: the credentials are throwaway values that exist only in the job. Action versions are pinned to exact releases, chosen at step start.
- **Expected files.** `.github/workflows/ci.yml` (new); `docs/etl/05-pipeline.md`, `docs/stack.md` (CI is now in use), `docs/decisions.md` (D-045 consequence, if CI changes it), this file.
- **Gate.**
  - Locally: the workflow file parses, and every command it runs has passed locally with the same environment variables.
  - **On GitHub, which needs Yassine:** Claude Code never pushes. Yassine pushes the branch; the run must be green and show 0 skipped tests.
  - **Sabotage, also on GitHub:** a throwaway branch that breaks one assertion makes the run red on that test. The branch is deleted afterwards and never merged.
- **Docs in the same commit.** As listed above.
- **Decisions to record.** None new, unless D-045's CI choice changes once real run times are known.
- **Commit message:**

```
ci: add GitHub Actions workflow with a Postgres service container

Runs the test suite on every push and pull request against a postgres:18.6 service container, with the same commands as a local run.
```

### Step 5.5: final documentation and the clean-run acceptance

- **Goal.** The docs describe the finished v1, and a fresh clone with an empty database reproduces every documented count with one command after setup.
- **Clean-run acceptance (part of the gate, done before the docs are written).**
  1. Fresh clone of the branch into a scratch directory, `cp .env.example .env` with local values, `uv sync`, `uv run python scripts/generate_raw_data.py`.
  2. An empty database that leaves the real one alone: a separate compose project with its own volume and port, `docker compose --env-file .env -p aap-cleanrun -f warehouse/docker-compose.yml up -d --wait`, with `POSTGRES_PORT=5434` in that clone's `.env`. Claude Code cannot delete volumes, so Yassine removes `aap-cleanrun_pgdata` afterwards.
  3. `uv run --env-file .env python -m pipeline.run` exits 0.
  4. Every count matches the documented numbers: raw data (3, 12, 36, 530, 1,488, 320, 36), 579 landed files, the 8 staging tables, the 4 dimensions and 3 facts, the 10 `rpt_` tables, `dbt build` `PASS=` the figure after step 5.3, and the full suite passing with 0 skipped.
  5. The time of each stage is recorded in the clean-run guide.
- **Scope of the docs.**
  - `docs/etl/05-pipeline.md`: every section filled, status done.
  - `docs/clean-run.md` (new): the clean-run guide, prerequisites to counts, with the measured times and how to start over.
  - `README.md` and `README.fr.md`: quick start uses the one command; status table and test counts updated; the Session 5 link checked.
  - `docs/etl/README.md`: stage 5 done. `CLAUDE.md`: phase and verification targets (pytest count, `dbt build` figure, the new tests).
- **Expected files.** The pages above.
- **Gate.**
  - The clean-run acceptance above, every count matching.
  - Every relative link in the changed pages resolves (a scripted check over the Markdown files).
  - `CLAUDE.md` stays at 125 lines or fewer. The commit hook passes.
- **Sabotage.** The acceptance counts are compared by a short script against the list of documented numbers (kept in the scratch clone, not committed). Changing one expected number (1,488 transitions to 1,489) makes it report exactly that table, so the comparison cannot pass vacuously.
- **Docs in the same commit.** This step is the docs.
- **Decisions to record.** None new.
- **Commit message:**

```
docs: finalize pipeline documentation and clean-run guide

Fills docs/etl/05-pipeline.md, adds a clean-run guide measured on a fresh clone and an empty database, and updates the README status and numbers.
```

### Timeline

The internship ends mid-November, taken here as Friday 2026-11-13: 9 working days from Tuesday 2026-11-03. Estimates are working days for one session's work, gates included. The Power BI dashboard is not in this plan and shares the buffer.

| Step | Work | Estimate (days) | Target date | If behind schedule |
|---|---|---|---|---|
| 0 | Plan confirmed (decisions D-043 to D-047, commit-count exception) | 0.25 | 2026-11-04 | keep |
| 5.1 | One-command pipeline, completeness checks, hook paths | 1.5 | 2026-11-05 | keep |
| 5.2 | Verify wrappers, shared test build | 0.5 | 2026-11-06 | keep |
| 5.3 | `is_rollup` on the two rollup tables | 0.5 | 2026-11-06 | keep |
| 5.4 | GitHub Actions CI (needs Yassine to push) | 1 | 2026-11-09 | **✂ cut line: cut first** |
| 5.5 | Clean-run acceptance and final docs | 1 | 2026-11-10 | keep, shorten the polish |
| | Buffer (shared with the dashboard) | 3 | 2026-11-13 | |

Total planned work: 4.75 days. If 5.4 is cut, 5.5 moves up a day to 2026-11-09 and the buffer grows to 4 days.

Step 5.5 is never cut, only shortened: the acceptance and the README numbers stay, "docs polish" goes (the third item in the cut order; the second, the date dimension, is not in v1). The reconciliation against `simulator/sprint_report.py` is never cut.

**Final report after 5.5:** a table of the commits; the clean-run counts and times; the suite and fast-subset runtimes; what the dashboard can rely on (the ten `rpt_` tables, their grains, `is_rollup`); every assumption made, and anything cut.
