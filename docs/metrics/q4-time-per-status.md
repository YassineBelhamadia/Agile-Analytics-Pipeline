# Q4. Time per status

**Question.** How long does work sit in each status, per team, and where does it wait?

**Models:**

- `marts.rpt_q4_time_in_status`. **Grain:** one row per team × status, plus "All teams" rows: 16 rows (4 working statuses × 3 teams + all). Rollup rows are not sums: filter to one level.
- `marts.rpt_q4_status_stays`. **Grain:** one row per stay (`issue_id`, `stay_number`): 1,988 rows, with the team, and the sprint name and number of the issue's sprint (null for the 180 never-scheduled issues).

## Formula and data

- **A stay** is one visit to a status. The first stay runs from the issue's creation to its first transition. Every later stay runs from the transition into the status to the next transition. A bounce back to an earlier status (QA → In Progress, 75 times in the shipped data) is another stay, with `visit_number` 2 or more.
- **Duration** = next transition − entry, in **calendar hours**: nights and weekends included ([D-040](../decisions.md#d-040-time-based-metrics-use-calendar-hours-exclude-epics-and-keep-open-stays-out-of-the-statistics)).
- **Statistics,** over completed stays: mean, median, and 90th percentile, with the continuous percentile (`percentile_cont`, linear interpolation).
- **Data:** `fct_status_transitions` (ordered by time, then changelog ID, with no ties in the data), `dim_issue` (`created_at`, `issue_type`), `rpt_data_as_of`.

## The still-open final stay, and other exclusions

Every issue's last stay has no next transition. There are three kinds:

| Kind | What it is | Shipped data | Treatment |
|---|---|---|---|
| `completed` | ended by a transition | 1,488 | in the statistics |
| `terminal` | Done, which no issue leaves in v1 | 313 | no row: not a working status |
| `open` | the current status of an unresolved issue | 187 | **excluded from mean, median and p90**, counted in `open_stay_count`, with `open_median_age_hours` at the as-of instant |

**Why open stays stay out of the statistics.** An open stay's duration is unknown: its age at the as-of instant is only a lower bound. Mixing lower bounds with real durations would bias every statistic down, by an amount that depends on when the data ends. Closing them at the as-of instant would be the same bias under another name.

180 of the 187 open stays are To Do stays of issues never pulled into a sprint. That is backlog age (median 841 h), not process time, and it would dominate the To Do figures. The other 7 are 5 In Progress (Nomad Sprint 12, median age 240 h) and 2 QA. Reporting the count and age next to the statistics keeps them visible without distorting them. A survival-analysis estimate (Kaplan–Meier) would use them properly, and is left out of v1 as over-engineering for 7 in-process stays.

**Epics are excluded.** They never change status, so their one stay is creation to now.

## Assumptions and limits

- **Calendar hours.** A stay from Friday 16:00 to Monday 10:00 counts 66 hours, although the simulated team works 8 hours a day on weekdays. Working hours would need a calendar of working days and hours in the warehouse (no date dimension in v1, [D-028](../decisions.md#d-028-star-schema-with-natural-keys-and-no-date-dimension)). Calendar hours are what someone waiting for the work experiences, and they need no calendar assumptions.
- **To Do includes backlog wait.** For a scheduled issue, To Do runs from creation to the start of work, so it holds the wait in the backlog before sprint planning, not just in-sprint waiting. That makes To Do far longer than the working statuses (median 912 h, about 38 days, all teams).
- **The workflow is fixed,** with no ties and a To Do start. `test_q4_raw_histories_start_in_to_do_and_never_leave_done` checks this on `raw_data/`. A reopened Done issue would need its Done stay counted, which the model already does as a completed stay.
- **No carryover.** Durations are unaffected: a stay is measured wherever it falls, across sprint boundaries.

## How it was verified

`tests/test_metrics.py` rebuilds every stay in plain Python from `raw_data/issues.json` and `status_transitions.json`, with each timestamp floored to the millisecond as the contract does ([D-024](../decisions.md#d-024-reconciliation-truncates-raw-timestamps-to-milliseconds)), and compares:

- `test_q4_time_in_status_matches_raw_data`: all 1,988 stays (team, status, visit, kind, duration or age), and all 16 cells (counts exact; mean, median, p90 and open age to 1e-9 h, below the data's 1 ms resolution).
- `test_q4_raw_histories_start_in_to_do_and_never_leave_done`: the model's assumptions, on the raw side.
- dbt: `assert_status_stays_chain_without_gaps` checks that each completed stay ends exactly where the next begins, that no duration is negative, and that each issue ends on an open or terminal stay.
- **Sabotage:** `test_sabotage_q4_starting_the_first_stay_at_the_first_transition_fails`. Starting the first stay at the first transition instead of at creation makes exactly the four To Do cells differ, and no other status.

## Example result (completed stays, calendar hours)

| Status | Team | Stays | Repeat visits | Mean | Median | p90 | Open stays |
|---|---|---|---|---|---|---|---|
| To Do | All teams | 320 | 0 | 872.7 | 912.5 | 1,345.5 | 180 |
| In Progress | All teams | 390 | 75 | 103.6 | 74.8 | 215.6 | 5 |
| Code Review | All teams | 390 | 75 | 13.1 | 4.6 | 21.6 | 0 |
| QA | All teams | 388 | 74 | 19.2 | 19.0 | 67.6 | 2 |
| In Progress | Phoenix | 219 | 48 | 102.0 | 74.5 | 212.7 | 0 |
| In Progress | Nomad | 108 | 21 | 101.8 | 75.7 | 212.8 | 5 |
| In Progress | Atlas | 63 | 6 | 112.2 | 73.8 | 237.8 | 0 |

Work spends about three days (median) in In Progress, and a few hours in Code Review and QA. Teams differ little in the working statuses. Phoenix's bounce rate shows in its 48 repeat visits to In Progress.
