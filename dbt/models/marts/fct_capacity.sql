-- One row per team and sprint (grain: team_id, sprint_id). /capacity names the
-- team by project key, so team_id is looked up in dim_team; a left join keeps
-- an unknown key as a null team_id, which fails not_null.
select
    teams.team_id,
    capacity.team_key,
    capacity.sprint_id,
    capacity.headcount,
    capacity.pto_days
from {{ ref('stg_capacity') }} as capacity
left join {{ ref('dim_team') }} as teams
    on teams.team_key = capacity.team_key
