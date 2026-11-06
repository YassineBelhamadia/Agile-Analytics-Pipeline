# CLAUDE.md — Agile Analytics Pipeline (AAP)

Standing context for every Claude Code session in this repo. Read it fully before any task.
Details live in `docs/`. This file points to them instead of copying them.

## What this project is
A portfolio data pipeline. A SimPy simulator generates realistic multi-team Jira data plus team
capacity data. A mock API serves it in Jira's real REST shape. An ETL loads it into a warehouse
for a BI dashboard that answers six business questions about team delivery vs. capacity.
Audience: employers hiring junior data / analytics engineers. Every design choice must be
defensible in an interview, including the options we rejected.

## Read these first
- `README.md` (English) and `README.fr.md` (French): public overview, headline results, quick start, status
- `docs/workflow.md`: sessions, branches, gates, commit format, hooks, permissions; `docs/etl-plan.md`: every planned commit, gate and message
- `docs/decisions.md`: decision log (`decision-log.md` is superseded, kept for history); `docs/etl/`: one page per stage; `docs/clean-run.md`: fresh clone to every count
- `docs/domain-model.md`: entities, relationships, sprint metric definitions, calibration, v1 limitations
- `docs/mock-api.md`: endpoints, pagination, verified vs. assumed; `docs/stack.md`: tool choices, reasons, what is deferred

## Repo layout
- `simulator/`: SimPy simulator, per-team random streams, persistence. Done. Do not touch unless asked.
- `scripts/`: `generate_raw_data.py` writes `raw_data/` (ground truth, gitignored, regenerable); `check_warehouse.py`; `test_hooks.sh`.
- `mock_api/`: FastAPI app, Jira-shaped endpoints plus `/capacity`, from `raw_data/`. Done, capacity included.
- `ingestion/`: `client.py` + `extract.py` land raw pages and `manifest.json` in gitignored `landing/`;
  `parse.py` + `load_staging.py` fill all 8 `staging` tables, reconciled with `raw_data/`. `tests/`: pytest, no server.
- `pipeline/`: one-command run, mock API subprocess, runtime completeness checks (docs/etl/05-pipeline.md).
- `warehouse/`: Postgres compose file. `dbt/`: `stg` views, `marts` star schema (natural keys) + `rpt_` metric tables (D-033).
- `.githooks/` + `.claude/settings.json`: hooks, permissions. `dashboard/`: Power BI, later. `docs/`: source of truth.

## Stack (locked, do not redesign; reasons in `docs/stack.md`)
- Python (simulation, extraction, parsing), uv, pytest, FastAPI + uvicorn (mock API).
- PostgreSQL in Docker (warehouse), dbt Core (transformations, data tests), GitHub Actions (CI), Power BI.
- Deferred: orchestration (Airflow or similar), after the v1 ETL works end to end; the dbt vs. plain SQL write-up, until the dbt project exists.

## Architecture principles
- **Ground truth vs. views.** The simulator holds one internal model. The mock API never stores
  a second copy; each endpoint is a serializer projecting ground truth into Jira's shape.
- **Mapping belongs to ingestion.** The API emits Jira's opaque fields (`customfield_10016` =
  story points, `customfield_10020` = sprints). The ingestion client maps them back to clean names.
- **Capacity is separate.** It has no real Jira contract, so it gets its own small endpoint and a
  schema of our own design. Keep it clearly separate from the Jira-shaped endpoints in code and docs.
- **Layers.** Raw landing files (untouched API responses) → staging tables in Postgres →
  dbt star schema → business-question models.
- **Star schema grain.** Facts: status transition, sprint membership, capacity (team + sprint). Dims: team, user, sprint, issue.

## Data contract facts the ETL must respect
- Search uses `/rest/api/3/search/jql` with `nextPageToken` / `isLast` (no `startAt`, no `total`). The old `/search` is removed. Unbounded JQL is rejected, so search per project.
- Changelog and agile endpoints page with `startAt` / `maxResults` / `isLast`.
- The changelog holds status items AND Sprint items (`fieldId: customfield_10020`). Sprint
  membership and its `added_at` time are derived from the Sprint items.
- Use the dedicated changelog endpoint, not `expand=changelog` (capped at 100 histories).
- Timestamps: the platform API uses `+0000`, the agile API uses `+00:00`. Parse both. All times are UTC.
- Status IDs are a fixed map (10000 to 10004), the same for every team.
- Epics have null story points and never change status. Exclude them from cycle-time and
  time-in-status metrics.
- No `priority` field in v1, on purpose (see domain-model.md).
- `duedate` is set when an issue first joins a sprint, by an authored policy that is never tuned;
  Epics and never-scheduled issues stay null. Rule: domain-model.md, "Deadline (duedate) rule".
- Breach (business question 3): issues with a duedate only, as defined in that same section. As-of = last event (D-036).
  Report resolved-only (Story 20.4%, Task 28.9%, Bug 38.8%) and incl. past due, split by committed vs. scope-added.
- Every request needs an `Authorization` header (Basic or Bearer, any non-empty value).
- `GET /capacity`: our own schema, not Jira's (snake_case, full-precision `pto_days`, agile-style
  paging, `team`/`sprint` filters, 36 rows). Contract: docs/mock-api.md section 11.

## Sprint metric definitions (exact timestamps, never calendar days)
- Committed: story points of issues with `added_at <= sprint start`
- Scope added: story points of issues with `added_at > sprint start`
- Completed: story points of issues with `resolutiondate <= sprint end`
- `simulator/sprint_report.py` is the answer key. Warehouse sprint metrics must match it
  exactly for all 36 sprints. A mismatch is a bug in one of them, never a rounding detail.

