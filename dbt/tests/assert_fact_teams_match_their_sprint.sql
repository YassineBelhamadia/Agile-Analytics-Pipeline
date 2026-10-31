-- A membership links an issue to a sprint of the issue's own team, and a
-- capacity row's team is its sprint's team. Returns the rows where they differ.
select 'fct_sprint_membership' as fact, m.issue_id as row_key, m.sprint_id, i.team_id as fact_team_id, s.team_id as sprint_team_id
from {{ ref('fct_sprint_membership') }} as m
join {{ ref('dim_issue') }}  as i on i.issue_id = m.issue_id
join {{ ref('dim_sprint') }} as s on s.sprint_id = m.sprint_id
where i.team_id is distinct from s.team_id

union all

select 'fct_capacity', null, c.sprint_id, c.team_id, s.team_id
from {{ ref('fct_capacity') }} as c
join {{ ref('dim_sprint') }} as s on s.sprint_id = c.sprint_id
where c.team_id is distinct from s.team_id
