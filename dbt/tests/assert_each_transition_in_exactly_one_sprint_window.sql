-- Every status transition falls in exactly one sprint window of its issue's
-- team (start_at <= transitioned_at < end_at). Zero windows means a gap or a
-- transition outside every sprint; two means overlapping sprints. Either would
-- make event_sprint_id null or duplicate the row. Returns the offending rows.
select
    transitions.changelog_id,
    transitions.transitioned_at,
    count(sprints.sprint_id) as matching_windows
from {{ ref('stg_status_transitions') }} as transitions
left join {{ ref('stg_issues') }} as issues
    on issues.issue_id = transitions.issue_id
left join {{ ref('dim_sprint') }} as sprints
    on  sprints.team_id = issues.team_id
    and sprints.start_at <= transitions.transitioned_at
    and transitions.transitioned_at < sprints.end_at
group by transitions.changelog_id, transitions.transitioned_at
having count(sprints.sprint_id) <> 1
