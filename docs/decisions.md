# Decision log

One entry per decision, newest at the bottom. Entries are seeded only from what [`CLAUDE.md`](../CLAUDE.md) and `docs/` already say. Each entry cites its source. Where no source gives a reason, the entry says *rationale to confirm with Yassine* instead of inventing one.

The narrative record of the early project decisions, with the full argument for each rejected option, is [`decision-log.md`](decision-log.md). The entries below summarize it, and it is kept for history only. Its section 7 ("v1 goal") ends mid-sentence: the text was already cut off in the commit that added the file (03e7a39), with no trailing newline, and the file has not changed since. No other copy of the sentence exists in the git history.

**Dates.** A seeded entry's date is the date of the commit that first recorded it, so it is "decided on or before" that date.

## Template

```markdown
## D-NNN: <short title>

- **Date:** YYYY-MM-DD
- **Decision:** what was decided, in one or two sentences.
- **Context:** the problem or constraint that forced a choice.
- **Options considered:** each option, including the one chosen.
- **Why:** the reason for the choice, and for rejecting the others.
- **Consequences:** what this makes easier, harder, or rules out.
- **Source:** where this is recorded or implemented.
```

---

## D-001: Synthetic data from a simulator, not a public Jira instance

- **Date:** 2026-09-17
- **Decision:** generate the dataset synthetically. Do not use a public Jira instance (Apache, Red Hat).
- **Context:** no access to real company Jira data. The project is about team delivery against capacity.
- **Options considered:**
  - public Jira instances (anonymous REST reads);
  - an LLM-written CSV;
  - real Jira Cloud fed with generated data;
  - a simulation.
- **Why:**
  - Public instances are OSS bug trackers, with no sprints and no stable team structure.
  - LLM-written data fails on scale, referential integrity, statistical realism and reproducibility.
  - Jira Cloud stamps timestamps at call time, so history can't be backdated.
- **Consequences:** every figure is synthetic, and the honesty note for interviews applies (decision-log 5.5).
- **Source:** [`decision-log.md`](decision-log.md) 5.1–5.3.

## D-002: Discrete-event simulation (SimPy)

- **Date:** 2026-09-17 (options); SimPy recorded as decided in the same file.
- **Decision:** model the delivery process as a SimPy discrete-event simulation.
- **Context:** naive per-field randomness gives data with no structure, e.g. no link between a capacity drop and a velocity drop.
- **Options considered:**
  - A: discrete-event simulation;
  - B: statistical sampling plus scripted events;
  - naive per-field randomness (rejected).
