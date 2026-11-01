-- Q2: velocity against available capacity, per team and sprint (grain: sprint_id).
-- Available person-days = headcount x working days - PTO days (D-037), the
-- simulator's own capacity arithmetic (sprint_cycle.py) in days instead of hours.
-- The rolling figure is capacity-weighted: completed points over available
-- person-days, both summed over the sprint and the two before it, so a short-
-- staffed sprint weighs less than a full one.
with per_sprint as (
    select
        metrics.sprint_id,
        metrics.team_id,
        metrics.team_key,
        metrics.team_name,
        metrics.sprint_name,
        metrics.sprint_number,
        metrics.start_at,
        metrics.end_at,
        capacity.headcount,
        capacity.pto_days,
        capacity.headcount * {{ var('sprint_working_days') }} - capacity.pto_days as available_person_days,
        metrics.completed_points
    from {{ ref('rpt_sprint_metrics') }} as metrics
    left join {{ ref('fct_capacity') }} as capacity
        on capacity.sprint_id = metrics.sprint_id
)

select
    sprint_id,
    team_id,
    team_key,
    team_name,
    sprint_name,
    sprint_number,
    start_at,
    end_at,
    headcount,
    pto_days,
    available_person_days,
    completed_points,
    completed_points / available_person_days as points_per_person_day,
    sum(completed_points) over team_window
        / sum(available_person_days) over team_window as points_per_person_day_rolling_3,
    count(*) over team_window as sprints_in_window
from per_sprint
window team_window as (
    partition by team_id
    order by sprint_number
    rows between 2 preceding and current row
)
