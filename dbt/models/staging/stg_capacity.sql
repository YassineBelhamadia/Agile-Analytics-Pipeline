-- One row per team and sprint. Our own schema: the team is a project key, not an ID.
select
    team_key,
    sprint_id,
    headcount,
    pto_days,
    _source_file,
    _loaded_at
from {{ source('staging', 'capacity') }}
