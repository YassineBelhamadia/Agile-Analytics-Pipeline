-- One row: the last instant anything happened in the data (D-036). Derived from
-- every event timestamp (issue created and resolved, status transitions, sprint
-- joins), never hardcoded. Q3 measures "open and overdue" at this instant.
-- Scheduled sprint ends are not events: the last sprint's end_at lies after it.
with events as (
    select max(created_at) as at from {{ ref('dim_issue') }}
    union all
    select max(resolved_at) from {{ ref('dim_issue') }}
    union all
    select max(transitioned_at) from {{ ref('fct_status_transitions') }}
    union all
    select max(added_at) from {{ ref('fct_sprint_membership') }}
)

select
    max(at)                                as as_of_at,
    (max(at) at time zone 'UTC')::date     as as_of_date
from events
