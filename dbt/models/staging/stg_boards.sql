-- One row per agile board, with the project (team) it belongs to.
select
    board_id,
    board_name,
    board_type,
    project_id  as team_id,
    project_key as team_key,
    _source_file,
    _loaded_at
from {{ source('staging', 'boards') }}
