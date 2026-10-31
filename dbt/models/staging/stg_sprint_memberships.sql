-- One row per issue joining a sprint; added_at is the Sprint changelog item's time.
select
    issue_id,
    sprint_id,
    added_at,
    changelog_id,
    _source_file,
    _loaded_at
from {{ source('staging', 'sprint_memberships') }}
