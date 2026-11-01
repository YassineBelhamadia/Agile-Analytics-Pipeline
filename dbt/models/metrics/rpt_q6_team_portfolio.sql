-- Q6: cross-team portfolio, one row per team (grain: team_id) over all its
-- sprints. Every value is read from the Q1 to Q5 models and rpt_sprint_metrics,
-- never recomputed, so the portfolio cannot disagree with them (D-042).
-- Size-dependent figures are normalized per available person-day; rates,
-- ratios and durations are already per issue or per sprint.
with sprints as (
    select
        team_id,
        count(*)                                     as sprint_count,
        count(*) filter (where is_hit)               as hit_count,
        count(*) filter (where is_hit)::numeric / count(*) as hit_rate
    from {{ ref('rpt_sprint_metrics') }}
    group by team_id
),

velocity as (
    select team_id, avg(velocity_points) as velocity_per_sprint
    from {{ ref('rpt_q1_velocity') }}
    group by team_id
),

capacity as (
    select
        team_id,
        avg(headcount)                                    as mean_headcount,
        sum(available_person_days)                        as available_person_days,
        sum(pto_days) / sum(headcount * {{ var('sprint_working_days') }}) as pto_share,
        sum(completed_points) / sum(available_person_days) as points_per_person_day
    from {{ ref('rpt_q2_velocity_vs_capacity') }}
    group by team_id
),

breach as (
    select team_key, breach_rate_resolved, breach_rate_incl_overdue, open_overdue_count
    from {{ ref('rpt_q3_breach_rate') }}
    where issue_type = 'All types' and commitment = 'All' and team_key <> 'ALL'
),

status_time as (
    select
        team_key,
        max(median_hours) filter (where status_name = 'To Do')       as median_hours_to_do,
        max(median_hours) filter (where status_name = 'In Progress') as median_hours_in_progress,
        max(median_hours) filter (where status_name = 'Code Review') as median_hours_code_review,
        max(median_hours) filter (where status_name = 'QA')          as median_hours_qa
    from {{ ref('rpt_q4_time_in_status') }}
    where team_key <> 'ALL'
    group by team_key
),

bug_ratio as (
    select team_id, sum(bug_count)::numeric / nullif(sum(story_count), 0) as bug_to_story_ratio
    from {{ ref('rpt_q5_bug_to_story_ratio') }}
    group by team_id
)

select
    teams.team_id,
    teams.team_key,
    teams.team_name,
    sprints.sprint_count,
    capacity.mean_headcount,
    capacity.available_person_days,
    capacity.pto_share,
    velocity.velocity_per_sprint,
    capacity.points_per_person_day,
    sprints.hit_count,
    sprints.hit_rate,
    breach.breach_rate_resolved,
    breach.breach_rate_incl_overdue,
    breach.open_overdue_count,
    status_time.median_hours_to_do,
    status_time.median_hours_in_progress,
    status_time.median_hours_code_review,
    status_time.median_hours_qa,
    bug_ratio.bug_to_story_ratio
from {{ ref('dim_team') }} as teams
left join sprints     on sprints.team_id = teams.team_id
left join velocity    on velocity.team_id = teams.team_id
left join capacity    on capacity.team_id = teams.team_id
left join breach      on breach.team_key = teams.team_key
left join status_time on status_time.team_key = teams.team_key
left join bug_ratio   on bug_ratio.team_id = teams.team_id
