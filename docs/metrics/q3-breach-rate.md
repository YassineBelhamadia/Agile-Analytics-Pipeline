# Q3. Deadline breach rate

**Question.** How often is work finished after its due date, by issue type and by team, and how much is open and already overdue?

**Models:**

- `marts.rpt_q3_breach_rate`. **Grain:** one row per team × issue type × commitment, plus rollup rows labelled "All teams", "All types" and "All". 48 rows in the shipped data: 18 detail rows and 30 rollup rows, told apart by `is_rollup` ([D-047](../decisions.md#d-047-rollup-rows-carry-an-is_rollup-flag)). The headline by issue type is `team_name = 'All teams'` and `commitment = 'All'`. Rollup rows are totals: filter to one level, never sum across levels.
  - **In the dashboard:** a visual that sums a count filters to `is_rollup = false`, where `due_issue_count` adds up to 320. A rate is never summed: a card or table showing one reads the row of its level (for example the headline row above).
- `marts.rpt_q3_deadline_outcomes`. **Grain:** one row per issue with a due date (320), with its outcome, team, sprint (name and number) and commitment, for drill-down.
- `marts.rpt_data_as_of`. One row: the instant open issues are measured at.

## Formula and data

The deadline rule and the breach definition are those of [`../domain-model.md`](../domain-model.md), "Deadline (duedate) rule" ([D-015](../decisions.md#d-015-deadline-duedate-rule-is-an-authored-policy), [D-020](../decisions.md#d-020-breach-headline-is-resolved-only-bug-to-story-ratio-counts-all-created-issues)). Only issues with a `duedate` count: Epics and the 180 never-scheduled issues have none.

| Outcome | Rule | In the headline | In the "incl. overdue" rate |
|---|---|---|---|
| `late` | resolved, UTC date of `resolved_at` > `duedate` | breached | breached |
| `on_time` | resolved, date ≤ `duedate` | on time | on time |
| `open_overdue` | unresolved at the as-of instant, `duedate` before its date | excluded | breached |
| `open_not_due` | unresolved, not yet due (censored) | excluded | excluded |

- **Headline** `breach_rate_resolved` = late ÷ (late + on time). This is the rate the answer key's figures are quoted in.
- **Including open overdue** `breach_rate_incl_overdue` = (late + open overdue) ÷ (late + on time + open overdue).
- **The as-of instant** is the last event in the data: the latest issue creation, resolution, status transition or sprint join (`rpt_data_as_of`, [D-036](../decisions.md#d-036-the-as-of-instant-is-derived-from-the-data)). It is derived on every build, never hardcoded. Shipped data: **2024-07-05 15:35:28.261 UTC**, so the as-of date is 2024-07-05.
- **Commitment.** The first sprint an issue joined set its due date, and decides whether it was committed (joined at or before the sprint start) or scope-added.
- **Data:** `dim_issue` (`issue_type`, `duedate`, `resolved_at`), `fct_sprint_membership`, `rpt_sprint_metrics` (team and sprint names), `fct_status_transitions` (for the as-of instant).

## Assumptions

- **Dates, not instants, for deadlines.** `duedate` has no time. An issue is late when it is resolved on a later UTC date. The simulator resolves work only in working hours, so for Stories and Tasks this agrees with the sprint's end instant ([`../domain-model.md`](../domain-model.md)).
- **An issue due on the as-of date is not yet overdue.** The data ends on Friday 2024-07-05 at 15:35, and four Nomad Sprint 12 issues due that day are still open. They are censored, not breached.
- **Millisecond flooring** cannot move a resolution across midnight, since midnight is a whole millisecond. So the warehouse's dates equal the raw dates.

## Example result (shipped data)

**Headline, resolved only, by issue type (all teams):** Story **20.4%** (31 of 152), Task **28.9%** (22 of 76), Bug **38.8%** (33 of 85). Including the 3 open overdue issues, all of them Bugs: Story 20.4%, Task 28.9%, Bug 40.9%.

**By team (all types):**

| Team | Due issues | Resolved | Late | Open overdue | Open not due | Resolved only | Incl. overdue |
|---|---|---|---|---|---|---|---|
| Phoenix | 171 | 170 | 41 | 1 | 0 | 24.1% | 24.6% |
| Nomad | 92 | 86 | 26 | 2 | 4 | 30.2% | 31.8% |
| Atlas | 57 | 57 | 19 | 0 | 0 | 33.3% | 33.3% |
| All teams | 320 | 313 | 86 | 3 | 4 | 27.5% | 28.2% |

**Committed vs. scope-added (resolved only):**

| | Committed | Scope added |
|---|---|---|
| Phoenix | 16.8% | 63.0% |
| Nomad | 28.6% | 34.8% |
| Atlas | 14.3% | 63.6% |
| Story | 12.4% | 43.6% |
| Task | 12.3% | 78.9% |
| Bug | 36.6% | 50.0% |

**Open and overdue at the as-of instant:** 3 issues, all Bugs: PHX-161 (Phoenix Sprint 11, due 2024-06-21), NMD-89 (due 2024-07-01) and NMD-91 (due 2024-07-02), both Nomad Sprint 12.

## Limits

- **No carryover.** 65 issues finish after their sprint ended (Q1), and all 65 are late by construction: no due date is after its sprint's last workday. They are 65 of the 86 late issues (75.6%). The other 21 were Bugs that missed a 5-workday deadline inside their sprint. In a real team, the unfinished work would have been replanned into the next sprint with a new date.
- **Scope creep is not scaled to team size.** Scope adds arrive at the same rate for every team, so Atlas, the team with the best hit rate (9 of 12), has the highest breach rate: its committed work is the most punctual (14.3% late), but its scope-added work is late 63.6% of the time. Present breach split by commitment ([`../domain-model.md`](../domain-model.md)).
- **Bug deadline is an authored policy:** 5 workdays, capped at the sprint's last workday. It was not tuned to any rate ([D-015](../decisions.md#d-015-deadline-duedate-rule-is-an-authored-policy)).
- **One team per user.** The team is the issue's sprint's team.

## How it was verified

`tests/test_metrics.py` recomputes the outcome of every issue, the as-of instant and every cell of the rate table, rollups included, in plain Python from `raw_data/*.json`, and compares them with `marts_test`:

- `test_q3_raw_data_gives_the_documented_headline`: raw data alone gives 20.4%, 28.9%, 38.8%.
- `test_q3_breach_rates_match_raw_data_and_the_headline`: all 320 issue outcomes (type, team, sprint, commitment, outcome) and all 48 cells (six counts exact, both rates as fractions to 1e-12) match. The as-of instant equals the raw maximum floored to the millisecond. The warehouse headline is 20.4%, 28.9%, 38.8%.
- `test_the_as_of_instant_follows_the_data`: moving one transition to 2024-07-06 12:00 UTC moves `rpt_data_as_of` to that instant.
- **Sabotage:** `test_sabotage_q3_a_flipped_breach_comparison_fails`. Flipping `>` to `<=` turns the headline into 79.6%, 71.1%, 61.2% and changes the outcome of all 313 resolved issues.
- dbt: `assert_breach_rate_rollups_add_up` checks that every rollup row is the sum of its parts along each dimension, and that the grand total is all 320 issues. `assert_rollup_flag_matches_labels` checks that `is_rollup` is true exactly on the rows with a rollup label. Accepted-value tests cover `outcome` and `commitment`.
- `test_is_rollup_splits_the_rows_and_the_detail_rows_add_up`: the flag splits the rows 18/30, and the detail rows add up to the 320 due issues.
