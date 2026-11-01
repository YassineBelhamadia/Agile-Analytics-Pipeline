-- sprint_number is the sprint's position in its team by start_at. Jira names
-- the shipped sprints "Sprint N", so the two must agree; a sprint out of order
-- or a gap in the sequence is returned.
select sprint_id, team_key, sprint_name, sprint_number
from {{ ref('rpt_sprint_metrics') }}
where sprint_name <> 'Sprint ' || sprint_number
