# 03. dbt star schema

Status: in progress (Session 3). Done so far:

- **Step 3.1:** the dbt project, the profile, the staging sources and the 8 `stg_` views.
- **Step 3.2:** the four dimensions in `marts`, with unique, not null, accepted-value and relationship tests.
- **Step 3.3:** the three facts, with relationship, accepted-value, composite-grain and window tests.

Remaining: data quality tests and the final docs (3.4). The plan is in [`../etl-plan.md`](../etl-plan.md).

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

**Dimensions** (schema `marts`, tables, rebuilt by every `dbt build`). Keys are natural Jira keys, with no surrogate keys ([D-028](../decisions.md#d-028-star-schema-with-natural-keys-and-no-date-dimension)).

| Table | Grain and key | Columns | Built from | Rows |
|---|---|---|---|---|
| `dim_team` | one row per team; `team_id` (Jira project ID) | `team_key` (unique, used by capacity), `team_name` | `stg_teams` | 3 |
| `dim_user` | one row per Jira account; `user_id` | `display_name`, `team_id` | `stg_users`, team from `stg_issues` | 12 |
| `dim_sprint` | one row per sprint; `sprint_id` | `team_id`, `board_id`, `sprint_name`, `sprint_state`, `start_at`, `end_at`, `complete_at` | `stg_sprints`, team from `stg_boards` by `board_id` | 36 |
| `dim_issue` | one row per issue; `issue_id` | `issue_key` (unique), `issue_type`, `team_id`, `current_status_id`, `current_status_name`, `assignee_user_id`, `reporter_user_id`, `parent_issue_id`, `story_points`, `created_at`, `resolved_at`, `duedate`, `summary` | `stg_issues` | 530 |

- **A user's team** ([D-029](../decisions.md#d-029-a-users-team-is-derived-from-the-issues-assigned-to-them)). `dim_user.team_id` is the team of the issues assigned to the user when there is exactly one such team, and null otherwise. A `not_null` test on it then fails. So a user assigned issues in two teams, or in none, is reported, never resolved by picking one. In the shipped data each user maps to one team: Phoenix 5, Nomad 4, Atlas 3, the simulator's headcounts. A test checks the derived team against `raw_data/users.json`.
- **Boards** are not a dimension. In v1 each team has one board, and the board only links a sprint to its team, so `dim_sprint` carries `board_id` and the board's `team_id`.
- **Status on `dim_issue`** is the current status, renamed `current_status_*` so it is not confused with the transition history (step 3.3).

**Tests on the dimensions** (`dbt/models/marts/_marts__dimensions.yml`), 31 in all:

- **`unique` and `not_null`:** every primary key, plus `team_key` and `issue_key`.
- **`not_null`:** names, `issue_type`, `created_at`, `current_status_name`, `dim_sprint.board_id`, and both derived `team_id`s (user and sprint).
- **`relationships`:**
  - `team_id` on the user, sprint and issue dimensions → `dim_team`;
  - `assignee_user_id` and `reporter_user_id` → `dim_user`;
  - `parent_issue_id` → `dim_issue`.
- **`accepted_values`:**
  - `issue_type`: Epic, Story, Task, Bug;
  - `current_status_name`: the five fixed statuses;
  - `sprint_state`: future, active, closed.

Generic tests take their arguments under `arguments:`, the dbt 1.10+ form, so dbt raises no deprecation warning.

**Facts** (schema `marts`, tables).

| Table | Grain and key | Columns | Built from | Rows |
|---|---|---|---|---|
| `fct_status_transitions` | one row per status change; `changelog_id` | `issue_id`, `team_id` (the issue's), `author_user_id`, `event_sprint_id`, `transitioned_at`, `from_status_id`, `from_status_name`, `to_status_id`, `to_status_name` | `stg_status_transitions`, `stg_issues`, `dim_sprint` | 1,488 |
| `fct_sprint_membership` | one row per issue joining a sprint; (`issue_id`, `sprint_id`) | `added_at`, `changelog_id` (unique) | `stg_sprint_memberships` | 320 |
| `fct_capacity` | one row per team and sprint; (`team_id`, `sprint_id`) | `team_key`, `headcount`, `pto_days` | `stg_capacity`, `team_id` from `dim_team` by `team_key` | 36 |

**`event_sprint_id`** ([D-030](../decisions.md#d-030-a-transitions-sprint-is-the-window-it-happened-in-event_sprint_id)) is the sprint of the issue's team whose window contained the transition.

- **The window** is half-open, `start_at <= transitioned_at < end_at`. One sprint's `end_at` is the next one's `start_at`, so a boundary instant belongs to the later sprint only.
- **Not the issue's sprint.** That stays in `fct_sprint_membership`.
- **Left joins.** The fact uses left joins, so a transition with no issue or no window keeps its row with a null key, which fails a test.

**Where `event_sprint_id` differs from the membership sprint.** Measured on the shipped data before the fact was built: 216 of the 1,488 transitions, all of them on the 320 scheduled issues. The 180 never-scheduled issues have no transitions.

| Category | Transitions | Issues |
|---|---|---|
| After the membership sprint ended | 216 | 66 |
| Before the issue joined its sprint | 0 | 0 |
| Before the membership sprint started | 0 | 0 |
| In a gap (no window) or two windows | 0 | 0 |

The 216, cut other ways:

- **How far:** 208 land in the next sprint and 8 (4 issues) two sprints later.
- **To status:** QA 70, Done 65 (65 issues), Code Review 61, In Progress 20. So 65 of the 66 issues reached Done in a later sprint.
- **Membership:** 138 transitions belong to scope-added issues (39) and 78 to committed ones (27).
- **Team:** PHX 100 (30 issues), NMD 59 (18), ATL 57 (18).
- **Type:** Story 112, Task 66, Bug 38.

This is the "no carryover" v1 limitation ([`../domain-model.md`](../domain-model.md), "Known v1 limitations"): an unfinished issue stays a member of its original sprint while its work continues in the next one. Nothing in the breakdown was unexpected.

**Tests on the facts** (`dbt/models/marts/_marts__facts.yml`, `dbt/tests/`), 34 in all:

- **`unique` and `not_null`:** `changelog_id` on both changelog facts.
- **`unique_combination`** ([D-031](../decisions.md#d-031-no-dbt-packages-in-v1), `dbt/tests/generic/unique_combination.sql`): (`issue_id`, `sprint_id`) and (`team_id`, `sprint_id`).
- **`not_null` and `relationships`:** every foreign key:
  - issue → `dim_issue`;
  - team → `dim_team`;
  - author → `dim_user`;
  - `event_sprint_id` and `sprint_id` → `dim_sprint`.
- **`not_null`:** `transitioned_at`, `added_at`, `team_key`, `headcount`, `pto_days`.
- **`accepted_values`:** `from_status_name` and `to_status_name`, the five fixed statuses.
- **Singular tests** (they return the offending rows):
  - `assert_each_transition_in_exactly_one_sprint_window`: 0 windows is a gap, 2 is overlapping sprints.
  - `assert_transitions_join_every_dimension_once`: the inner join to all four dimensions has as many rows as the fact.
  - `assert_fact_teams_match_their_sprint`: a membership's issue and sprint share a team, and so do a capacity row and its sprint.
- **`author_user_id` is `not_null`** although Jira allows a null author (automation). The shipped data has none, and a null would drop the row from the inner join to `dim_user`.

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

`uv run --env-file .env python -m pytest tests/test_dbt.py -rs`: 15 tests, 0 skipped.

- **Isolation.** The Postgres tests load staging into `staging_test` and run dbt with the test vars, so dbt builds `stg_test` (and later `marts_test`). All three schemas are dropped before and after, and `staging`, `stg` and `marts` are never touched. dbt's target and log files go to a temporary directory.
- **No `.env`.** dbt runs from that temporary directory, so it finds no `.env` and depends on the process environment alone.
- **Sabotage runs** use `dbt run`, then `dbt test`, and compare the exact set of failed tests. `dbt build` skips everything downstream of a failing test. For example, a failed window test stops `fct_status_transitions` from being built, which is what the pipeline wants, but it would hide the other tests a sabotage should fail.

| Test | What it proves |
|---|---|
| `test_profile_takes_every_connection_value_from_the_environment` | host, port, user, password and dbname in `dbt/profiles.yml` are all `env_var('POSTGRES_*')`. |
| `test_dbt_debug_connects_with_the_environment` | `dbt debug` passes ("All checks passed!"). |
| `test_sabotage_a_wrong_credential_fails_dbt_debug_clearly` | **Sabotage:** a wrong `POSTGRES_PASSWORD` fails `dbt debug` with `password authentication failed`. Removing `POSTGRES_HOST` fails it with `Env var required but not provided: 'POSTGRES_HOST'`. |
| `test_stg_views_build_count_and_survive_a_staging_reload` | `dbt build --select staging` creates exactly the 8 views, with the shipped counts. After a normal `load_staging`, the views are the same objects (same oids) with the same counts. |
| `test_dbt_never_writes_outside_the_test_schemas` | With the test vars, every `staging/` model is in `stg_test`, every `marts/` model in `marts_test`, and the source in `staging_test`. |
| `test_dimensions_build_with_the_shipped_counts_and_pass_their_tests` | A full `dbt build` passes with no failed test. The dimensions are tables with 3, 12, 36 and 530 rows. |
| `test_derived_user_teams_equal_raw_data` | Each user's derived team is their team in `raw_data/users.json`, matched by display name and team key. |
| `test_sabotage_a_deleted_staging_user_fails_the_user_relationships` | **Sabotage:** deleting "Phoenix Dev 1" from staging fails exactly the tests pointing at `dim_user`: issue assignee and reporter, transition author, and the join to every dimension. After a reload, the build passes. |
| `test_sabotage_a_duplicated_staging_user_fails_unique` | **Sabotage:** staging's primary key refuses a duplicate user (`UniqueViolation`). With the key dropped, a duplicated row fails exactly `unique_dim_user_user_id` and the join test (that user's transitions double). |
| `test_sabotage_a_user_in_two_teams_fails_the_team_mapping` | **Sabotage:** moving one PHX issue to ATL gives its assignee two teams. Exactly two tests fail: `not_null_dim_user_team_id`, and `assert_fact_teams_match_their_sprint`, since the issue's PHX sprint membership now crosses teams. |
| `test_facts_build_with_the_shipped_counts_and_join_all_four_dimensions` | The facts have 1,488, 320 and 36 rows. The inner join to all four dimensions gives 1,488 rows and 1,488 distinct IDs. |
| `test_event_sprint_is_the_window_that_contains_the_transition` | `event_sprint_id` differs from the membership sprint on exactly 216 transitions, and for every one of them both the transition and its event sprint come after the membership sprint ended. |
| `test_a_transition_on_a_sprint_boundary_belongs_to_the_later_sprint` | A transition moved to exactly PHX Sprint 6's `start_at` (= Sprint 5's `end_at`) gets Sprint 6, and every test passes. |
| `test_sabotage_an_orphan_transition_fails_the_issue_relationship` | **Sabotage:** a transition with issue ID 999999999 fails the `issue_id` relationship test. So do the checks that need its issue: `not_null` on `team_id` and `event_sprint_id`, the window test and the join test. Five in all, nothing else. |
| `test_sabotage_overlapping_sprints_fail_the_window_test` | **Sabotage:** PHX Sprint 5 ending a day late overlaps Sprint 6. Exactly the window test and `unique` on `changelog_id` fail, since the overlapping day's transitions match two sprints. |

**Gate run for step 3.1**, on the real schemas, through the documented commands:

- **`dbt debug`:** "All checks passed!", exit 0.
- **`dbt build`:** `PASS=8 WARN=0 ERROR=0`. The 8 views are in `stg`.
- **Counts through the views:** 3 teams, 12 users, 3 boards, 36 sprints, 36 capacity, 530 issues, 1,488 transitions, 320 memberships.
- **Reload:** after `uv run --env-file .env python -m ingestion.load_staging`, the view names, oids and counts are identical.
- **Sabotage:**
  - `POSTGRES_PASSWORD=wrong` gives `Connection test: [ERROR]` and `password authentication failed for user "aap"`, exit 1.
  - Without `POSTGRES_HOST`, run outside the repo, it gives `Env var required but not provided: 'POSTGRES_HOST'`, exit 2.

**Gate run for step 3.2**, on the real schemas:

- **`dbt build`:** `PASS=43 WARN=0 ERROR=0` (8 views, 4 tables, 31 tests).
- **Counts:** `dim_team` 3, `dim_user` 12, `dim_sprint` 36, `dim_issue` 530. Every key is unique and not null, and every relationship holds.
- **Teams:** 0 users without exactly one team.
- **Sabotage, delete:** after deleting "Atlas Dev 2" from `staging.users`, `dbt build` fails exactly two tests:
  - `relationships_dim_issue_assignee_user_id__user_id__ref_dim_user_`, with 31 rows;
  - `relationships_dim_issue_reporter_user_id__user_id__ref_dim_user_`, with 39 rows.
- **Sabotage, duplicate:**
  - On the real `staging`, inserting a copy of a user row is refused: `duplicate key value violates unique constraint "users_pkey"`.
  - The pytest case above drops that key in `staging_test` and shows `unique_dim_user_user_id` failing.
- **Restore:** `load_staging`, then `dbt build`: `PASS=43` again.

**Gate run for step 3.3**, on the real schemas:

- **`dbt build`:** `PASS=80 WARN=0 ERROR=0` (8 views, 7 tables, 65 tests).
- **Counts:** `fct_status_transitions` 1,488, `fct_sprint_membership` 320, `fct_capacity` 36.
- **Join:** the inner join of the transition fact to `dim_issue`, `dim_team`, `dim_user` and `dim_sprint` returns 1,488 rows and 1,488 distinct `changelog_id`s. No rows are lost and none are duplicated.
- **Sabotage, orphan row:**
  - A copy of a transition with `issue_id` 999999999 was inserted into `staging.status_transitions`.
  - `dbt run` then `dbt test` gives `ERROR=5`: `relationships_fct_status_transitions_issue_id__issue_id__ref_dim_issue_`, `not_null_fct_status_transitions_team_id`, `not_null_fct_status_transitions_event_sprint_id`, `assert_each_transition_in_exactly_one_sprint_window` and `assert_transitions_join_every_dimension_once`, each with 1 row.
- **Restore:** `load_staging`, then `dbt build`: `PASS=80`.

## Known limits

- **Global schema names.** `stg` and `marts` have no per-developer or per-target prefix, so two people sharing a database would overwrite each other's models ([D-027](../decisions.md#d-027-two-dbt-layers-with-exact-schema-names-stg-views-and-marts-tables)).
- **dbt reads `.env` on its own** from the working directory. A missing variable can be filled from a file the command doesn't name ([D-026](../decisions.md#d-026-dbt-core-112-with-dbt-postgres-111-in-dbt-profile-from-the-environment)).
- **A staging schema change needs the views rebuilt.** `load_staging --rebuild` refuses while the `stg_` views depend on staging ([D-022](../decisions.md#d-022-staging-reloads-by-truncate-and-copy-drop-and-recreate-only-on---rebuild)). Drop the `stg` schema first, rebuild, then `dbt build`.

## Options rejected and why

- **dbt v2, dbt-core 1.13 (beta), a profile in `~/.dbt/`, literal or defaulted credentials:** [D-026](../decisions.md#d-026-dbt-core-112-with-dbt-postgres-111-in-dbt-profile-from-the-environment).
- **dbt's default schema concatenation, `stg_` as tables, tests on the real schemas or a second database:** [D-027](../decisions.md#d-027-two-dbt-layers-with-exact-schema-names-stg-views-and-marts-tables).
- **Surrogate keys and a date dimension:** [D-028](../decisions.md#d-028-star-schema-with-natural-keys-and-no-date-dimension).
- **A hand-written user-to-team mapping, or picking the most frequent team:** [D-029](../decisions.md#d-029-a-users-team-is-derived-from-the-issues-assigned-to-them).
