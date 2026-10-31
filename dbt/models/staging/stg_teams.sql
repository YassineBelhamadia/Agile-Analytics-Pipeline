-- One row per team (Jira project). Renames only.
select
    project_id   as team_id,
    project_key  as team_key,
    project_name as team_name,
    _source_file,
    _loaded_at
from {{ source('staging', 'teams') }}
