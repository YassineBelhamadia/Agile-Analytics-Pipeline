-- Q1: velocity per team and sprint (grain: sprint_id). Velocity is completed
-- story points, as in rpt_sprint_metrics (the answer key's "completed").
-- The rolling average covers the sprint and the two before it in the same team;
-- the first two sprints average what exists, and sprints_in_window says how many.
-- completed_after_end_points shows the no-carryover limit: points of the sprint's
-- members resolved after it ended, which count in no sprint's velocity.
with late as (
    select
        membership.sprint_id,
        coalesce(sum(issues.story_points), 0) as completed_after_end_points,
        count(*)                              as completed_after_end_issues
    from {{ ref('fct_sprint_membership') }} as membership
    join {{ ref('dim_issue') }} as issues
        on issues.issue_id = membership.issue_id
    join {{ ref('dim_sprint') }} as sprints
        on sprints.sprint_id = membership.sprint_id
    where issues.resolved_at > sprints.end_at
    group by membership.sprint_id
)

select
    metrics.sprint_id,
    metrics.team_id,
    metrics.team_key,
    metrics.team_name,
    metrics.sprint_name,
    metrics.sprint_number,
    metrics.start_at,
    metrics.end_at,
    metrics.completed_points as velocity_points,
    avg(metrics.completed_points) over team_window as velocity_rolling_3,
    count(*) over team_window                     as sprints_in_window,
    coalesce(late.completed_after_end_points, 0)  as completed_after_end_points,
    coalesce(late.completed_after_end_issues, 0)  as completed_after_end_issues
from {{ ref('rpt_sprint_metrics') }} as metrics
left join late
    on late.sprint_id = metrics.sprint_id
window team_window as (
    partition by metrics.team_id
    order by metrics.sprint_number
    rows between 2 preceding and current row
)
