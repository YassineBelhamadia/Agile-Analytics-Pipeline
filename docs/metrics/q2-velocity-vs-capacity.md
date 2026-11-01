# Q2. Velocity against available capacity

**Question.** How much does each team deliver for the people it actually had in a sprint?

**Model:** `marts.rpt_q2_velocity_vs_capacity`. **Grain:** one row per sprint (`sprint_id`), 36 rows, with the team and sprint names and number.

## Formula and data

- **Available person-days** = `headcount × 10 − pto_days` ([D-037](../decisions.md#d-037-available-capacity-is-headcount--10-working-days--pto-days)). Every sprint has 10 working days ([`../domain-model.md`](../domain-model.md), "Known v1 limitations"). The 10 is the dbt var `sprint_working_days`.
- **Velocity** (`completed_points`): the sprint's completed story points ([q1-velocity.md](q1-velocity.md)).
- **Points per available person-day** = `completed_points / available_person_days`.
- **Rolling figure** (`points_per_person_day_rolling_3`): completed points summed over the sprint and the two before it, divided by their summed available person-days. It is capacity-weighted, so a short-staffed sprint weighs less than a full one ([D-038](../decisions.md#d-038-rolling-figures-cover-the-sprint-and-the-two-before-it)). `sprints_in_window` gives the window size.
- **Data:** `fct_capacity` (`headcount`, `pto_days` at full precision, from `/capacity`) and `rpt_sprint_metrics`.

### Checked against the simulator

The formula was confirmed, read-only, against how the simulator sizes a sprint (`simulator/sprint_cycle.py`, `sprint_cycle_process`):

```python
baseline_available_hours = headcount * SPRINT_LENGTH_HOURS - average_pto_days_per_sprint * WORK_HOURS_PER_DAY
committed_target_points  = baseline_available_hours / hours_per_point * capacity_realization_factor * optimism
```

`SPRINT_LENGTH_HOURS` is `WORK_HOURS_PER_DAY × 10` and `WORK_HOURS_PER_DAY` is 8 (`simulator/clock.py`). So the simulator's available hours are exactly `8 × (headcount × 10 − PTO days)`: the same 10 working days and the same subtraction, in hours instead of days. Two differences, both intended:

- **Actual vs. planned PTO.** The simulator plans each commitment on the team's *average* PTO (`average_pto_days_per_sprint`). The PTO it then draws for the sprint is what `/capacity` serves and what Q2 uses. "Available" means what the team really had.
- **When the absence lands.** The drawn PTO holds one developer slot for `pto_days × 8` hours, starting at a random point in the sprint and only once a developer is free (`pto_process`). So part of a sprint's PTO can fall in the next sprint. Q2 charges all of it to the sprint it was recorded for, as a capacity tool would.

## How it relates to the realization factor

At planning, the simulator commits, per baseline available person-day, `8 × capacity_realization_factor × optimism ÷ hours_per_point` points. With the tuned personalities (`simulator/personalities.py`, mid-range optimism):

| Team | hours per point | crf (tuned) | optimism (mid) | Planned points per person-day | Committed per available person-day | Completed per available person-day |
|---|---|---|---|---|---|---|
| Phoenix | 3.5 | 0.35 | 1.15 | 0.92 | 1.00 (581 / 583.3) | 0.87 (509 / 583.3) |
| Nomad | 5.0 | 0.29 | 1.165 | 0.54 | 0.65 (278 / 430.7) | 0.53 (227 / 430.7) |
| Atlas | 6.0 | 0.22 | 1.04 | 0.31 | 0.40 (139 / 348.0) | 0.41 (141 / 348.0) |

What this shows:

- **Q2 measures the realization factor, scaled by the cost of a point.** Points per person-day is about `8 × crf ÷ hours_per_point`. The ordering Phoenix > Nomad > Atlas mostly reflects how cheap each team's points are (3.5, 5 and 6 hours), not how hard people work. Q2 compares a team with itself across sprints. Comparing teams needs the caveat in Q6.
- **Committed is above the planned rate** for every team, because planning takes whole tickets: the ticket that crosses the target is taken in full. That matters most for Atlas, whose target is about 9 points a sprint, while its average commitment is 11.6.
- **Completed falls short of committed** for Phoenix and Nomad, whose factors were tuned to hit-rate bands of 37–45% and 48–56% ([`../domain-model.md`](../domain-model.md), calibration). Atlas, tuned to 69–78%, completes slightly more than it commits, because scope-added work counts as completed. Shipped hit rates: 42%, 50%, 75%.
- **PTO explains little within a team** in the shipped data. Over 12 sprints, the correlation between a sprint's PTO and its completed points is −0.11 (Atlas), 0.09 (Nomad) and 0.13 (Phoenix). Issue sizes, scope adds and the no-carryover limit move velocity more than PTO does. That is an observation on one seed, not a finding.

The realization factor and `hours_per_point` are simulator parameters. The warehouse doesn't hold them, and the table above takes them from `simulator/personalities.py` for explanation only.

## Assumptions and limits

- **PTO never exceeds the sprint's person-days.** A dbt test, `assert_pto_within_sprint_person_days`, fails any capacity row with `pto_days < 0` or `> headcount × 10`, where available capacity would be zero or negative. Maximum in the shipped data: 21% of a sprint's person-days.
- **Headcount is the team's size, not who worked.** v1 has one team per user, and PTO is recorded per team, not per person ([`../domain-model.md`](../domain-model.md)).
- **No carryover.** Points finished after their sprint count nowhere, which lowers the ratio most for Atlas ([q1-velocity.md](q1-velocity.md)).
- **Calendar.** No public holidays: every sprint has 10 working days, by the simulator's design.

## How it was verified

`tests/test_metrics.py` recomputes every column from `raw_data/capacities.json`, `issues.json`, `sprint_issue_memberships.json` and `sprints.json` in plain Python, and compares it with `marts_test.rpt_q2_velocity_vs_capacity`:

- `test_q2_velocity_vs_capacity_matches_raw_data`: all 36 sprints, all seven columns. Headcount, points and window size are exact. Float columns are compared to a relative 1e-12.
- `test_raw_pto_never_exceeds_the_sprints_person_days`: the raw side of the PTO rule.
- **Sabotage:** `test_sabotage_q2_ignoring_pto_fails`. Dropping `− pto_days` makes all 36 sprints differ, in the three capacity columns only.
- **Sabotage:** `test_sabotage_pto_above_the_sprints_person_days_fails_the_pto_test`. One capacity row set to `headcount × 10 + 0.5` fails exactly `assert_pto_within_sprint_person_days`.

## Example result (Nomad, the high-PTO team)

| Sprint | Headcount | PTO days | Available person-days | Completed | Points per person-day | Rolling 3 |
|---|---|---|---|---|---|---|
| 1 | 4 | 0.95 | 39.05 | 27 | 0.691 | 0.691 |
| 2 | 4 | 2.46 | 37.54 | 5 | 0.133 | 0.418 |
| 3 | 4 | 2.94 | 37.06 | 28 | 0.756 | 0.528 |
| 4 | 4 | 5.02 | 34.98 | 21 | 0.600 | 0.493 |
| 5 | 4 | 2.69 | 37.31 | 15 | 0.402 | 0.585 |
| 6 | 4 | 8.51 | 31.49 | 26 | 0.826 | 0.597 |
| 7 | 4 | 2.57 | 37.43 | 14 | 0.374 | 0.518 |
| 8 | 4 | 5.86 | 34.14 | 27 | 0.791 | 0.650 |
| 9 | 4 | 7.27 | 32.73 | 20 | 0.611 | 0.585 |
| 10 | 4 | 1.58 | 38.42 | 24 | 0.625 | 0.674 |
| 11 | 4 | 1.64 | 38.36 | 11 | 0.287 | 0.502 |
| 12 | 4 | 7.87 | 32.13 | 9 | 0.280 | 0.404 |

Values are rounded here. The table holds full precision.
