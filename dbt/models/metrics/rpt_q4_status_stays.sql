-- Q4 detail: one row per stay of a non-Epic issue in a status (grain: issue_id,
-- stay_number). A stay starts when the issue enters the status (creation, for
-- the first one) and ends at the issue's next transition. A bounce back to an
-- earlier status is a new stay, numbered by visit_number. Epics are excluded
-- (D-040): they never change status.
-- stay_kind:
-- - completed: ended by a transition; duration_hours is set. Only these feed the statistics;
-- - open:      the current status of an unresolved issue, still running at the as-of instant;
--              open_age_hours is how long it has run so far (a lower bound, not a duration);
-- - terminal:  Done, which no issue leaves in v1; not a working status.
-- Durations are calendar hours, weekends and nights included (D-040).
with issues as (
    select issue_id, issue_key, issue_type, team_id, created_at, current_status_name
    from {{ ref('dim_issue') }}
    where issue_type <> 'Epic'
),

ordered_transitions as (
    select
        transitions.issue_id,
        transitions.from_status_name,
        transitions.to_status_name,
        transitions.transitioned_at,
        row_number() over (
            partition by transitions.issue_id
            order by transitions.transitioned_at, transitions.changelog_id
        ) as transition_number
    from {{ ref('fct_status_transitions') }} as transitions
    join issues
        on issues.issue_id = transitions.issue_id
),

entries as (
    -- The first stay: the status the issue was created in, from its creation.
    select
        issues.issue_id,
        0 as stay_number,
        coalesce(first_transition.from_status_name, issues.current_status_name) as status_name,
        issues.created_at as entered_at
    from issues
    left join ordered_transitions as first_transition
        on  first_transition.issue_id = issues.issue_id
        and first_transition.transition_number = 1

    union all

    -- Every later stay: the status a transition moved the issue into.
    select issue_id, transition_number, to_status_name, transitioned_at
    from ordered_transitions
),

stays as (
    select
        entries.*,
        lead(entries.entered_at) over (partition by entries.issue_id order by entries.stay_number) as left_at,
        row_number() over (partition by entries.issue_id, entries.status_name order by entries.stay_number) as visit_number
    from entries
),

first_membership as (
    select distinct on (issue_id) issue_id, sprint_id
    from {{ ref('fct_sprint_membership') }}
    order by issue_id, added_at, sprint_id
)

select
    stays.issue_id,
    stays.stay_number,
    issues.issue_key,
    issues.issue_type,
    issues.team_id,
    teams.team_key,
    teams.team_name,
    sprints.sprint_id,
    sprints.sprint_name,
    sprints.sprint_number,
    stays.status_name,
    case stays.status_name
        when 'To Do' then 1 when 'In Progress' then 2 when 'Code Review' then 3 when 'QA' then 4 when 'Done' then 5
    end as status_order,
    stays.visit_number,
    stays.entered_at,
    stays.left_at,
    case
        when stays.left_at is not null then 'completed'
        when stays.status_name = 'Done' then 'terminal'
        else 'open'
    end as stay_kind,
    extract(epoch from stays.left_at - stays.entered_at) / 3600 as duration_hours,
    case
        when stays.left_at is null and stays.status_name <> 'Done'
            then extract(epoch from as_of.as_of_at - stays.entered_at) / 3600
    end as open_age_hours
from stays
join issues
    on issues.issue_id = stays.issue_id
left join {{ ref('dim_team') }} as teams
    on teams.team_id = issues.team_id
left join first_membership
    on first_membership.issue_id = stays.issue_id
left join {{ ref('rpt_sprint_metrics') }} as sprints
    on sprints.sprint_id = first_membership.sprint_id
cross join {{ ref('rpt_data_as_of') }} as as_of
