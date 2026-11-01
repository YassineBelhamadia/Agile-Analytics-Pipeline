-- Every membership is counted in exactly one sprint row, and committed plus
-- scope added is every member's points: a membership split between the two
-- branches, or dropped by both, shows up here. Returns the totals that differ.
with metrics as (
    select sum(issue_count) as issues, sum(committed_points + scope_added_points) as points
    from {{ ref('rpt_sprint_metrics') }}
),

memberships as (
    select count(*) as issues, coalesce(sum(i.story_points), 0) as points
    from {{ ref('fct_sprint_membership') }} as m
    join {{ ref('dim_issue') }} as i on i.issue_id = m.issue_id
)

select metrics.issues as metric_issues, memberships.issues as membership_issues,
       metrics.points as metric_points, memberships.points as membership_points
from metrics cross join memberships
where metrics.issues <> memberships.issues
   or metrics.points <> memberships.points
