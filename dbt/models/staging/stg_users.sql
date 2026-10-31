-- One row per Jira account. The API exposes no team on users (see dim_user).
select
    account_id as user_id,
    display_name,
    _source_file,
    _loaded_at
from {{ source('staging', 'users') }}
