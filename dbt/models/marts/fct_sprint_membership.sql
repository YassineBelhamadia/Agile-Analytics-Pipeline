-- One row per issue joining a sprint (grain: issue_id, sprint_id). added_at is
-- when the Sprint changelog item was written; Session 4 compares it with the
-- sprint's start_at for committed vs. scope added.
select
    issue_id,
    sprint_id,
    added_at,
    changelog_id
from {{ ref('stg_sprint_memberships') }}
