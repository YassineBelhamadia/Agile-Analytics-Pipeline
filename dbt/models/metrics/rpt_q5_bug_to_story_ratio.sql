-- Q5: bug-to-story ratio by team over time (grain: team_id x period_number).
-- Counts every created issue (D-020), scheduled or not, by when it was created.
-- The period is the team's sprint window the issue was created in
-- (start_at <= created_at < end_at), and period 0 ("Before Sprint 1") is the
-- backlog build-up before the first sprint. In the shipped data that is 13
-- two-week periods covering the whole span (D-041).
with issues as (
    select issue_id, issue_type, team_id, created_at
    from {{ ref('dim_issue') }}
    where issue_type in ('Bug', 'Story', 'Task')
),

periods as (
    select team_id, team_key, team_name, sprint_id, sprint_name, sprint_number as period_number, start_at, end_at
    from {{ ref('rpt_sprint_metrics') }}

    union all

    select team_id, team_key, team_name, null, 'Before Sprint 1', 0, null, min(start_at)
    from {{ ref('rpt_sprint_metrics') }}
    group by team_id, team_key, team_name
),

counts as (
    select
        periods.team_id,
        periods.period_number,
        count(issues.issue_id) filter (where issues.issue_type = 'Bug')   as bug_count,
        count(issues.issue_id) filter (where issues.issue_type = 'Story') as story_count,
        count(issues.issue_id) filter (where issues.issue_type = 'Task')  as task_count
    from periods
    left join issues
        on  issues.team_id = periods.team_id
        and issues.created_at < periods.end_at
        and (periods.start_at is null or issues.created_at >= periods.start_at)
    group by periods.team_id, periods.period_number
)

select
    periods.team_id,
    periods.team_key,
    periods.team_name,
    periods.period_number,
    periods.sprint_id,
    periods.sprint_name,
    periods.start_at,
    periods.end_at,
    counts.bug_count,
    counts.story_count,
    counts.task_count,
    counts.bug_count::numeric / nullif(counts.story_count, 0) as bug_to_story_ratio,
    sum(counts.bug_count) over team_to_date
        / nullif(sum(counts.story_count) over team_to_date, 0) as cumulative_bug_to_story_ratio
from periods
join counts
    on  counts.team_id = periods.team_id
    and counts.period_number = periods.period_number
window team_to_date as (
    partition by periods.team_id
    order by periods.period_number
    rows between unbounded preceding and current row
)
