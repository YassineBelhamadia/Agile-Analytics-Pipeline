-- One row per issue. status_* is the current status; the history is in stg_status_transitions.
select
    issue_id,
    issue_key,
    issue_type,
    project_id          as team_id,
    status_id,
    status_name,
    assignee_account_id as assignee_user_id,
    reporter_account_id as reporter_user_id,
    parent_issue_id,
    story_points,
    created_at,
    resolved_at,
    duedate,
    summary,
    _source_file,
    _loaded_at
from {{ source('staging', 'issues') }}