- **Why:** A lets correlations emerge from the process mechanics instead of being hand-scripted, and gives the most realistic result. The log first leaned toward B for timeline reasons. Why A was picked over B despite that: *rationale to confirm with Yassine*.
- **Consequences:** realism comes with calibration work ([D-011](#d-011-calibration-on-seeds-160-held-out-61100-shipped-seed22)).
- **Source:** [`decision-log.md`](decision-log.md) 5.4.

## D-003: Serve the data through a mock API in Jira's REST shape

- **Date:** 2026-09-17
- **Decision:** serialize the simulation into Jira's real REST response shapes and serve them from a FastAPI app, instead of reading files directly.
- **Context:** the ingestion layer should show real API client work: auth, pagination, parsing against a real schema.
- **Options considered:** read local files directly; a mock API.
- **Why:** the client is built against the real contract, so it could point at real Jira with few changes.
- **Consequences:** the mock's fidelity is documented as confirmed vs. assumed ([`mock-api.md`](mock-api.md) section 8).
- **Source:** [`decision-log.md`](decision-log.md) 5.5.

## D-004: Ground truth vs. views

- **Date:** 2026-09-21
- **Decision:** the simulator holds one ground-truth model. Each mock API endpoint is a serializer projecting it into Jira's shape. No second Jira-shaped copy is stored.
- **Context:** several endpoints expose overlapping views of the same entities.
- **Options considered:** *rationale to confirm with Yassine* (the doc records no alternative).
- **Why:** the simulator model stays independent of any JSON shape. The serialization is a separate layer on top.
- **Consequences:** `mock_api.verify` checks every serializer against the ground truth.
- **Source:** [`domain-model.md`](domain-model.md), "Core principle".

## D-005: Locked stack

- **Date:** 2026-10-16
- **Decision:**
  - Python for extraction and parsing.
  - PostgreSQL in Docker as the warehouse.
  - dbt Core for transformations and tests.
  - GitHub Actions for CI.
  - Power BI for the dashboard.
  - Supporting tools: uv, pytest, FastAPI, uvicorn.
- **Context:** the stack must be free, standard, and match junior data and analytics engineering roles.
- **Options considered:** per layer, only the chosen tool is recorded. The alternatives that were weighed are *rationale to confirm with Yassine*.
- **Why:** reasons per layer are in `stack.md`. For example, Python is one language for the upstream half, and dbt gives SQL models with a dependency graph and built-in tests.
- **Consequences:**
  - Power BI Desktop needs Windows (a VM on macOS).
  - Changing a locked choice needs a written reason first.
- **Source:** [`stack.md`](stack.md).

## D-006: Orchestration and the dbt vs. plain SQL write-up are deferred

- **Date:** 2026-10-16
- **Decision:**
  - No orchestrator (Airflow, Dagster, Prefect) in v1. v1 is one command from an empty database.
  - The dbt vs. plain SQL comparison waits until the dbt project exists.
- **Context:** v1 must work end to end first.
- **Options considered:** schedule now or later; write the comparison now or after building.
- **Why:**
  - A scheduler adds infrastructure without making v1 more correct.
  - The comparison should rest on what dbt actually cost and saved here.
- **Consequences:** Session 5 builds a one-command run, not a DAG.
- **Source:** [`stack.md`](stack.md), "Deferred on purpose".

## D-007: Story points as `customfield_10016`, mapped in ingestion

- **Date:** 2026-09-21
- **Decision:** the mock API emits Jira's opaque custom fields: `customfield_10016` for story points and `customfield_10020` for sprints. The ingestion layer maps them to clean names.
- **Context:** real Jira exposes story points as a site-specific custom field.
- **Options considered:** opaque custom fields; a clean `story_points` key in the API (an acceptable shortcut, to be stated as a simplification if used).
- **Why:** fidelity. Real integrations always do this mapping, so doing it here is realistic engineering.
- **Consequences:** staging owns the mapping ([`etl-plan.md`](etl-plan.md) Session 2).
- **Source:** [`domain-model.md`](domain-model.md), "Jira API realism decision".

## D-008: Capacity through its own small REST endpoint

- **Date:** 2026-09-21 (approach); endpoint built 2026-10-20
- **Decision:** serve capacity from `GET /capacity`, with a schema of our own design, kept separate from the Jira-shaped endpoints.
- **Context:** Jira has no capacity concept, and there is no universal contract to imitate.
- **Options considered:** a custom REST endpoint; a flat CSV (a legitimate alternative, since many companies do this).
- **Why:** pipeline consistency: two APIs, one client pattern, rather than mixing an API client with a file reader.
- **Consequences:** capacity uses snake_case fields and agile-style paging. It joins on project key and agile sprint ID.
- **Source:** [`domain-model.md`](domain-model.md), "Ingestion split"; [`mock-api.md`](mock-api.md) section 11.

## D-009: Per-team random streams

- **Date:** 2026-10-11
- **Decision:** each team draws from its own numpy `Generator`, derived from the base seed and the team key. Transition IDs are namespaced per team.
- **Context:** with one shared stream, an extra draw by one team shifted every later draw of the others.
- **Options considered:** one global stream (the previous design); per-team streams.
- **Why:** teams can be calibrated independently. Changing one team leaves the others' data byte-identical (verified).
- **Consequences:** any future shared randomness needs its own named stream.
- **Source:** [`domain-model.md`](domain-model.md), "Per-team random streams".

## D-010: Bugs carry story points and count toward velocity

- **Date:** 2026-09-21
- **Decision:**
  - Stories, Tasks and Bugs carry story points.
  - Epics have none (null in the API).
- **Context:** Scrum convention varies on pointing bugs.
- **Options considered:** unpointed bugs (stricter convention); pointed bugs.
- **Why:** most real teams point bugs. A bug-heavy sprint then visibly lowers "new value" velocity.
- **Consequences:** Epics are excluded from point and time-based metrics.
- **Source:** [`domain-model.md`](domain-model.md), "Story points by issue type".

## D-011: Calibration on seeds 1–60, held out 61–100, shipped SEED=22

- **Date:** 2026-10-11
- **Decision:**
  - Tune personalities on seeds 1–60 and check them on 61–100.
  - Ship SEED=22, picked as a typical run: of seeds 1–100, it is the closest to the 60-seed averages among those where the ordering holds strictly.
- **Context:** 20-seed tuning carried noise as wide as the target bands.
- **Options considered:** 20-seed calibration (previous); 60 + 40 held out.
- **Why:** held-out seeds show whether the parameters describe the simulation or only fit their seeds.
- **Consequences:** changing either seed set invalidates the reported calibration figures.
- **Source:** [`domain-model.md`](domain-model.md), "Calibration seed set".

## D-012: Sprint metrics compare exact timestamps

- **Date:** 2026-10-11
- **Decision:** committed, scope added and completed compare exact timestamps against sprint start and end instants (09:00), never calendar days.
- **Context:** calendar-day comparison counted later scope as committed, and work done after 09:00 on the end day as completed.
- **Options considered:** calendar days (previous); exact timestamps.
- **Why:** correctness. One sprint's end instant is the next one's start.
- **Consequences:**
  - `simulator/sprint_report.py` is the answer key, and warehouse metrics must match it exactly.
  - The crf parameter was retuned after this change.
- **Source:** [`domain-model.md`](domain-model.md), "Where commit vs. completion vs. scope-added actually comes from".

## D-013: Fixed status IDs 10000–10004

- **Date:** 2026-10-11
- **Decision:** status IDs are one fixed map, the same for every team: To Do 10000, In Progress 10001, Code Review 10002, QA 10003, Done 10004.
- **Context:** Jira identifies statuses by ID in changelog items.
- **Options considered:** *rationale to confirm with Yassine*.
- **Why:** *rationale to confirm with Yassine*. The docs state the map and that it is fixed. `mock_api.verify` fails loudly on an unmapped status.
- **Consequences:** staging maps IDs to names with this map and fails on an unknown ID.
- **Source:** [`mock-api.md`](mock-api.md) section 3.1; `mock_api/config.py` `STATUS_IDS`; [`CLAUDE.md`](../CLAUDE.md).

## D-014: No `priority` field in v1

- **Date:** 2026-10-11
- **Decision:** priority is out of scope for v1. The mock API emits no `priority` field.
- **Context:** none of the six business questions needs it.
- **Options considered:** a decorative field; priority-aware queueing; leave it out.
- **Why:** a field that influences nothing is decoration. A real version needs priority-aware queueing and a recalibration.
- **Consequences:** a v1.1 candidate.
- **Source:** [`domain-model.md`](domain-model.md), "Known v1 limitations".

## D-015: Deadline (duedate) rule is an authored policy

- **Date:** 2026-10-16
- **Decision:**
  - Story and Task are due on the sprint's last workday.
  - Bug is due 5 workdays after joining, capped at that day.
  - Set on first sprint join only. Epics and never-scheduled issues stay null.
- **Context:** business question 3 needs deadlines, and Jira has no native SLA here.
- **Options considered:** *rationale to confirm with Yassine*.
- **Why:** fixed before any breach rate was measured. It is not calibrated, and must not be tuned to a target.
- **Consequences:** breach is reported resolved-only and including past due, split by committed vs. scope-added.
- **Source:** [`domain-model.md`](domain-model.md), "Deadline (duedate) rule".

## D-016: One JQL search per project

- **Date:** 2026-10-20
- **Decision:** extraction runs one `/rest/api/3/search/jql` search per project key, discovered from the boards.
- **Context:** the API rejects unbounded JQL. The old `/rest/api/3/search` is removed.
- **Options considered:** one unbounded search (rejected by the API); one search per project.
- **Why:** it is the only shape the API accepts. The mock rejects queries with no restriction ([`mock-api.md`](mock-api.md) section 5). Whether real Jira rejects unbounded enhanced-search queries the same way is not listed as confirmed in mock-api.md section 8.
- **Consequences:** a new project needs a board to be discovered.
- **Source:** `ingestion/extract.py`; [`CLAUDE.md`](../CLAUDE.md).

## D-017: Dedicated changelog endpoint, not `expand=changelog`

- **Date:** 2026-10-20
- **Decision:** read each issue's history from `/rest/api/3/issue/{key}/changelog`, paged to the end.
- **Context:** `expand=changelog` caps at 100 histories.
- **Options considered:** `expand=changelog` on the search; the dedicated endpoint.
- **Why:** a full history with no cap.
- **Consequences:** one request or more per issue (530 changelog pages in the shipped data).
- **Source:** `ingestion/extract.py`; [`mock-api.md`](mock-api.md) section 9.

## D-018: Raw landing files are kept untouched

- **Date:** 2026-10-20
- **Decision:**
  - Every response page is written to `landing/` exactly as received, with a manifest of the request behind each file.
  - Nothing is parsed during extraction.
- **Context:** the layers are raw landing, then staging, then the dbt star schema, then business models.
- **Options considered:** files on disk (built); raw `jsonb` in Postgres (`stack.md` names `jsonb` as a reason for Postgres); parsing during extraction.
- **Why:** the raw layer stays a faithful record that staging can be rebuilt from. Why files on disk rather than `jsonb`: *rationale to confirm with Yassine*.
- **Consequences:** staging reads `manifest.json`, never filenames. Reruns are byte-identical.
- **Source:** `ingestion/extract.py`; [`CLAUDE.md`](../CLAUDE.md), "Architecture principles".

## D-019: Staging has primary keys but no foreign keys

- **Date:** 2026-10-23
- **Decision:** staging tables get primary keys only. Relationships are checked by dbt tests in Session 3.
- **Context:** staging is a clean mirror, loaded as a full refresh in one transaction.
- **Options considered:** foreign keys in staging; dbt relationship tests.
- **Why:** set in the Session 2 spec ([`etl-plan.md`](etl-plan.md)). Beyond "dbt tests will check relationships later": *rationale to confirm with Yassine*.
- **Consequences:** an orphan row loads without error and must be caught by step 3.4.
- **Source:** [`etl-plan.md`](etl-plan.md) Session 2.

## D-020: Breach headline is resolved-only; bug-to-story ratio counts all created issues

- **Date:** 2026-10-23
- **Decision:**
  - Breach headline: resolved issues only. Story 20.4%, Task 28.9%, Bug 38.8% (answer key).
  - A separate open-and-overdue count at the last data timestamp, derived from the data on every build, never hardcoded ([D-036](#d-036-the-as-of-instant-is-derived-from-the-data), amended 2026-11-01).
  - The bug-to-story ratio counts all created issues.
- **Context:** 180 non-Epic issues never join a sprint.
- **Options considered:** for the ratio, sprint-scoped vs. all created issues.
- **Why:** a sprint-scoped ratio would drop 180 issues. The headline matches the answer key.
- **Consequences:** fixed inputs for Session 4.
- **Source:** [`etl-plan.md`](etl-plan.md) Session 4; [`domain-model.md`](domain-model.md), "Deadline (duedate) rule".

## D-021: Postgres driver: psycopg 3

- **Date:** 2026-10-23
- **Decision:** the staging loader talks to Postgres through psycopg 3 directly. It loads each table with `COPY ... FROM STDIN` inside one transaction.
- **Context:** step 2.1 needs a driver for typed bulk loads with transactional DDL.
- **Options considered:**
  - psycopg 3;
  - psycopg2;
  - SQLAlchemy, or pandas `to_sql` (both already dependencies).
- **Why:**
  - psycopg 3 is the maintained PostgreSQL adapter for Python, its `COPY` support fits bulk loads, and `scripts/check_warehouse.py` already uses it.
  - psycopg2 is the older generation of the same project.
  - Going through SQLAlchemy or pandas would add a layer between the explicit DDL and the rows, and `to_sql` infers column types instead of using the declared ones.
- **Consequences:** no new dependency (`psycopg[binary]>=3.3.6` was already in `pyproject.toml`). Column types live in one place, `load_staging.TABLES`.
- **Source:** [`etl-plan.md`](etl-plan.md) Session 2 spec; `ingestion/load_staging.py`; [`etl/02-staging.md`](etl/02-staging.md).

## D-022: Staging reloads by truncate and COPY; drop and recreate only on `--rebuild`

- **Date:** 2026-10-23
- **Decision:**
  - Staging tables are created once, if missing.
  - Every run checks each existing table against its declared definition (columns, types, nullability, primary key), then truncates every table and refills it with `COPY`, all in one transaction.
  - `--rebuild` drops and recreates the tables for a real schema change, without CASCADE.
  - This replaces the drop-and-recreate on every run used in step 2.1.
- **Context:** dbt staging models are often views on these tables. `DROP TABLE` fails while a view depends on the table, and `DROP ... CASCADE` would silently delete the views.
- **Options considered:**
  1. **Drop and recreate every run** (step 2.1). Breaks as soon as a view depends on staging. Its only advantage, schema changes applied automatically, is kept by `--rebuild`.
  2. **Drop ... CASCADE every run.** Reloads succeed, but they delete dependent dbt views without saying so, and dbt must rebuild them after every load.
  3. **Load into new tables, then swap by rename.** Views follow the table they were created on, not its name, so they keep pointing at the old table. That is wrong or broken, the same problem as option 1.
  4. **`DELETE` then `COPY`.** Also keeps views, but leaves dead rows that need vacuuming. `TRUNCATE` does the same job in one step and is transactional in Postgres.
  5. **Incremental upsert.** Not a full refresh. A row removed upstream would stay in staging.
  6. **Truncate and `COPY`, create if missing, `--rebuild` for schema changes** (chosen).
- **Why:**
  - Tables keep their identity, so dependent views survive a reload.
  - `TRUNCATE` is transactional, so a failed run still rolls back to the previous rows.
  - With tables persisting, a definition changed in code could otherwise meet an old table silently, for example a type widened in one place only. So the definition check stops the load and names `--rebuild`.
  - A rebuild without CASCADE fails loudly while a view depends on a table, and deletes nothing.
- **Consequences:**
  - A schema change means: drop the dependent views (or let dbt rebuild them), run `--rebuild`, then rerun dbt.
  - `TRUNCATE` takes an exclusive lock until commit, like the drop did, so readers wait.
  - `TRUNCATE` is not MVCC-safe: a concurrent transaction holding an older snapshot (repeatable read) can see the tables empty. Not an issue for a single-user pipeline.
- **Source:** `ingestion/load_staging.py` (`_ensure_table`, `load_staging`); [`etl/02-staging.md`](etl/02-staging.md); step 2.1b in [`etl-plan.md`](etl-plan.md).

## D-023: Staging changelog and status rules

- **Date:** 2026-10-23
- **Decision:** in staging,
  - status names come from the fixed map only, and the payload's own name must agree with it;
  - a Sprint changelog item adds one membership per sprint ID in `to` but not in `from`, and one that removes a sprint fails the load;
  - a history with more than one status item fails the load;
  - story points are stored as `numeric`.
- **Context:**
  - Step 2.2 derives transitions and memberships from changelog items.
  - The v1 data always has one item per history, and an empty `from` on Sprint items. Its status names always match the map.
  - None of these rules changes what is loaded today. They decide what happens when the contract changes.
- **Options considered:**
  - For names: take them from the payload (`toString`); use the map silently; use the map and check the payload.
  - For removals: model them as membership end times; ignore them; fail.
  - For story points: `integer` (all current values); `numeric`.
- **Why:**
  - The map is the rule (CLAUDE.md, data contract facts). Checking the payload against it means a renamed status shows up as an error rather than as two names for one ID.
  - Removals have no place in the v1 membership schema (`added_at` only, [`domain-model.md`](domain-model.md)), and ignoring one would leave a wrong membership in staging.
  - Jira story points are decimal on a real site, and `numeric` keeps sums exact for the sprint-report reconciliation.
- **Consequences:**
  - Carryover or sprint removal (a v1.1 candidate) needs a membership end column before these loads can succeed.
  - Adding a status means updating `parse.STATUS_NAMES`.
- **Source:** `ingestion/parse.py` (`status_name`, `parse_issues`); [`etl/02-staging.md`](etl/02-staging.md).

## D-024: Reconciliation truncates raw timestamps to milliseconds

- **Date:** 2026-10-23
- **Decision:** when staging is compared with `raw_data/`, each raw timestamp is read as UTC and truncated (floored) to milliseconds, then compared for exact equality. No other tolerance anywhere.
- **Context:**
  - `raw_data/` timestamps are naive with microseconds; the API emits milliseconds, truncated (`jira_datetime` in `mock_api/serializers.py`, which documents why: rounding could carry 23:59:59.9996 into the next day).
  - 2,403 of 2,651 raw timestamps differ from staging only below the millisecond.
- **Options considered:**
  - exact microsecond equality (fails on 2,403 values, for a difference the contract defines);
  - a ±1 ms tolerance (would also accept rounding and off-by-one bugs);
  - rounding (fails on 1,236 values);
  - truncation (chosen).
- **Why:** it reproduces the contract's own rule exactly, so any other difference still fails. The test shows rounding would not pass.
- **Consequences:**
  - Staging timestamps are millisecond-precise.
  - Session 4 metrics compare them with sprint boundaries. No boundary comparison is affected in the shipped data ([`etl-plan.md`](etl-plan.md), Session 4).
- **Source:** `tests/test_staging_reconciliation.py`; [`etl/02-staging.md`](etl/02-staging.md), "Reconciliation with raw_data/".

## D-025: Extraction settings come from the environment only, supplied by `--env-file .env`

- **Date:** 2026-10-30
- **Decision:** `ingestion.extract` keeps reading `AAP_API_BASE_URL` and `AAP_API_TOKEN` from the process environment only. Both are listed in `.env.example`, and the documented command is `uv run --env-file .env python -m ingestion.extract`. The base URL is checked (absolute `http`/`https` with a host) before any request.
- **Context:**
  - `.env.example` listed only `POSTGRES_*`. A `.env` copied from it held no API settings, so `uv run --env-file .env python -m ingestion.extract` failed with `Missing AAP_API_BASE_URL and AAP_API_TOKEN`, and the docs showed only inline variables.
  - A malformed base URL (e.g. `localhost:8000`) was retried 3 times, then failed with an httpx message that did not name the variable.
- **Options considered:**
  - load `.env` inside the client (e.g. python-dotenv): hides where settings come from, adds a dependency, and differs from how every other command here gets credentials;
  - keep inline variables only: works, but leaves `.env` incomplete and the setup undocumented;
  - list the variables in `.env.example` and use `--env-file .env`, like Postgres (chosen).
- **Why:** one way to supply settings across the repo, with no change to the locked environment-only design. Bad values fail at once with an error naming the variable.
- **Consequences:**
  - Running without `--env-file .env` and with no inline variables still fails, by design, now with a message that says how to fix it.
  - The error for a malformed URL does not echo its value, only its length.
- **Source:** `ingestion/extract.py` (`client_from_environment`); `.env.example`; [`etl/01-extraction.md`](etl/01-extraction.md).

## D-026: dbt Core 1.12 with dbt-postgres 1.11, in `dbt/`, profile from the environment

- **Date:** 2026-10-31
- **Decision:**
  - The dbt project lives in `dbt/` at the repo root, next to `ingestion/` and `warehouse/`.
  - Dependencies, added with uv: `dbt-core>=1.12.6,<1.13` and `dbt-postgres>=1.11.0,<1.12`. `uv.lock` pins them exactly.
  - `dbt/profiles.yml` is committed. Every connection value is `env_var('POSTGRES_*')` with no default.
  - Command: `uv run --env-file .env dbt <cmd> --project-dir dbt --profiles-dir dbt`.
  - Anonymous usage stats are off (`flags: send_anonymous_usage_stats: false`).
- **Context:** step 3.1 needs a dbt install that fits the locked stack ([D-005](#d-005-locked-stack)): dbt Core, Python 3.13, Postgres 18, uv. It also needs a profile that holds no secret.
- **Options considered:**
  - **Versions.**
    - dbt-core 1.12.6, the latest stable on PyPI (released 2026-10-14), with Python 3.10 to 3.14 in its classifiers (chosen).
    - 1.13, still beta in the dbt docs version list.
    - An older 1.x: no reason, and it would miss fixes.
    - "dbt v2". dbt now prints that v2 is the default for new installations. Confirmed in its install page (<https://docs.getdbt.com/docs/local/install-dbt>, read 2026-10-31): the default v2 comes under a separate license agreement, and an Apache 2.0 open-source v2 runtime is offered separately. v2 is not a dbt-core release on PyPI (dbt-core tops out at 1.12.6). Not checked: v2's Postgres support and feature parity. Rejected because the locked choice is dbt Core added with uv.
  - **Adapter.** Since dbt 1.8, adapters are versioned separately from core. dbt-postgres 1.11.0 (2026-07-16) is the latest. It requires `dbt-core>=1.8.0rc1,<2.0`, `dbt-adapters>=1.24.1`, and supports Python 3.13.
  - **Profile location.**
    - `~/.dbt/profiles.yml`, dbt's default: outside the repo, so each clone and CI must recreate it by hand.
    - `dbt/profiles.yml`, committed, reading `env_var()` (chosen).
  - **Credentials.**
    - Literal values in the profile: a committed secret.
    - `env_var()` with defaults: a missing variable would connect somewhere unexpected.
    - `env_var()` with no default (chosen).
- **Why:**
  - The newest stable pair inside the locked stack, with a 1.12.x / 1.11.x range so patch fixes come in only through `uv lock`.
  - The profile is versioned with the project, and credentials stay in the environment, the same as every other command here ([D-025](#d-025-extraction-settings-come-from-the-environment-only-supplied-by---env-file-env)).
  - A missing variable fails with `Env var required but not provided: '<NAME>'`.
- **Consequences:**
  - dbt-postgres brings `psycopg2-binary` as its own driver, next to the project's psycopg 3 ([D-021](#d-021-postgres-driver-psycopg-3)). The two don't interact.
  - **dbt-core 1.12 loads `.env` by itself.** `dbt/cli/main.py` calls `load_dotenv(find_dotenv(usecwd=True), override=False)` on every invocation, and there is no flag to turn it off.
    - So from the repo root, dbt fills in a `POSTGRES_*` variable missing from the environment with the value in the root `.env`. Variables already set still win, so the documented `--env-file .env` command behaves the same.
    - This differs from D-025's environment-only rule for our own code, and it is dbt's behavior, not ours. The documented command keeps `--env-file .env`, so it also works where `.env` is absent (CI).
    - `tests/test_dbt.py` runs dbt from a temporary directory, so a missing variable really fails there.
- **Source:** `pyproject.toml`, `uv.lock`, `dbt/profiles.yml`, `dbt/dbt_project.yml`; PyPI JSON for dbt-core, dbt-postgres and dbt-adapters (read 2026-10-31); [`etl/03-dbt-star-schema.md`](etl/03-dbt-star-schema.md).

## D-027: Two dbt layers with exact schema names, `stg` (views) and `marts` (tables)

- **Date:** 2026-10-31
- **Decision:**
  - `stg_` models are light views, one per staging table, in schema `stg`. They rename columns to one convention: each key is `<entity>_id` (`project_id` → `team_id`, `account_id` → `user_id`, `assignee_account_id` → `assignee_user_id`). They do nothing else.
  - Dimensions and facts are tables in schema `marts`.
  - `dbt/macros/generate_schema_name.sql` overrides dbt's macro so a model's `+schema` is used as is.
  - Two vars, both used only by the tests:
    - `staging_schema` (default `staging`), the source schema;
    - `schema_suffix` (default empty), appended to every built schema.
- **Context:** dbt's built-in `generate_schema_name` returns `<profile schema>_<custom schema>`. With a profile schema of `marts`, it would build `marts_stg` and `marts_marts`.
- **Options considered:**
  - **Layers.** One layer (marts straight from sources); stg views plus marts tables (chosen); an extra intermediate layer (nothing to put in it yet).
  - **Materialization of `stg_`.** Views (chosen): always current after a reload, no storage, cheap at 2,500 rows. Tables: would need a dbt run after every load to stay current.
  - **Schema names.**
    - dbt's default concatenation;
    - setting the profile schema per run;
    - overriding the macro (chosen).
  - **Test isolation.** Running the dbt tests on the real `stg`/`marts` (breaks the Session 2 rule that tests never touch real schemas); a second database (needs `CREATE DATABASE` and a second profile target); vars for the source schema and a suffix (chosen).
- **Why:**
  - Two layers keep renaming apart from modeling: a staging column rename touches one view.
  - The macro override is the documented dbt mechanism, and it is six lines.
  - The suffix lets pytest build `stg_test` and `marts_test` from `staging_test` with the same project and profile. The default run is unaffected.
- **Consequences:**
  - The schema names are global. Two people building into one database would collide, because there is no per-developer prefix. That is fine for a single-user pipeline.
  - Nodes without a custom schema (dbt tests) resolve to the profile schema, `marts`.
  - Views over staging survive `load_staging`, because it truncates in place ([D-022](#d-022-staging-reloads-by-truncate-and-copy-drop-and-recreate-only-on---rebuild)). `tests/test_dbt.py` checks that the view oids are unchanged after a reload.
- **Source:** `dbt/dbt_project.yml`, `dbt/macros/generate_schema_name.sql`, `dbt/models/staging/`; [`etl/03-dbt-star-schema.md`](etl/03-dbt-star-schema.md).

## D-028: Star schema with natural keys and no date dimension

- **Date:** 2026-10-31
- **Decision:**
  - Dimensions: `dim_team`, `dim_user`, `dim_sprint`, `dim_issue`.
  - Facts:
    - `fct_status_transitions`: one row per status change;
    - `fct_sprint_membership`: one row per issue joining a sprint, with `added_at`;
    - `fct_capacity`: one row per team and sprint.
  - Keys are the source system's natural keys (Jira IDs, and team key plus sprint ID for capacity). No surrogate keys. No date dimension in v1.
- **Context:** the grain was locked earlier (CLAUDE.md, "Star schema grain"). Session 4 metrics compare exact timestamps with sprint boundaries ([D-012](#d-012-sprint-metrics-compare-exact-timestamps)).
- **Options considered:**
  - **Keys.**
    - Surrogate keys (hashed or sequence): they protect against key reuse across sources and support slowly changing dimensions. There is one source here and no history of dimension changes.
    - Natural keys (chosen).
  - **Date dimension.**
    - A calendar table, the usual choice for BI filtering by week or month;
    - none in v1 (chosen).
  - **Membership.**
    - A sprint ID on the issue dimension: loses `added_at`, and can't hold more than one sprint per issue.
    - A separate fact (chosen).
- **Why:**
  - Natural keys can be read in any query and joined back to staging and `raw_data/` without a lookup. Every key is already unique and stable in the source.
  - The metrics need instants, not calendar attributes. A date dimension was already listed as a cut candidate (CLAUDE.md, "If behind schedule").
- **Consequences:**
  - A real Jira migration that renumbers IDs would need surrogate keys.
  - Power BI date slicing will need a date table, built in the report or added in v1.1.
- **Source:** [`etl-plan.md`](etl-plan.md) Session 3; [`etl/03-dbt-star-schema.md`](etl/03-dbt-star-schema.md).

## D-029: A user's team is derived from the issues assigned to them

- **Date:** 2026-10-31
- **Decision:** `dim_user.team_id` is the team of the issues assigned to the user. A test checks that every user maps to exactly one team.
- **Context:**
  - The API exposes no user-to-team link, so staging has none ([`etl/02-staging.md`](etl/02-staging.md)).
  - The domain model has exactly one team per user, a stated v1 limitation ([`domain-model.md`](domain-model.md), "Known v1 limitations").
  - In the shipped data, all 530 issues have an assignee, and each of the 12 users is assigned issues in one team only.
- **Options considered:**
  - no team on users;
  - a hand-written mapping (a seed file): a second source of truth to keep in sync;
  - the most frequent team among assigned issues: hides a user who works across teams;
  - the team of assigned issues, with a test that there is exactly one (chosen).
- **Why:** it uses only data the API returns, and it turns the v1 limitation into a check. If a user is ever assigned issues in two teams, the test fails instead of a team being picked silently.
- **Consequences:**
  - A user with no assigned issues would get no team and fail the test. There is none today.
  - Reporters and changelog authors are not used for the derivation.
- **Source:** `dbt/models/marts/dim_user.sql` (step 3.2); [`etl/03-dbt-star-schema.md`](etl/03-dbt-star-schema.md).

## D-030: A transition's sprint is the window it happened in (`event_sprint_id`)

- **Date:** 2026-10-31
- **Decision:**
  - `fct_status_transitions.event_sprint_id` is the sprint of the issue's team whose window contained the transition.
  - The window is half-open: `start_at <= transitioned_at < end_at`. One sprint's end is the next one's start, so a boundary instant matches the later sprint only.
  - A dbt test checks that every transition matches exactly one window.
  - The issue's own sprint stays in `fct_sprint_membership`.
- **Context:** the transition fact joins all four dimensions, so it needs a sprint key. In the shipped data the two candidates differ on 216 of 1,488 transitions. All 216 happen after the issue's membership sprint ended: unfinished work keeps moving, and v1 has no carryover ([`domain-model.md`](domain-model.md), "Known v1 limitations"). The breakdown is in [`etl/03-dbt-star-schema.md`](etl/03-dbt-star-schema.md).
- **Options considered:**
  - **A: by time window** (chosen, confirmed by Yassine at the 3.2 review).
  - **B: by membership,** the sprint the issue belongs to. It would label work done in sprint N+1 as sprint N, and it duplicates rows as soon as an issue joins two sprints.
  - **A closed window** (`<= end_at`). A transition exactly on a boundary would match two sprints.
- **Why:**
  - Every transition has exactly one window, and the join can't duplicate rows.
  - It answers "what happened during this sprint", which time-in-status per sprint needs.
  - Membership questions (committed, scope added, completed) still use `fct_sprint_membership` and the issue's `resolved_at`, as the answer key does.
- **Consequences:**
  - A transition outside every sprint (before the first sprint, after the last one, or in a gap between sprints) gets a null `event_sprint_id`. It fails `not_null` and the window test rather than being dropped. The shipped data has none.
  - Overlapping sprints fail the window test and duplicate rows, which `unique` on `changelog_id` catches.
  - The column name says what it is, so it is not mistaken for the issue's sprint.
- **Source:** `dbt/models/marts/fct_status_transitions.sql`, `dbt/tests/assert_each_transition_in_exactly_one_sprint_window.sql`.

## D-031: No dbt packages in v1

- **Date:** 2026-10-31
- **Decision:** the project uses dbt's built-in generic tests (`unique`, `not_null`, `relationships`, `accepted_values`), plus its own tests in `dbt/tests/`. It has no `packages.yml`.
- **Context:** the composite grains (`fct_sprint_membership`, `fct_capacity`) need a uniqueness test on two columns. dbt has no built-in test for that.
- **Options considered:**
  - `dbt_utils.unique_combination_of_columns`, the usual choice. It needs `packages.yml`, a `dbt deps` step with network access in every clone and in CI, and a version pin to maintain.
  - `unique` on a concatenated expression: works, but the generated test name and its failure output are hard to read.
  - A 10-line generic test, `dbt/tests/generic/unique_combination.sql` (chosen).
- **Why:** one small test doesn't justify a dependency and an extra install step. The SQL is short enough to read in an interview.
- **Consequences:**
  - If later sessions need several dbt_utils macros, adding the package is the better trade. `dbt/dbt_packages/` is already gitignored.
  - Singular tests cover what the generic ones can't:
    - every transition in exactly one window;
    - the transition fact joining every dimension once;
    - facts and their sprints sharing a team.
- **Source:** `dbt/tests/`.

## D-032: Sprint `complete_at` is reconciled against a derived value

- **Date:** 2026-10-31
- **Decision:**
  - `tests/test_staging_reconciliation.py` compares `staging.sprints.complete_at` with `end_date` at 09:00 UTC for closed sprints, and with null otherwise.
  - It also compares each sprint's state.
  - `complete_at` is documented as derived and unused by any metric.
- **Context:**
  - Session 2 left `complete_at` unreconciled, because `raw_data/sprints.json` has no completion field.
  - The mock API sets `completeDate` = `endDate` for closed sprints (`mock_api/serializers.py`, `_sprint_dates`).
  - Step 3.4 asked for the check. The gap was found at step 3.1, and Yassine accepted the derived rule at the 3.2 review, adding the state comparison.
- **Options considered:**
  - leave `complete_at` unchecked;
  - check it in dbt (dbt can't read `raw_data/*.json` without loading it as a seed);
  - compare it, in the existing independent reconciliation, with the rule restated from the contract (chosen).
- **Why:**
  - It catches a parsing or loading error in `complete_at`, for example a shifted `+00:00` offset or a wrong column, with the same rule as the other timestamps ([D-024](#d-024-reconciliation-truncates-raw-timestamps-to-milliseconds)).
  - It does not import the serializer, so the check stays independent.
- **Consequences:**
  - The check can't detect a wrong completion instant in the ground truth, because none exists.
  - Metrics use `end_at`, never `complete_at`, so the derivation affects no reported figure.
  - If a later simulator records real completion times, this rule must change.
- **Source:** `tests/test_staging_reconciliation.py` (`sprint_complete_at`, `test_sabotage_3_...`); [`etl/03-dbt-star-schema.md`](etl/03-dbt-star-schema.md).

## D-033: Metrics are dbt SQL models, built as tables in `marts`

- **Date:** 2026-11-01
- **Decision:**
  - Sprint metrics and the six business questions are SQL models in `dbt/models/metrics/`, built as tables in schema `marts`, next to the star schema.
  - They are named `rpt_<subject>`: `rpt_sprint_metrics` (step 4.1), then one or more `rpt_q<N>_<subject>` models per business question.
  - Each one has a documented grain, carries the team name and the sprint name or number, and needs no further join in the BI tool. Every model and column has a description in its `schema.yml`.
- **Context:** Session 4 builds the layer the dashboard reads. Every figure must be traceable to the star schema and testable with the same tools.
- **Options considered:**
  - **Python (pandas) metrics** writing tables. Ruled out by Yassine at session start: a second transformation tool, outside dbt's lineage and tests.
  - **Measures in Power BI (DAX).** The figures would exist only inside the BI file, with no reconciliation against the answer key and nothing to review in git.
  - **Views instead of tables.** Cheap to build, but every dashboard refresh would recompute them, and a broken upstream table would show up only when someone opens the report. Tables are rebuilt and tested by `dbt build`.
  - **A separate `metrics` schema.** Ruled out by Yassine at session start: `marts` is the one schema the dashboard reads.
  - **Prefix.** `fct_` would suggest an event grain, but these are aggregates. `mart_` repeats the schema name. `rpt_` says "reporting model, ready to read".
  - **The `marts/` folder.** A separate `metrics/` folder keeps the two layers visible ("dbt star schema → business-question models" in `CLAUDE.md`), at the cost of three config lines in `dbt_project.yml`.
- **Why:** dbt already holds the star schema and its tests. Keeping the metrics there gives one lineage graph, one test runner and SQL a reviewer can read.
- **Consequences:**
  - `dbt build` rebuilds and tests the metrics with everything else.
  - The dashboard reads tables with no joins, so the BI file holds presentation only.
  - The model names are a contract for the dashboard. Renaming one later is a breaking change.
- **Source:** `dbt/dbt_project.yml`, `dbt/models/metrics/`; [`etl/04-metrics.md`](etl/04-metrics.md).

## D-034: Sprint metrics read the membership fact and `end_at`, reconciled with `sprint_report.py`

- **Date:** 2026-11-01
- **Decision:**
  - `rpt_sprint_metrics` takes a sprint's issues from `fct_sprint_membership`.
  - Committed is `added_at <= start_at`, scope added `added_at > start_at`, and completed `resolved_at <= end_at`, all exact instants ([D-012](#d-012-sprint-metrics-compare-exact-timestamps)).
  - It reports every value the answer key reports: committed, scope added, completed, hit (`completed >= committed`), bug count and issue count.
  - `tests/test_metrics.py` calls `compute_sprint_report` on `raw_data/` and requires equality for all 36 sprints, on all six values.
- **Context:** `simulator/sprint_report.py` is the answer key. A mismatch is a bug in one side, never a rounding detail.
- **Options considered:**
  - **Calendar days** (`added_at::date <= start_date`). The original bug: it changes 17 of the 36 sprints in the shipped data. It is the sabotage case.
  - **`complete_at` for the end.** It is derived from the scheduled end and reconciled only against that rule ([D-032](#d-032-sprint-complete_at-is-reconciled-against-a-derived-value)). `end_at` is the instant the answer key uses.
  - **`event_sprint_id` to pick a sprint's issues.** It places a transition in the sprint it happened in, and 216 transitions fall after their issue's sprint ended ([D-030](#d-030-a-transitions-sprint-is-the-window-it-happened-in-event_sprint_id)). The answer key counts membership.
  - **The "→ Done" transition time for completed.** Equivalent in the data, but the answer key reads `resolutiondate`, so `resolved_at` keeps the two sides comparable line by line.
  - **Expected values from a hand-written Python formula.** Calling the answer key itself means the test can't agree with the warehouse on a shared misreading of the definitions.
- **Why:** the answer key defines the figures, and the reconciliation must compare against it, not against a restatement of it.
- **Consequences:**
  - Completed counts every member resolved by the end, scope-added issues included, as the answer key does. So a sprint can be hit while committed work is unfinished.
  - Work finished after its sprint ended counts in no sprint (no carryover): 65 issues and 343 story points in the shipped data. Velocity reads lower than real throughput.
  - Calling the answer key imports `simulator/` code into the test. The import is read-only: the answer key is never edited to make a check pass.
- **Source:** `dbt/models/metrics/rpt_sprint_metrics.sql`, `tests/test_metrics.py`; [`etl/04-metrics.md`](etl/04-metrics.md).

## D-035: The millisecond floor is checked at sprint boundaries, not tolerated

- **Date:** 2026-11-01
- **Decision:** a test reads `raw_data/` and counts raw membership `added_at`, issue `resolutiondate` and transition timestamps that fall strictly inside the first millisecond after any sprint boundary. The count must be 0. It is 0 in the shipped data.
- **Context:**
  - The warehouse holds timestamps floored to milliseconds ([D-024](#d-024-reconciliation-truncates-raw-timestamps-to-milliseconds)). The answer key compares raw microsecond values.
  - Sprint boundaries are whole seconds (09:00 UTC). Flooring moves a value onto a boundary `b` only if it lies in `(b, b + 1 ms)`. That flips `> b` into `<= b`: a scope add would become committed, or work resolved just after the end would count as completed. Values exactly on `b` stay on `b`, and every other value keeps its side.
- **Options considered:**
  - **Assume it never happens.** It was checked once during planning, but a new seed could break it with nothing to say so.
  - **Load microseconds into the warehouse.** The API emits milliseconds, so the warehouse cannot know them. That would mean reading `raw_data/` in the pipeline.
  - **A tolerance in the reconciliation.** It would hide real off-by-one bugs too.
  - **A check on the raw side** (chosen), with a sabotage case: a value 0.5 ms after a sprint start is caught, and flooring it flips it from scope added to committed.
- **Why:** it proves, rather than assumes, that the exact reconciliation is not passing by luck, and it fails loudly on the first dataset where it would.
- **Consequences:**
  - Shipped data: 13 distinct boundary instants. 0 of 320 `added_at`, 0 of 313 `resolutiondate` and 0 of 1,488 transition timestamps fall in the first millisecond after one. 244 `added_at` values sit exactly on a sprint start (committed at planning), which flooring leaves unchanged.
  - If the check ever fails, the fix belongs in the contract (for example, emitting microseconds), not in a tolerance.
- **Source:** `tests/test_metrics.py` (`test_no_raw_timestamp_falls_in_the_first_millisecond_after_a_sprint_boundary`, `test_sabotage_a_value_half_a_millisecond_after_a_boundary_is_caught`).

## D-036: The as-of instant is derived from the data

- **Date:** 2026-11-01
- **Decision:**
  - `marts.rpt_data_as_of` holds one row: the latest issue creation, issue resolution, status transition or sprint join.
  - Q3 measures "open and overdue" at its date.
  - It is recomputed on every build, and no document or model hardcodes it. This amends [D-020](#d-020-breach-headline-is-resolved-only-bug-to-story-ratio-counts-all-created-issues), which quoted a fixed value.
- **Context:** an unresolved issue is overdue only relative to a point in time. The data has no "now" of its own, and a fixed date would silently go stale with a new seed or a longer run.
- **Options considered:**
  - **A hardcoded instant** (D-020's 2024-07-05 15:35:28). Correct today, wrong for any other dataset.
  - **The wall-clock time of the build.** Every open issue would become overdue years after the simulated period.
  - **The last sprint's scheduled `end_at`** (2024-07-08 09:00). It is a plan, not an event: nothing happened after the Friday 15:35 event, and four issues due on 2024-07-05 would turn overdue over a weekend with no work in the data.
  - **The latest event timestamp** (chosen).
- **Why:** the as-of instant should be the moment the data describes, and moving one event moves it (`test_the_as_of_instant_follows_the_data`).
- **Consequences:**
  - Shipped data: 2024-07-05 15:35:28.261 UTC (the raw 15:35:28.261283, floored to the millisecond, D-024). 3 issues are open and overdue, 4 open and not yet due.
  - Q4's still-open stays (step 4.3) can use the same instant.
- **Source:** `dbt/models/metrics/rpt_data_as_of.sql`; [`metrics/q3-breach-rate.md`](metrics/q3-breach-rate.md).

## D-037: Available capacity is headcount × 10 working days − PTO days

- **Date:** 2026-11-01
- **Decision:**
  - Q2's available person-days = `headcount × 10 − pto_days`, with 10 as the dbt var `sprint_working_days`.
  - Velocity is divided by it.
  - A dbt test fails any capacity row with PTO below 0 or above `headcount × 10`.
- **Context:** `/capacity` serves `headcount` and `pto_days` but no capacity figure, and the docs gave no formula. Yassine chose this one at the 4.1 review, on condition that it matches how the simulator itself turns headcount and PTO into capacity.
- **Options considered:**
  - **Headcount only.** It ignores the absence the data was built to show.
  - **Hours** (`× 8`). The same ratio scaled by 8, in a unit `/capacity` doesn't use.
  - **Planned PTO** (`average_pto_days_per_sprint`). This is what the simulator plans with, but it is a simulator parameter, absent from the API and the warehouse.
  - **Person-days with actual PTO** (chosen).
- **Why:**
  - Checked read-only in `simulator/sprint_cycle.py`: the simulator sizes commitment from `headcount × SPRINT_LENGTH_HOURS − pto × WORK_HOURS_PER_DAY`, where `SPRINT_LENGTH_HOURS = 8 × 10`. That is the same formula in hours, with the same 10 working days.
  - The simulator plans on *average* PTO, while the PTO it records is the PTO drawn for the sprint. Q2 uses the recorded value, which is what "available" means.
- **Consequences:**
  - Points per person-day reflects the realization factor divided by the hours a point costs, so it compares a team with itself, not teams with each other ([`metrics/q2-velocity-vs-capacity.md`](metrics/q2-velocity-vs-capacity.md)).
  - The drawn PTO can partly fall in the next sprint (the absence starts at a random point). Q2 charges it all to the sprint it was recorded for.
- **Source:** `dbt/models/metrics/rpt_q2_velocity_vs_capacity.sql`, `dbt/tests/assert_pto_within_sprint_person_days.sql`, `dbt/dbt_project.yml`.

## D-038: Rolling figures cover the sprint and the two before it

- **Date:** 2026-11-01
- **Decision:**
  - The 3-sprint rolling figures cover the current sprint and the two before it, in the same team, by sprint number.
  - Sprints 1 and 2 use the sprints that exist, and a `sprints_in_window` column says how many.
  - Q1 averages velocity. Q2 divides summed points by summed person-days (capacity-weighted).
- **Context:** the rolling average is a trend line for the dashboard, and a team has only 12 sprints.
- **Options considered:**
  - **A centred window** (the sprint before and the sprint after). It uses the future, so it is not usable as a running trend.
  - **Null until three sprints exist.** It hides a sixth of each chart, while the column already says how full the window is.
  - **For Q2, the mean of the three ratios.** It gives a short-staffed sprint the same weight as a full one.
- **Why:** a trailing window is what a team sees at its retrospective. Exposing the window size lets the dashboard filter to full windows instead of the model deciding.
- **Consequences:** the first two rolling values are less stable. Filter on `sprints_in_window = 3` for full windows.
- **Source:** `dbt/models/metrics/rpt_q1_velocity.sql`, `rpt_q2_velocity_vs_capacity.sql`.

## D-039: Breach rates ship as a table with labelled rollups, plus an issue-level table

- **Date:** 2026-11-01
- **Decision:**
  - `rpt_q3_breach_rate` holds one row per team × issue type × commitment, built with `group by cube`. Its rollup rows are labelled "All teams", "All types" and "All", and each row carries its outcome counts and both rates.
  - `rpt_q3_deadline_outcomes` holds one row per issue with a due date, for drill-down.
  - These replace the two models proposed at 4.1 (`rpt_q3_breach_rate_by_type`, `rpt_q3_breach_rate_by_team`).
- **Context:**
  - A rate can't be summed or averaged across groups.
  - The question asks for breach by issue type and by team, and the domain model asks for it split by commitment.
  - The dashboard must not recompute anything ([D-033](#d-033-metrics-are-dbt-sql-models-built-as-tables-in-marts)).
- **Options considered:**
  - **Two models, by type and by team.** They duplicate the logic, and the commitment split would need a third.
  - **Issue-level rows only, with rates as DAX measures.** The figures would then live in the BI file and go unreconciled.
  - **Labelled rollups** (chosen), with a test that every rollup equals the sum of its parts.
- **Why:** every slice the question needs is a filter on one table, and the headline is one row per type.
- **Consequences:**
  - Summing across levels double-counts. The model description and the Q3 page say so.
  - Cells with no due issue have no row (48 in the shipped data).
- **Source:** `dbt/models/metrics/rpt_q3_breach_rate.sql`, `rpt_q3_deadline_outcomes.sql`, `dbt/tests/assert_breach_rate_rollups_add_up.sql`.

## D-040: Time-based metrics use calendar hours, exclude Epics, and keep open stays out of the statistics

- **Date:** 2026-11-01
- **Decision:**
  - Q4 measures each stay in a status in calendar hours, nights and weekends included.
  - Epics are excluded from every time-based metric.
  - Statistics (mean, median, 90th percentile) cover completed stays only. Still-open stays are counted and their age reported separately. Done is terminal and has no statistics.
- **Context:**
  - Time per status needs a clock, a population and a rule for stays that haven't ended.
  - In the shipped data, 187 of the 1,988 stays are open: 180 are To Do stays of never-scheduled issues, 5 In Progress and 2 QA.
- **Options considered:**
  - **Clock:** working hours (8 a day, weekdays) need a calendar of working days and hours, which v1 doesn't have ([D-028](#d-028-star-schema-with-natural-keys-and-no-date-dimension)). Calendar hours (chosen) need none, and they measure elapsed waiting time.
  - **Epics:** kept with their single creation-to-now stay, or excluded (chosen). They never change status, so including them would only add backlog age.
  - **Open stays:**
    - closed at the as-of instant (a lower bound mixed with real durations, biasing every statistic down by an amount set by when the data ends);
    - dropped silently;
    - a survival estimate (Kaplan–Meier), the correct treatment of censored durations, but heavy for 7 in-process stays;
    - counted and aged separately (chosen).
- **Why:** the statistics describe only durations that are known, and nothing is hidden: the count and age of open stays sit next to them.
- **Consequences:**
  - A weekend inside a stay adds 48 hours, and To Do includes backlog wait before planning (median 912 h).
  - If a later version has many long-running open stays, a survival estimate becomes worth its complexity.
  - A bounce back to a status is another stay, and `visit_number` tells first visits from repeat ones.
- **Source:** `dbt/models/metrics/rpt_q4_status_stays.sql`, `rpt_q4_time_in_status.sql`; [`metrics/q4-time-per-status.md`](metrics/q4-time-per-status.md).

## D-041: Q5 counts issues by the sprint window they were created in

- **Date:** 2026-11-01
- **Decision:**
  - The bug-to-story ratio is reported per team for 13 periods: the team's 12 sprint windows (`start_at <= created_at < end_at`), plus period 0, "Before Sprint 1".
  - Each row also carries the cumulative ratio.
- **Context:** the issues were created between 2024-01-08 and 2024-07-05. That is 26 weeks: a two-week backlog build-up, then 12 two-week sprints.
- **Options considered:**
  - **Weeks:** 26 points, with Atlas averaging 0.5 Bugs a week, so mostly zeros.
  - **Calendar months:** a partial first month (24 days) and a 5-day last one.
  - **Sprint windows** (chosen): 13 equal periods covering the whole span, on the same axis as Q1 and Q2.
- **Why:** equal-length periods with no partial ends, and enough issues per period to read the trend, especially through the cumulative ratio.
- **Consequences:**
  - An issue created outside every window would be lost. The dbt test `assert_q5_counts_every_created_issue` catches it.
  - The millisecond boundary check ([D-035](#d-035-the-millisecond-floor-is-checked-at-sprint-boundaries-not-tolerated)) now covers creation times too: 0 of 530.
  - Per-period ratios stay noisy for Atlas (1 to 7 Stories per period).
- **Source:** `dbt/models/metrics/rpt_q5_bug_to_story_ratio.sql`; [`metrics/q5-bug-to-story.md`](metrics/q5-bug-to-story.md).

## D-042: The portfolio reads Q1 to Q5 and normalizes only size-dependent figures

- **Date:** 2026-11-01
- **Decision:**
  - `rpt_q6_team_portfolio` has one row per team, and every value comes from `rpt_sprint_metrics` or the Q1 to Q5 models.
  - Capacity use is completed points per available person-day, summed over all sprints before dividing.
  - Rates, ratios and medians are not normalized further.
- **Context:** the question asks for a fair comparison, "per available person-day where applicable". The models for each question already exist and are reconciled.
- **Options considered:**
  - **Recompute in the portfolio:** two definitions of each figure that could drift apart.
  - **Divide every figure by person-days:** a breach rate or a median per person-day has no meaning.
  - **Mean of the sprint ratios for capacity use:** weighs a short-staffed sprint like a full one. It is the sabotage case.
  - **"Capacity use" as the share of nominal capacity left after PTO:** reported as `pto_share` for context, but it measures absence, not use.
- **Why:** one source per number, and normalization only where team size changes the figure.
- **Consequences:**
  - Points are team-relative (a point costs 3.5 to 6 hours across teams), so capacity use is not comparable in effort. The page states this and shows the size of the effect with simulator-only parameters.
  - Changing a Q1 to Q5 definition changes the portfolio with it.
- **Source:** `dbt/models/metrics/rpt_q6_team_portfolio.sql`; [`metrics/q6-portfolio.md`](metrics/q6-portfolio.md).
