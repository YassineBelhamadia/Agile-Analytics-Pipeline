-- One row per status change, keyed by the changelog history ID.
select
    changelog_id,
    issue_id,
    author_account_id as author_user_id,
    transitioned_at,
    from_status_id,
    from_status_name,
    to_status_id,
    to_status_name,
    _source_file,
    _loaded_at
from {{ source('staging', 'status_transitions') }}
