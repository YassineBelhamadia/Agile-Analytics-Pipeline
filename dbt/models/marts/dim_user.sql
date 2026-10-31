-- One row per Jira account. The API exposes no team on users, so a user's team
-- is the team of the issues assigned to them (D-029). It is null unless there
-- is exactly one such team; the not_null test on team_id then fails, so a user
-- spread over two teams (or assigned nothing) is reported, never resolved by
-- picking one.
with assigned_teams as (
    select
        assignee_user_id        as user_id,
        count(distinct team_id) as team_count,
        min(team_id)            as team_id
    from {{ ref('stg_issues') }}
    where assignee_user_id is not null
    group by assignee_user_id
)

select
    users.user_id,
    users.display_name,
    case when assigned_teams.team_count = 1 then assigned_teams.team_id end as team_id
from {{ ref('stg_users') }} as users
left join assigned_teams
    on assigned_teams.user_id = users.user_id
