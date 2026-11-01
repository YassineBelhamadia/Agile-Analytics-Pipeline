# 04. Sprint metrics and business questions

Status: in progress (Session 4, [`../etl-plan.md`](../etl-plan.md)).

- **Step 4.1** (done): `rpt_sprint_metrics`, reconciled with `simulator/sprint_report.py` for all 36 sprints.
- **Step 4.2** (done): business questions 1 to 3 (velocity, velocity against capacity, breach rate), each recomputed from `raw_data/`.
- **Step 4.3:** business questions 4 to 6.

Each question has its own page in [`../metrics/`](../metrics/): what it asks, the formula, the assumptions, how it was verified, and an example result.

## Purpose

Turns the star schema ([03-dbt-star-schema.md](03-dbt-star-schema.md)) into the figures the dashboard shows. The figures are dbt SQL models, built as tables in `marts`, and every row is ready to read with no further join ([D-033](../decisions.md#d-033-metrics-are-dbt-sql-models-built-as-tables-in-marts)). The sprint figures must equal the simulator's answer key exactly. Everything else is built on them.

## Inputs and outputs

**Inputs:** the star schema in `marts` (`dim_team`, `dim_sprint`, `dim_issue`, `fct_sprint_membership`, `fct_status_transitions`, `fct_capacity`), and the dbt var `sprint_working_days` (10).

**Outputs:** tables in `marts`, replaced by every `dbt build` or `dbt run`. The models live in `dbt/models/metrics/`, which `dbt_project.yml` builds into `marts`.

## Schema or contract

### `rpt_sprint_metrics`

**Grain:** one row per sprint (`sprint_id`), 36 rows.

| Column | Type | Meaning |
|---|---|---|
| `sprint_id` | bigint | Jira sprint ID, primary key |
| `team_id`, `team_key`, `team_name` | bigint, text, text | the sprint's team |
| `sprint_name` | text | "Sprint 1" to "Sprint 12" |
| `sprint_number` | bigint | position in the team's sequence by `start_at`; a test checks it matches the name |
| `start_at`, `end_at` | timestamptz | the sprint's start and end instants, 09:00 UTC; one sprint's end is the next one's start |
| `committed_points` | numeric | story points of members that joined at or before `start_at` |
| `scope_added_points` | numeric | story points of members that joined after `start_at` |
| `completed_points` | numeric | story points of members resolved at or before `end_at` |
| `is_hit` | boolean | `completed_points >= committed_points` |
| `bug_count` | bigint | member issues of type Bug |
| `issue_count` | bigint | member issues, all types |

### Business-question models

Every model carries the team name, and the sprint name and number where its grain has a sprint. Each needs no further join. Each model and column is described in `dbt/models/metrics/_metrics__*.yml`.

| Model | Question | Grain | Rows (SEED=22) | Page |
|---|---|---|---|---|
| `rpt_data_as_of` | helper | one row: the last event instant | 1 | [q3](../metrics/q3-breach-rate.md) |
| `rpt_q1_velocity` | Q1 velocity | sprint | 36 | [q1-velocity.md](../metrics/q1-velocity.md) |
| `rpt_q2_velocity_vs_capacity` | Q2 velocity against capacity | sprint | 36 | [q2-velocity-vs-capacity.md](../metrics/q2-velocity-vs-capacity.md) |
| `rpt_q3_deadline_outcomes` | Q3 detail | issue with a due date | 320 | [q3-breach-rate.md](../metrics/q3-breach-rate.md) |
| `rpt_q3_breach_rate` | Q3 breach rate | team × issue type × commitment, with "All" rollups | 48 | [q3-breach-rate.md](../metrics/q3-breach-rate.md) |

### The definitions in plain words

- **Who is in a sprint.** The issues that joined it, from `fct_sprint_membership`. The membership is written by the Sprint item in each issue's changelog. An issue that is still unfinished when the sprint ends stays a member of that sprint only (no carryover in v1).
- **Committed:** the points the team signed up for at planning, the members already in the sprint at its start instant.
- **Scope added:** the points pulled in after the start.
- **Completed:** the points of members resolved by the end instant, whether they were committed or added. Work resolved after the end counts in no sprint.
- **Hit:** completed reaches committed. Completed includes scope-added work, so a sprint can be hit while committed work is unfinished, as in the answer key. The scope-added figure is reported next to it for that reason.

These are the formulas of the answer key, `simulator/sprint_report.py`, and of [`../domain-model.md`](../domain-model.md) ("Where commit vs. completion vs. scope-added actually comes from").

### Why exact timestamps

A sprint starts and ends at 09:00 UTC, and one sprint's end instant is the next sprint's start ([D-012](../decisions.md#d-012-sprint-metrics-compare-exact-timestamps)). Comparing calendar days instead was the original bug. It counted an issue added at 14:00 on the start day as committed, and work resolved at 15:00 on the end day, which is the next sprint's first morning, as completed in the sprint that had already ended. On the shipped data it changes 17 of the 36 sprints. The test suite keeps it as a sabotage case.

The model uses `end_at`, never `complete_at`, which is derived from the scheduled end ([D-032](../decisions.md#d-032-sprint-complete_at-is-reconciled-against-a-derived-value)). It uses membership, not `event_sprint_id`, which places a transition in the sprint it happened in ([D-034](../decisions.md#d-034-sprint-metrics-read-the-membership-fact-and-end_at-reconciled-with-sprint_reportpy)).

### The millisecond limit

The warehouse holds timestamps floored to milliseconds, because the API emits milliseconds ([D-024](../decisions.md#d-024-reconciliation-truncates-raw-timestamps-to-milliseconds)). The answer key compares the raw microsecond values. Sprint boundaries are whole seconds, so flooring can change a comparison only for a raw value strictly inside the first millisecond after a boundary. Such a value would floor onto the boundary, and a scope add would become committed, or work resolved just after the end would count as completed.

A test counts those values in `raw_data/` ([D-035](../decisions.md#d-035-the-millisecond-floor-is-checked-at-sprint-boundaries-not-tolerated)). Result on the shipped data, against the 13 distinct boundary instants:

| Raw timestamps | Values | Within the first ms after a boundary | Exactly on a boundary |
|---|---|---|---|
| membership `added_at` | 320 | 0 | 244 (committed at planning) |
| issue `resolutiondate` | 313 | 0 | 0 |
| transition `timestamp` | 1,488 | 0 | 0 |

Values exactly on a boundary are already whole milliseconds, so flooring leaves them there. The exact reconciliation therefore does not pass by luck. A dataset where the check fails needs a contract fix (for example, microseconds from the API), not a tolerance.

## How to run

From the repo root, with Postgres up and staging loaded ([02-staging.md](02-staging.md#how-to-run)):

```bash
uv run --env-file .env dbt build --project-dir dbt --profiles-dir dbt     # builds and tests everything, metrics included
uv run --env-file .env python -m pytest tests/test_metrics.py -rs         # reconciliation with the answer key
```

## How it is verified

### dbt tests on `rpt_sprint_metrics`

Step 4.1 added 20 tests (94 data tests in the project then, 74 before):

- `unique` and `not_null` on `sprint_id`, `not_null` on every column, `relationships` to `dim_sprint` and `dim_team`, and `expected_row_count` 36.
- **`assert_sprint_metrics_cover_every_membership`:** the sum of `issue_count` is the membership count, and committed plus scope added is every member's points. A membership counted twice, or in neither branch, fails it.
- **`assert_sprint_number_matches_sprint_name`:** `sprint_name` is "Sprint " plus `sprint_number`. A sprint out of order, or a gap, fails it.

### dbt tests on the business-question models (step 4.2)

Step 4.2 added 67 tests, for 161 in the project:

- `unique` and `not_null` on every key, `not_null` on every column that is always set, and the row counts above (`rpt_q3_breach_rate` has a `unique_combination` test on its three dimensions instead).
- `accepted_values` on `issue_type`, `commitment` and `outcome`.
- **`assert_pto_within_sprint_person_days`:** no capacity row has PTO below 0 or above `headcount × 10`.
- **`assert_breach_rate_rollups_add_up`:** every rollup row of `rpt_q3_breach_rate` is the sum of its parts along each dimension, and the grand total is all 320 due issues.

### Reconciliation with the answer key (pytest)

`tests/test_metrics.py`. The expected side runs `compute_sprint_report` on `raw_data/`, loaded with `simulator.persistence.load_from_json`. Nothing comes from `mock_api/`, `ingestion/` or the warehouse. Both sides meet on team key and sprint name. The Postgres tests build into `marts_test` from `staging_test`, as in Session 3, and never touch the real schemas.

| Test | What it proves |
|---|---|
| `test_answer_key_covers_every_sprint` | The answer key yields 36 sprints and 320 memberships from `raw_data/`. |
| `test_sprint_metrics_match_sprint_report_for_every_sprint` | `dbt build` passes, and every sprint's committed, scope added, completed, hit, bug count and issue count equal the answer key. Nothing is missing on either side. |
| `test_sabotage_calendar_days_fail_the_reconciliation` | **Sabotage:** in a copy of the dbt project, the three comparisons become calendar-day comparisons. Each replacement is asserted to happen once, so the sabotage can't silently miss. The reconciliation then differs on exactly 17 sprints. The real project is never edited. |
| `test_no_raw_timestamp_falls_in_the_first_millisecond_after_a_sprint_boundary` | The millisecond check above: 0 values for each of the three kinds. |
| `test_sabotage_a_value_half_a_millisecond_after_a_boundary_is_caught` | **Sabotage:** a scope add 0.5 ms after a sprint start is caught by the check, and flooring it would flip it to committed. |

### Business questions against `raw_data/` (pytest, step 4.2)

Same file. The expected side is plain Python on `raw_data/*.json`, without the answer key or any pipeline code. Details and sabotage cases are on each question's page.

| Test | What it proves |
|---|---|
| `test_q1_velocity_matches_raw_data` | Q1, all 36 sprints, all columns |
| `test_q1_late_points_are_the_documented_no_carryover_figures` | 65 issues and 343 points finished after their sprint: 28.1% of all completed |
| `test_sabotage_q1_a_four_sprint_window_fails` | **Sabotage:** a 4-sprint window makes 27 sprints differ, in the rolling columns only |
| `test_raw_pto_never_exceeds_the_sprints_person_days` | the PTO rule on the raw side |
| `test_q2_velocity_vs_capacity_matches_raw_data` | Q2, all 36 sprints, all columns |
| `test_sabotage_q2_ignoring_pto_fails` | **Sabotage:** capacity without PTO makes all 36 sprints differ |
| `test_sabotage_pto_above_the_sprints_person_days_fails_the_pto_test` | **Sabotage:** too much PTO fails exactly the PTO test |
| `test_q3_raw_data_gives_the_documented_headline` | raw data gives 20.4%, 28.9%, 38.8% |
| `test_q3_breach_rates_match_raw_data_and_the_headline` | Q3, all 320 outcomes, all 48 cells, the as-of instant, and the warehouse headline |
| `test_sabotage_q3_a_flipped_breach_comparison_fails` | **Sabotage:** a flipped comparison gives 79.6%, 71.1%, 61.2% and changes all 313 resolved outcomes |
| `test_the_as_of_instant_follows_the_data` | a later event moves the as-of instant ([D-036](../decisions.md#d-036-the-as-of-instant-is-derived-from-the-data)) |

Sabotage runs edit a copy of the dbt project (`sabotaged_project`). Each edited text must occur exactly once, so a sabotage can't silently miss. The real project is never edited.

### Gate run for step 4.2

- **`dbt build`**, real schemas: `PASS=182 WARN=0 ERROR=0` (8 views, 13 tables, 161 data tests).
- **pytest** `tests/test_metrics.py`: 16 passed, 0 skipped. The full suite and the hooks are in the session report.
- **Q3 headline** on the real `marts`: Story 20.4%, Task 28.9%, Bug 38.8%. As-of 2024-07-05 15:35:28.261 UTC, 3 issues open and overdue.

### Gate run for step 4.1

- **`dbt build`**, real schemas: `PASS=110 WARN=0 ERROR=0` (8 views, 8 tables, 94 data tests).
- **pytest** `tests/test_metrics.py`: 5 passed, 0 skipped.
- **Real `marts`**, the same comparison run read-only: 36 sprints, 0 differences.
- **Sabotage, calendar days:** 17 of 36 sprints differ (PHX 8, ATL 5, NMD 4; committed, scope added or completed shift by up to 13 points).

### The 36 sprints

Warehouse values from `marts.rpt_sprint_metrics`. "Match" means all six values equal `sprint_report.py`.

| Team | Sprint | Committed | Scope added | Completed | Hit | Bugs | Issues | Match |
|---|---|---|---|---|---|---|---|---|
| PHX | Sprint 1 | 46 | 11 | 30 | no | 4 | 14 | yes |
| PHX | Sprint 2 | 47 | 6 | 42 | no | 6 | 15 | yes |
| PHX | Sprint 3 | 47 | 3 | 39 | no | 2 | 14 | yes |
| PHX | Sprint 4 | 45 | 4 | 49 | yes | 4 | 11 | yes |
| PHX | Sprint 5 | 44 | 22 | 44 | yes | 6 | 17 | yes |
| PHX | Sprint 6 | 58 | 15 | 29 | no | 3 | 14 | yes |
| PHX | Sprint 7 | 50 | 3 | 40 | no | 1 | 16 | yes |
| PHX | Sprint 8 | 53 | 2 | 47 | no | 0 | 12 | yes |
| PHX | Sprint 9 | 45 | 5 | 48 | yes | 4 | 15 | yes |
| PHX | Sprint 10 | 49 | 8 | 36 | no | 2 | 11 | yes |
| PHX | Sprint 11 | 48 | 18 | 48 | yes | 5 | 16 | yes |
| PHX | Sprint 12 | 49 | 8 | 57 | yes | 7 | 16 | yes |
| NMD | Sprint 1 | 20 | 13 | 27 | yes | 3 | 9 | yes |
| NMD | Sprint 2 | 32 | 4 | 5 | no | 1 | 6 | yes |
| NMD | Sprint 3 | 21 | 7 | 28 | yes | 2 | 9 | yes |
| NMD | Sprint 4 | 21 | 5 | 21 | yes | 1 | 6 | yes |
| NMD | Sprint 5 | 22 | 6 | 15 | no | 6 | 12 | yes |
| NMD | Sprint 6 | 20 | 6 | 26 | yes | 2 | 7 | yes |
| NMD | Sprint 7 | 23 | 4 | 14 | no | 2 | 7 | yes |
| NMD | Sprint 8 | 24 | 3 | 27 | yes | 4 | 7 | yes |
| NMD | Sprint 9 | 22 | 8 | 20 | no | 4 | 7 | yes |
| NMD | Sprint 10 | 22 | 7 | 24 | yes | 3 | 9 | yes |
| NMD | Sprint 11 | 19 | 0 | 11 | no | 2 | 4 | yes |
| NMD | Sprint 12 | 32 | 16 | 9 | no | 5 | 9 | yes |
| ATL | Sprint 1 | 10 | 11 | 10 | yes | 1 | 6 | yes |
| ATL | Sprint 2 | 13 | 13 | 8 | no | 0 | 4 | yes |
| ATL | Sprint 3 | 10 | 0 | 5 | no | 1 | 3 | yes |
| ATL | Sprint 4 | 13 | 2 | 15 | yes | 1 | 3 | yes |
| ATL | Sprint 5 | 11 | 24 | 12 | yes | 2 | 7 | yes |
| ATL | Sprint 6 | 11 | 3 | 12 | yes | 1 | 6 | yes |
| ATL | Sprint 7 | 13 | 16 | 11 | no | 0 | 7 | yes |
| ATL | Sprint 8 | 10 | 6 | 15 | yes | 1 | 4 | yes |
| ATL | Sprint 9 | 10 | 5 | 10 | yes | 2 | 5 | yes |
| ATL | Sprint 10 | 10 | 5 | 10 | yes | 0 | 4 | yes |
| ATL | Sprint 11 | 13 | 0 | 13 | yes | 1 | 4 | yes |
| ATL | Sprint 12 | 15 | 5 | 20 | yes | 0 | 4 | yes |

Totals: PHX hit 5 of 12 sprints (581 committed, 105 scope added, 509 completed), NMD 6 of 12 (278, 79, 227), ATL 9 of 12 (139, 90, 141). These are the hit rates in [`../domain-model.md`](../domain-model.md): 42%, 50%, 75%.

## Known limits

- **No carryover.** An issue unfinished at its sprint's end stays a member of that sprint only. 66 issues have work after their own sprint ended ([03-dbt-star-schema.md](03-dbt-star-schema.md)): 65 were resolved later, carrying 343 story points that count as completed in no sprint, and one (PHX Sprint 11) is still in QA. So velocity reads lower than real throughput, and these issues explain part of the deadline breach rate (step 4.2). A further 6 NMD Sprint 12 issues were unfinished when the data ends.
- **Completed includes scope-added work.** A sprint can be hit while committed work is unfinished. That is the answer key's definition, kept on purpose. Scope added is reported next to it.
- **Scope creep is not scaled to team size.** Scope adds arrive at the same rate for every team, so they weigh far more on Atlas's small commitment (90 added on 139 committed) than on Phoenix's (105 on 581) ([`../domain-model.md`](../domain-model.md), "Known v1 limitations").
- **One team per user.** Every sprint figure is a team figure. Nothing is split across teams.
- **Millisecond precision.** Correct only while no relevant raw value falls in the first millisecond after a boundary. The check fails loudly if one does.
- **`expected_row_count` 36** and the 17-sprint sabotage count are the SEED=22 shipped dataset's.

## Options rejected and why

- **Python metrics, DAX measures, views, a separate schema:** [D-033](../decisions.md#d-033-metrics-are-dbt-sql-models-built-as-tables-in-marts).
- **Calendar days, `complete_at`, `event_sprint_id`, the Done transition time, a hand-written expected formula:** [D-034](../decisions.md#d-034-sprint-metrics-read-the-membership-fact-and-end_at-reconciled-with-sprint_reportpy).
- **Assuming the floor is harmless, microseconds in the warehouse, a tolerance:** [D-035](../decisions.md#d-035-the-millisecond-floor-is-checked-at-sprint-boundaries-not-tolerated).
- **A hardcoded as-of instant, the build's wall clock, the last scheduled sprint end:** [D-036](../decisions.md#d-036-the-as-of-instant-is-derived-from-the-data).
- **Headcount only, hours, planned PTO:** [D-037](../decisions.md#d-037-available-capacity-is-headcount--10-working-days--pto-days).
- **A centred window, nulls before three sprints, the mean of ratios:** [D-038](../decisions.md#d-038-rolling-figures-cover-the-sprint-and-the-two-before-it).
- **Separate by-type and by-team models, rates as DAX measures:** [D-039](../decisions.md#d-039-breach-rates-ship-as-a-table-with-labelled-rollups-plus-an-issue-level-table).
