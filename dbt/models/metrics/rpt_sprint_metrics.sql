-- One row per sprint: committed, scope added and completed story points, the
-- sprint hit, and the bug and issue counts of its members. The same figures as
-- simulator/sprint_report.py, the answer key (D-034).
--
-- Exact instants, never calendar days (D-012):
-- - committed:   joined at or before the sprint start (added_at <= start_at);
-- - scope added: joined after it (added_at > start_at);
-- - completed:   member issues resolved at or before the sprint end (resolved_at <= end_at),
--                whenever they joined. Not complete_at, which is derived (D-032).
-- A sprint's members come from fct_sprint_membership, not from event_sprint_id:
-- an issue finished after its sprint ended still belongs to that sprint (no carryover).
-- Left joins keep a sprint with no members, at zero points.
with members as (
    select
        membership.sprint_id,
        membership.added_at,
        issues.issue_type,
        issues.story_points,
        issues.resolved_at
    from {{ ref('fct_sprint_membership') }} as membership
    join {{ ref('dim_issue') }} as issues
        on issues.issue_id = membership.issue_id
),

per_sprint as (
    select
        sprints.sprint_id,
        coalesce(sum(members.story_points) filter (where members.added_at <= sprints.start_at), 0) as committed_points,
        coalesce(sum(members.story_points) filter (where members.added_at > sprints.start_at), 0)  as scope_added_points,
        coalesce(sum(members.story_points) filter (where members.resolved_at <= sprints.end_at), 0) as completed_points,
        count(members.sprint_id) filter (where members.issue_type = 'Bug')                         as bug_count,
        count(members.sprint_id)                                                                   as issue_count
    from {{ ref('dim_sprint') }} as sprints
    left join members
        on members.sprint_id = sprints.sprint_id
    group by sprints.sprint_id
)

select
    sprints.sprint_id,
    sprints.team_id,
    teams.team_key,
    teams.team_name,
    sprints.sprint_name,
    row_number() over (partition by sprints.team_id order by sprints.start_at) as sprint_number,
    sprints.start_at,
    sprints.end_at,
    per_sprint.committed_points,
    per_sprint.scope_added_points,
    per_sprint.completed_points,
    per_sprint.completed_points >= per_sprint.committed_points as is_hit,
    per_sprint.bug_count,
    per_sprint.issue_count
from {{ ref('dim_sprint') }} as sprints
join per_sprint
    on per_sprint.sprint_id = sprints.sprint_id
left join {{ ref('dim_team') }} as teams
    on teams.team_id = sprints.team_id
