-- One row per sprint. Start, end and complete instants are UTC timestamptz.
select
    sprint_id,
    board_id,
    sprint_name,
    state as sprint_state,
    start_at,
    end_at,
    complete_at,
    _source_file,
    _loaded_at
from {{ source('staging', 'sprints') }}
