# Q6. Cross-team portfolio

**Question.** Side by side, how do the three teams compare on delivery, predictability, quality and flow, once team size is taken into account?

**Model:** `marts.rpt_q6_team_portfolio`. **Grain:** one row per team (`team_id`), over all its sprints (`sprint_count` = 12, Sprint 1 to Sprint 12).

## Formula and data

Every value is read from the models that answer Q1 to Q5 and from `rpt_sprint_metrics`, never recomputed, so the portfolio can't disagree with them ([D-042](../decisions.md#d-042-the-portfolio-reads-q1-to-q5-and-normalizes-only-size-dependent-figures)).

| Column | Source | Definition | Normalized? |
|---|---|---|---|
| `velocity_per_sprint` | Q1 | mean completed points per sprint | no: grows with team size and point size |
| `points_per_person_day` | Q2 | **capacity use:** completed points ÷ available person-days, summed over all 12 sprints | per available person-day |
| `mean_headcount`, `available_person_days`, `pto_share` | Q2 | context: PTO days ÷ nominal person-days (headcount × 10) | rate |
| `hit_count`, `hit_rate` | `rpt_sprint_metrics` | sprints with completed ≥ committed, ÷ 12 | rate |
| `breach_rate_resolved`, `breach_rate_incl_overdue`, `open_overdue_count` | Q3 | the team's all-types row | rate |
| `median_hours_to_do`, `_in_progress`, `_code_review`, `_qa` | Q4 | median completed stay per status, calendar hours | per issue |
| `bug_to_story_ratio` | Q5 | all Bugs ÷ all Stories created | ratio |

**What "fairly" means here.** Only figures that grow with team size are normalized, and they are normalized per available person-day: headcount × 10 − PTO, so PTO counts too. Rates, ratios and medians are already per sprint or per issue. Dividing them by people would mix units, so they are left as they are. Capacity use is summed before dividing (capacity-weighted), so a short-staffed sprint weighs less than a full one. The sabotage test shows the difference.

## Normalization limits

- **A story point is not the same size in every team.** It is estimated per team: in the simulator, a Phoenix point costs 3.5 hours of work, a Nomad point 5 and an Atlas point 6 (`simulator/personalities.py`). So Phoenix's 0.87 points per person-day against Atlas's 0.41 says more about point size than about output. As an illustration only, multiplying by those simulator-only hours gives about 0.38, 0.33 and 0.30 of each available hour turned into completed estimated work. That is close to each team's tuned realization factor ([q2-velocity-vs-capacity.md](q2-velocity-vs-capacity.md)), and much closer between teams. The warehouse has no hours-per-point, so the portfolio can't make that correction.
- **PTO is per team, not per person** ([`../domain-model.md`](../domain-model.md)), and headcount is the team's size, not who actually worked.
- **Scope creep is not scaled to team size.** Scope adds arrive at the same rate for every team, so they weigh most on the smallest team. That lifts Atlas's breach rate (33.3%) despite its best hit rate.
- **No carryover.** Points finished after their sprint count nowhere: 38% of Atlas's completed points against 24% of Phoenix's ([q1-velocity.md](q1-velocity.md)). Atlas's velocity and capacity use read lowest relative to real throughput.
- **One team per user, one seed.** The ordering of teams can change with the seed: the per-seed spread of hit rates is wide ([`../domain-model.md`](../domain-model.md), calibration).
- **Twelve sprints.** Every rate rests on 12 sprints or a few dozen issues per team. Differences of a few points are noise.

## How it was verified

- `test_q6_portfolio_equals_the_q1_to_q5_values`: after a build, every column of every team equals the value computed in the test from `rpt_sprint_metrics` and the Q1 to Q5 tables. Q1 to Q5 are themselves checked against `raw_data/` (their pages). Counts are exact, rates fractions to 1e-12, float sums to a relative 1e-12.
- **Sabotage:** `test_sabotage_q6_averaging_the_sprint_ratios_fails`. Capacity use as the mean of the 12 sprint ratios, instead of total points over total person-days, makes `points_per_person_day` differ for all 3 teams, and nothing else.

## Example result (shipped data, SEED=22)

| | Phoenix | Nomad | Atlas |
|---|---|---|---|
| Headcount | 5 | 4 | 3 |
| PTO share of person-days | 2.8% | 10.3% | 3.3% |
| Velocity per sprint (points) | 42.4 | 18.9 | 11.8 |
| Capacity use (points per available person-day) | 0.87 | 0.53 | 0.41 |
| Sprint hit rate | 41.7% (5 of 12) | 50.0% (6) | 75.0% (9) |
| Breach rate, resolved only | 24.1% | 30.2% | 33.3% |
| Breach rate, incl. open overdue | 24.6% | 31.8% | 33.3% |
| Median hours in To Do | 910 | 720 | 1,081 |
| Median hours in In Progress | 74.5 | 75.7 | 73.8 |
| Median hours in Code Review | 4.7 | 4.4 | 4.4 |
| Median hours in QA | 18.5 | 19.0 | 19.3 |
| Bug-to-story ratio | 0.68 | 0.79 | 0.25 |

**Headline.** Phoenix delivers the most points per person-day but hits its commitment least often. Atlas is the most predictable but misses the most deadlines, 14 of its 19 late issues on scope-added work. Nomad loses the most capacity to PTO and creates the most bugs per story. In-sprint flow (In Progress, Code Review, QA) is nearly identical across teams. The differences come from how much each team commits, not how fast it works an issue.
