# Business questions

One page per question the dashboard answers. Each page states the question in plain words, the formula and data, the assumptions, how it was verified, and an example result from the shipped dataset (SEED=22). The models are SQL in `dbt/models/metrics/`, built as tables in schema `marts` ([D-033](../decisions.md#d-033-metrics-are-dbt-sql-models-built-as-tables-in-marts)). How the stage is built, run and tested: [`../etl/04-metrics.md`](../etl/04-metrics.md).

| # | Question | Page | Model(s) | Grain | Headline (shipped data) |
|---|---|---|---|---|---|
| base | Committed, scope added, completed per sprint | [`../etl/04-metrics.md`](../etl/04-metrics.md) | `rpt_sprint_metrics` | sprint | equals `sprint_report.py` on all 36 sprints; hit rates 42% / 50% / 75% |
| 1 | Velocity per sprint | [q1-velocity.md](q1-velocity.md) | `rpt_q1_velocity` | sprint | Phoenix 42.4, Nomad 18.9, Atlas 11.8 points per sprint; 343 points (28.1%) finish after their sprint and count nowhere |
| 2 | Velocity against capacity | [q2-velocity-vs-capacity.md](q2-velocity-vs-capacity.md) | `rpt_q2_velocity_vs_capacity` | sprint | 0.87 / 0.53 / 0.41 points per available person-day, tracking each team's realization factor and point size |
| 3 | Deadline breach rate | [q3-breach-rate.md](q3-breach-rate.md) | `rpt_q3_breach_rate`, `rpt_q3_deadline_outcomes`, `rpt_data_as_of` | team × type × commitment; issue | Story 20.4%, Task 28.9%, Bug 38.8% (resolved only); 3 open and overdue |
| 4 | Time per status | [q4-time-per-status.md](q4-time-per-status.md) | `rpt_q4_time_in_status`, `rpt_q4_status_stays` | team × status; stay | median 75 h In Progress, 5 h Code Review, 19 h QA; To Do (912 h) is mostly backlog wait |
| 5 | Bug-to-story ratio | [q5-bug-to-story.md](q5-bug-to-story.md) | `rpt_q5_bug_to_story_ratio` | team × two-week period | Nomad 0.79, Phoenix 0.68, Atlas 0.25 |
| 6 | Cross-team portfolio | [q6-portfolio.md](q6-portfolio.md) | `rpt_q6_team_portfolio` | team | Phoenix delivers most per person-day, Atlas hits most but breaches most, Nomad loses most to PTO |

**Rules every model follows**

- A documented grain, with the team name, and the sprint name and number where the grain has a sprint. No further joins in the BI tool.
- Every model and column is described in `dbt/models/metrics/_metrics__*.yml`.
- Rollup rows ("All teams", "All types", "All") are totals. Filter to one level and never sum across levels.

**Limits shared by every question** ([`../domain-model.md`](../domain-model.md), "Known v1 limitations"):

- **No carryover.** 66 issues have work after their sprint ended (65 resolved later, 343 points). This lowers velocity and raises the breach rate.
- **One team per user.**
- **Scope creep not scaled to team size.**
- **Story points are team-relative.**
- **Durations are in calendar hours.**
- **Epics are in no time-based metric.**
