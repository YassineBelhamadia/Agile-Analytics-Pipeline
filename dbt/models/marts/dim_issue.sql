-- One row per issue, with its current status. The status history is the
-- transition fact; sprint membership is its own fact, since an issue may join
-- more than one sprint. Epics keep null story points.
select
    issue_id,
    issue_key,
    issue_type,
    team_id,
    status_id   as current_status_id,
    status_name as current_status_name,
    assignee_user_id,
    reporter_user_id,
    parent_issue_id,
    story_points,
    created_at,
    resolved_at,
    duedate,
    summary
from {{ ref('stg_issues') }}
