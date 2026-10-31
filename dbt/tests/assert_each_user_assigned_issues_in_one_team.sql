-- The v1 limitation as a check (D-029): every user is assigned issues in
-- exactly one team. Read from the stg layer, independently of how dim_user
-- derives the team, and lists the teams found so a failure says why.
select
    users.user_id,
    users.display_name,
    count(distinct issues.team_id)                         as teams,
    string_agg(distinct issues.team_id::text, ', ')        as team_ids
from {{ ref('stg_users') }} as users
left join {{ ref('stg_issues') }} as issues
    on issues.assignee_user_id = users.user_id
group by users.user_id, users.display_name
having count(distinct issues.team_id) <> 1
