-- Inner joining the transition fact to all four dimensions keeps every row
-- exactly once: no transition is lost to a missing key, none is duplicated by a
-- non-unique one. Returns a row only when the counts differ.
with joined as (
    select count(*) as joined_rows
    from {{ ref('fct_status_transitions') }} as f
    join {{ ref('dim_issue') }}  as i on i.issue_id = f.issue_id
    join {{ ref('dim_team') }}   as t on t.team_id = f.team_id
    join {{ ref('dim_user') }}   as u on u.user_id = f.author_user_id
    join {{ ref('dim_sprint') }} as s on s.sprint_id = f.event_sprint_id
)

select joined.joined_rows, facts.fact_rows
from joined
cross join (select count(*) as fact_rows from {{ ref('fct_status_transitions') }}) as facts
where joined.joined_rows <> facts.fact_rows
