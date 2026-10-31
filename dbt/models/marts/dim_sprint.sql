-- One row per sprint. The team comes from the sprint's board (originBoardId).
-- start_at and end_at are the exact 09:00 UTC instants the sprint metrics compare against.
select
    sprints.sprint_id,
    boards.team_id,
    sprints.board_id,
    sprints.sprint_name,
    sprints.sprint_state,
    sprints.start_at,
    sprints.end_at,
    sprints.complete_at
from {{ ref('stg_sprints') }} as sprints
left join {{ ref('stg_boards') }} as boards
    on boards.board_id = sprints.board_id
