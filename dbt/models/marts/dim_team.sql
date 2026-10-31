-- One row per team (Jira project). Natural key: team_id (the Jira project ID).
-- team_key is also unique: capacity uses it, since /capacity has no project ID.
select
    team_id,
    team_key,
    team_name
from {{ ref('stg_teams') }}