## Shipped dataset (SEED=22): verification targets
3 teams, 12 users, 36 sprints, 36 capacity rows, 530 issues, 1,488 status transitions,
320 sprint memberships, 320 issues with a duedate (210 null). Extraction, staging and warehouse
counts must match these. Staging also equals raw_data/ row by row (raw timestamps truncated to ms; docs/etl/02-staging.md).
- Marts (dbt): dims 3 teams / 12 users / 36 sprints / 530 issues; facts 1,488 / 320 / 36 (dbt tests check these).
  Metrics: `rpt_sprint_metrics` equals sprint_report.py on all 36 sprints (hits PHX 5, NMD 6, ATL 9); index docs/metrics/. 10 `rpt_` tables;
  `is_rollup` splits `rpt_q3_breach_rate` 18/30 and `rpt_q4_time_in_status` 12/4 (D-047). `dbt build`: PASS=254. pytest: 140 passed, 0 skipped.
- Landing: 3 boards, 36 sprints, 530 issues, 36 capacity rows, changelogs 1,488 status + 320
  Sprint entries, 320 sprint-issue entries; 579 files at default page sizes, reruns byte-identical.
- Calibration: seeds 1-60, held out 61-100 (`simulator/calibration.py`); results only in domain-model.md.
- If SEED or any personality parameter changes, regenerate and update these numbers here and in the docs in the same change.

## Commands (from repo root; `--env-file .env` wherever credentials are needed)
- Data / mock API: `uv run python scripts/generate_raw_data.py`; `uv run uvicorn mock_api.app:app --port 8000`
- Whole pipeline (Postgres up, `raw_data/` generated, API port free): `uv run --env-file .env python -m pipeline.run`.
  Exit 0 ok, 1 stage failed, 2 precondition (nothing written), 3 completeness check failed, 130 Ctrl-C, 143 SIGTERM (D-046, D-048). Its first line names the target database.
- Verify: `uv run python -m mock_api.verify`, `uv run python -m mock_api.verify_app`, `uv run python -m simulator.calibration`
- Postgres up (postgres:18.6, localhost:5433, credentials from the root `.env`; copy `.env.example`):
  `docker compose --env-file .env -f warehouse/docker-compose.yml up -d --wait`
- Postgres smoke check: `uv run --env-file .env python scripts/check_warehouse.py`; down: compose command with `down`, never `-v`. Exported `POSTGRES_*` override `--env-file`.
- Stages by hand: `uv run --env-file .env python -m ingestion.extract` (mock API running, env vars only, D-025), then `ingestion.load_staging` (`--rebuild` for schema changes)
- dbt: `uv run --env-file .env dbt build --project-dir dbt --profiles-dir dbt` (or `debug`; dbt also reads `.env` itself, D-026)
- Tests: `uv run --env-file .env python -m pytest -rs` (Postgres tests skip without `POSTGRES_*`; gates need 0 skipped; `--reverse` runs them backwards)
- Fast subset, no database, not a gate: `uv run python -m pytest -m "not postgres"`. Only read-only tests may use `built_warehouse` (D-045).
- Hooks: `sh scripts/test_hooks.sh`; enable once per clone (Yassine only): `git config core.hooksPath .githooks`

## Working rules
- Commit only on a feature branch (`feat/etl-session-N`), one commit per `docs/etl-plan.md` step,
  after its gate passes, with the plan's message verbatim. Hooks enforce the format and the docs
  check (`docs/workflow.md`). Never push. Gate fails twice: stop, write a failure note, no commit.
- Conventional Commits (feat, fix, refactor, docs, chore, test, ci); subject <= 72 chars. Never
  mention Claude or add co-author/session trailers. Hook bypass variables are Yassine's; report any bypass used.
- One task per prompt. Do not start the next block until I confirm.
- Do not touch `simulator/` unless the task says so.
- Do not redesign locked decisions. If you think one is wrong, stop and tell me why.
- When checking external docs (Atlassian), report what is confirmed vs. assumed, with sources.
- Verify against the real `raw_data/`, not toy data, with at least one sabotage test proving a check can fail.
- If something can't be done as asked, say so instead of silently working around it.
- No credentials in tracked files. `.env` is gitignored; `.env.example` holds placeholders.
- Keep reports short: what changed, verification results, what was assumed, what was left out.

## Current phase: ETL build, target date to be set (steps and gates: `docs/etl-plan.md`)
0-1. Sessions 0 and 1: workflow scaffolding; capacity endpoint, Postgres in Docker, extraction client (done)
2. Session 2: parsing and staging tables (done, on `feat/etl-session-2`)
3. Session 3: dbt star schema (done, on `feat/etl-session-3`; docs/etl/03-dbt-star-schema.md)
4. Session 4: sprint metrics and the six business questions (done, on `feat/etl-session-4`; docs/metrics/)
5. Session 5: one-command pipeline, tests, docs, clean run from an empty database (done, on `feat/etl-session-5`; docs/clean-run.md)

Next: CI on its own branch (etl-plan step 5.4), then the Power BI dashboard. Never cut the reconciliation against `sprint_report.py`.

## Maintaining this file
Update it whenever a decision, rule, command, phase or verification target changes, in the
same change that causes it. When a verification target, test count or project status changes, also update
the numbers and the status table in `README.md` and `README.fr.md`. Keep it at 125 lines or fewer; move detail into `docs/`.
