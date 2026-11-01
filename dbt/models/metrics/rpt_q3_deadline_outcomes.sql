-- Q3 detail: one row per issue with a due date (grain: issue_id), with its
-- deadline outcome (docs/domain-model.md, "Deadline (duedate) rule"):
-- - late:         resolved on a later UTC date than its duedate;
-- - on_time:      resolved on or before its duedate;
-- - open_overdue: unresolved at the as-of instant, with a duedate before that day;
-- - open_not_due: unresolved and not yet due (censored, in no rate).
-- The sprint is the first one the issue joined, which set the duedate; it
-- decides committed vs. scope added. Issues without a duedate (Epics, never
-- scheduled) are not in this table.
with first_membership as (
    select distinct on (issue_id)
        issue_id,
        sprint_id,
        added_at
    from {{ ref('fct_sprint_membership') }}
    order by issue_id, added_at, sprint_id
)

select
    issues.issue_id,
    issues.issue_key,
    issues.issue_type,
    sprints.team_id,
    sprints.team_key,
    sprints.team_name,
    sprints.sprint_id,
    sprints.sprint_name,
    sprints.sprint_number,
    case when first_membership.added_at <= sprints.start_at then 'committed' else 'scope_added' end as commitment,
    issues.duedate,
    issues.resolved_at,
    (issues.resolved_at at time zone 'UTC')::date as resolved_date,
    case
        when issues.resolved_at is not null then
            case when (issues.resolved_at at time zone 'UTC')::date > issues.duedate then 'late' else 'on_time' end
        when issues.duedate < as_of.as_of_date then 'open_overdue'
        else 'open_not_due'
    end as outcome,
    as_of.as_of_at
from {{ ref('dim_issue') }} as issues
left join first_membership
    on first_membership.issue_id = issues.issue_id
left join {{ ref('rpt_sprint_metrics') }} as sprints
    on sprints.sprint_id = first_membership.sprint_id
cross join {{ ref('rpt_data_as_of') }} as as_of
where issues.duedate is not null
