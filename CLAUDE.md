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
- `docs/domain-model.md`: entities, relationships, sprint metric definitions, calibration, v1 limitations
- `docs/mock-api.md`: endpoints, pagination, what is verified vs. assumed
- `docs/stack.md`: tool choices and what is deferred (created in ETL Session 1)

## Repo layout
- `simulator/`: SimPy simulator, per-team random streams, persistence. Done. Do not touch unless asked.
- `scripts/generate_raw_data.py`: runs the simulation and writes `raw_data/`
- `raw_data/`: generated ground truth. Gitignored, regenerable.
- `mock_api/`: FastAPI app serving Jira-shaped endpoints from `raw_data/`. Done except capacity.
- `ingestion/`: extraction client, then parsing into staging. In progress.
- `warehouse/`: Postgres in Docker, then the dbt project. In progress.
- `dashboard/`: Power BI. Later.
- `docs/`: documentation, the source of truth for decisions.

## Stack (locked, do not redesign)
- Python for simulation, extraction and parsing. uv for dependencies, pytest for tests.
- FastAPI + uvicorn for the mock API.
- PostgreSQL in Docker as the warehouse.
- dbt Core for transformations and data tests.
- GitHub Actions for CI.
- Power BI for the dashboard.
- Deferred: orchestration (Airflow or similar), after the v1 ETL works end to end.

## Architecture principles
- **Ground truth vs. views.** The simulator holds one internal model. The mock API never stores
  a second copy; each endpoint is a serializer projecting ground truth into Jira's shape.
- **Mapping belongs to ingestion.** The API emits Jira's opaque fields (`customfield_10016` =
  story points, `customfield_10020` = sprints). The ingestion client maps them back to clean names.
- **Capacity is separate.** It has no real Jira contract, so it gets its own small endpoint and a
  schema of our own design. Keep it clearly separate from the Jira-shaped endpoints in code and docs.
- **Layers.** Raw landing files (untouched API responses) → staging tables in Postgres →
  dbt star schema → business-question models.
- **Star schema grain.** Fact = one row per status transition. Dimensions: team, user, sprint,
  issue. Capacity keyed by team + sprint.

## Data contract facts the ETL must respect
- Search uses `/rest/api/3/search/jql` with `nextPageToken` / `isLast` (no `startAt`, no `total`).
  The old `/search` endpoint is removed. Unbounded JQL is rejected, so search per project.
- Changelog and agile endpoints page with `startAt` / `maxResults` / `isLast`.
- The changelog holds status items AND Sprint items (`fieldId: customfield_10020`). Sprint
  membership and its `added_at` time are derived from the Sprint items.
- Use the dedicated changelog endpoint, not `expand=changelog` (capped at 100 histories).
- Timestamps: the platform API uses `+0000`, the agile API uses `+00:00`. Parse both. All times are UTC.
- Status IDs are a fixed map (10000 to 10004), the same for every team.
- Epics have null story points and never change status. Exclude them from cycle-time and
  time-in-status metrics.
- No `priority` field in v1, on purpose (see domain-model.md).
- Every request needs an `Authorization` header (Basic or Bearer, any non-empty value).

## Sprint metric definitions (exact timestamps, never calendar days)
- Committed: story points of issues with `added_at <= sprint start`
- Scope added: story points of issues with `added_at > sprint start`
- Completed: story points of issues with `resolutiondate <= sprint end`
- `simulator/sprint_report.py` is the answer key. Warehouse sprint metrics must match it
  exactly for all 36 sprints. A mismatch is a bug in one of them, never a rounding detail.

## Shipped dataset (SEED=22): verification targets
3 teams, 12 users, 36 sprints, 36 capacity rows, 530 issues, 1,488 status transitions,
320 sprint memberships. Extraction, staging and warehouse counts must match these.
- Calibration seeds 1-60, held-out seeds 61-100 (`simulator/calibration.py`). Results live
  in domain-model.md; do not copy them here.
- If SEED or any personality parameter changes, regenerate and update these numbers here
  and in the docs in the same change.

## Commands (venv active, from repo root)
- Generate data: `python scripts/generate_raw_data.py`
- Run mock API: `uvicorn mock_api.app:app --port 8000`
- Verify serializers: `python -m mock_api.verify`
- Verify API: `python -m mock_api.verify_app`
- Calibration table: `python -m simulator.calibration`
- Postgres (after Session 1): `docker compose -f warehouse/docker-compose.yml up -d`,
  credentials from `.env` (see `.env.example`)

## Working rules
- Never commit or push. Prepare the change and report; I review and commit myself.
- Conventional Commits (feat, fix, refactor, docs, chore, test). One block per commit.
- One task per prompt. Do not start the next block until I confirm.
- Do not touch `simulator/` unless the task says so.
- Do not redesign locked decisions. If you think one is wrong, stop and tell me why.
- When checking external docs (Atlassian), report what is confirmed vs. assumed, with sources.
- Verify against the real `raw_data/`, not toy data. Include at least one sabotage test that
  proves a check can fail.
- If something can't be done as asked, say so instead of silently working around it.
- No credentials in tracked files. `.env` is gitignored; `.env.example` holds placeholders.
- Keep reports short: what changed, verification results, what was assumed, what was left out.

## Current phase: ETL build, target done by Monday 2026-10-19
1. Session 1: capacity endpoint, Postgres in Docker, extraction client
2. Session 2: parsing and staging tables
3. Session 3: dbt star schema
4. Session 4: sprint metrics and the six business questions, reconciled with sprint_report.py
5. Session 5: one-command pipeline, tests, CI, docs, clean run from an empty database

If behind schedule, cut in this order: GitHub Actions, date dimension, docs polish.
Never cut the reconciliation against `sprint_report.py`.

## Maintaining this file
Update it whenever a decision, rule, command, phase or verification target changes, in the
same change that causes it. Keep it under about 130 lines; move detail into `docs/`.