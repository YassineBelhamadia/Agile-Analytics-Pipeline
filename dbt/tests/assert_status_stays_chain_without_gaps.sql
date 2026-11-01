-- Each issue's stays tile its history: a completed stay ends exactly when the
-- next one starts, no duration is negative, and every issue has one last stay
-- (open or terminal). Returns the stays that break this.
with stays as (
    select
        issue_id, stay_number, entered_at, left_at, stay_kind, duration_hours,
        lead(entered_at) over (partition by issue_id order by stay_number) as next_entered_at
    from {{ ref('rpt_q4_status_stays') }}
)

select issue_id, stay_number, stay_kind, entered_at, left_at, next_entered_at, duration_hours
from stays
where left_at is distinct from next_entered_at
   or duration_hours < 0
   or (next_entered_at is null and stay_kind = 'completed')
