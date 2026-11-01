# Q5. Bug-to-story ratio over time

**Question.** For every Story a team creates, how many Bugs does it create, and how does that change over time?

**Model:** `marts.rpt_q5_bug_to_story_ratio`. **Grain:** one row per team × period (`team_id`, `period_number`), 39 rows: 13 periods per team, each named after its sprint ("Before Sprint 1", "Sprint 1" to "Sprint 12").

## Formula and data

- **Counts** of Bugs, Stories and Tasks **created** in the period, from `dim_issue.created_at`. Every created issue counts, whether or not it ever joined a sprint, because 180 non-Epic issues never did ([D-020](../decisions.md#d-020-breach-headline-is-resolved-only-bug-to-story-ratio-counts-all-created-issues)). Epics are not counted. Tasks are shown for context and are not in the ratio.
- **Ratio** = Bugs ÷ Stories in the period. It is null if no Story was created.
- **Cumulative ratio** = Bugs ÷ Stories from the start up to the period. It is the steadier trend line.
- **Periods** are the team's sprint windows, `start_at <= created_at < end_at`. Period 0, "Before Sprint 1", holds what was created before the first sprint.

## Why this time grain

The data runs from the first issue (2024-01-08 09:23) to the last event (2024-07-05 15:35): 26 weeks. The simulator builds a two-week backlog before Sprint 1, then runs 12 two-week sprints. So the span is exactly 13 two-week periods, and the sprint windows tile it with no partial period ([D-041](../decisions.md#d-041-q5-counts-issues-by-the-sprint-window-they-were-created-in)).

- **Weekly** would average 0.5 Bugs and 2 Stories a week for Atlas (13 and 52 in 26 weeks), and under 3 Bugs a week even for Phoenix: mostly zeros and divisions by tiny numbers.
- **Monthly** would cut the span into a 24-day January, four full months and a 5-day July. The first and last points would rest on partial months, and the axis would not line up with the sprint charts.
- **Sprint windows** keep every period the same length and put Q5 on the same axis as Q1 and Q2.

Small counts remain: a two-week Atlas period has 1 to 7 Stories. Read the cumulative ratio for the trend, and the per-period ratio for spikes.

## Assumptions and limits

- **Created, not resolved.** The ratio measures what arrives, including work never scheduled. A ratio of *completed* work would follow velocity instead.
- **Arrival mix is a simulator parameter.** Bugs are 28% of Phoenix arrivals, 15% of Atlas's and 22% of Nomad's (`simulator/personalities.py`), and about one sprint in six is a "bug incident" sprint with 40% Bugs (`simulator/sprint_cycle.py`). Incidents are not recorded in the data. Nomad's rise in periods 3 to 8 is consistent with them, but that is an inference.
- **One team per user, and a team per project.** An issue's team is its project.

## How it was verified

`tests/test_metrics.py` recomputes every period's counts and both ratios in plain Python from `raw_data/issues.json` and `sprints.json` (windows at 09:00 UTC):

- `test_q5_bug_to_story_ratio_matches_raw_data`: all 39 rows. Counts are exact, ratios fractions to 1e-12.
- `test_q5_periods_cover_every_created_issue`: the 13 periods hold all 500 non-Epic issues. The dbt test `assert_q5_counts_every_created_issue` checks the same thing in the warehouse.
- The millisecond boundary check ([`../etl/04-metrics.md`](../etl/04-metrics.md)) covers issue creation times too: none of the 530 falls in the first millisecond after a sprint boundary.
- **Sabotage:** `test_sabotage_q5_counting_only_scheduled_issues_fails`. Counting only issues that joined a sprint, the option D-020 rejected, changes the counts and ratios.

## Example result

| Team | Bugs | Stories | Bug-to-story ratio (all periods) |
|---|---|---|---|
| Phoenix | 75 | 111 | 0.68 |
| Nomad | 53 | 67 | 0.79 |
| Atlas | 13 | 52 | 0.25 |

Nomad, by period (cumulative ratio in brackets): 0.43 before Sprint 1 (0.43), then 0.33, 0.40, **1.20, 0.80, 1.33, 1.25, 1.00, 1.60** in sprints 3 to 8 (climbing to 0.89), then 0.50, 0.75, 0.67, 0.25 (ending at 0.79).
