-- Every non-Epic issue falls in exactly one Q5 period: the counts add up to the
-- issues in dim_issue, so an issue created outside every window (or in two) is
-- reported. Returns the totals when they differ.
with periods as (
    select sum(bug_count) as bugs, sum(story_count) as stories, sum(task_count) as tasks
    from {{ ref('rpt_q5_bug_to_story_ratio') }}
),

issues as (
    select
        count(*) filter (where issue_type = 'Bug')   as bugs,
        count(*) filter (where issue_type = 'Story') as stories,
        count(*) filter (where issue_type = 'Task')  as tasks
    from {{ ref('dim_issue') }}
)

select periods.*, issues.bugs as issue_bugs, issues.stories as issue_stories, issues.tasks as issue_tasks
from periods cross join issues
where (periods.bugs, periods.stories, periods.tasks) <> (issues.bugs, issues.stories, issues.tasks)
