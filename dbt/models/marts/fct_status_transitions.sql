-- One row per status change (the changelog history ID). The team is the issue's.
-- event_sprint_id is the sprint of the issue's team whose window contained the
-- transition, half-open (start_at <= transitioned_at < end_at): one sprint's end
-- is the next one's start, so a boundary instant belongs to the later sprint
-- only. It is not the issue's membership sprint (that is fct_sprint_membership):
-- work that continues after its sprint ended lands in the sprint it happened in.
-- Left joins keep every transition; a missing issue or window shows up as a
-- null key and fails a not_null test instead of dropping the row.
select
    transitions.changelog_id,
    transitions.issue_id,
    issues.team_id,
    transitions.author_user_id,
    sprints.sprint_id as event_sprint_id,
    transitions.transitioned_at,
    transitions.from_status_id,
    transitions.from_status_name,
    transitions.to_status_id,
    transitions.to_status_name
from {{ ref('stg_status_transitions') }} as transitions
left join {{ ref('stg_issues') }} as issues
    on issues.issue_id = transitions.issue_id
left join {{ ref('dim_sprint') }} as sprints
    on  sprints.team_id = issues.team_id
    and sprints.start_at <= transitions.transitioned_at
    and transitions.transitioned_at < sprints.end_at
