# Q1. Velocity per sprint

**Question.** How many story points does each team finish per sprint, and what is its recent trend?

**Model:** `marts.rpt_q1_velocity`. **Grain:** one row per sprint (`sprint_id`), 36 rows, with the team and sprint names and number.

## Formula and data

- **Velocity** (`velocity_points`) is the sprint's completed story points from [`rpt_sprint_metrics`](../etl/04-metrics.md): points of the sprint's members resolved at or before its end instant, committed and scope-added alike. It equals `simulator/sprint_report.py`'s `completed` for all 36 sprints.
- **Rolling average** (`velocity_rolling_3`): the mean velocity of the sprint and the two before it in the same team ([D-038](../decisions.md#d-038-rolling-figures-cover-the-sprint-and-the-two-before-it)). `sprints_in_window` says how many sprints the window holds: 1 for sprint 1, 2 for sprint 2, 3 after that.
- **Points finished after the sprint** (`completed_after_end_points`, `completed_after_end_issues`): the sprint's members resolved after its end instant.
- **Data:** `fct_sprint_membership`, `dim_issue` (`story_points`, `resolved_at`), `dim_sprint` (`end_at`).

## Assumptions and limits

- **Story points are team-relative.** A Phoenix point costs 3.5 hours of work and an Atlas point 6 (`simulator/personalities.py`), so velocity compares a team with itself over time, not teams with each other. [q6-portfolio.md](q6-portfolio.md) compares teams and states the limits.
- **No carryover (v1 limit).** An issue unfinished at its sprint's end stays a member of that sprint only, and its points count in no sprint's velocity when it is finished later ([`../domain-model.md`](../domain-model.md), "Known v1 limitations"). In the shipped data, **65 issues and 343 story points** are resolved after their sprint ended. That is **28.1%** of the 1,220 points ever completed on sprint members: velocity counts the other 877 (71.9%). Put another way, real throughput is 39.1% higher than the summed velocity. By team:

  | Team | Velocity (12 sprints) | Finished after their sprint | Share of all completed |
  |---|---|---|---|
  | Phoenix | 509 | 164 points (29 issues) | 24.4% |
  | Nomad | 227 | 91 points (18 issues) | 28.6% |
  | Atlas | 141 | 88 points (18 issues) | 38.4% |

  So velocity reads lower than real throughput, most for Atlas. These issues also explain part of the breach rate ([q3-breach-rate.md](q3-breach-rate.md)).
- **Partial windows.** The first two rolling values average 1 and 2 sprints. Filter on `sprints_in_window = 3` to show full windows only.

## How it was verified

`tests/test_metrics.py` recomputes every column in plain Python from `raw_data/*.json` (memberships, issue resolution dates, sprint end dates at 09:00 UTC), with no pipeline code, and compares it with `marts_test.rpt_q1_velocity`:

- `test_q1_velocity_matches_raw_data`: all 36 sprints, all five columns. Points and counts are exact. The rolling mean is compared as a fraction, to 1e-12.
- `test_q1_late_points_are_the_documented_no_carryover_figures`: 343 points, 65 issues, 877 velocity, 28.1%.
- **Sabotage:** `test_sabotage_q1_a_four_sprint_window_fails`. A 4-sprint window makes exactly 27 sprints differ (sprints 4 to 12 of each team), in the rolling columns only.

## Example result (Nomad)

| Sprint | Velocity | Rolling 3 | Finished after the sprint |
|---|---|---|---|
| 1 | 27 | 27.0 | 6 |
| 2 | 5 | 16.0 | 31 |
| 3 | 28 | 20.0 | 0 |
| 4 | 21 | 18.0 | 5 |
| 5 | 15 | 21.3 | 13 |
| 6 | 26 | 20.7 | 0 |
| 7 | 14 | 18.3 | 13 |
| 8 | 27 | 22.3 | 0 |
| 9 | 20 | 20.3 | 10 |
| 10 | 24 | 23.7 | 5 |
| 11 | 11 | 18.3 | 8 |
| 12 | 9 | 14.7 | 0 |

Sprint 2 shows the carryover limit at its sharpest: 5 points finished inside the sprint and 31 just after it.
