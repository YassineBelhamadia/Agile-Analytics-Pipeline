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
  - A separate open-and-overdue count at the last data timestamp, 2024-07-05 15:35:28.
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
