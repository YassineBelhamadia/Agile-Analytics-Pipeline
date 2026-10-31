-- Every non-Epic issue has story points, and no Epic has any (D-010).
-- Returns the issues that break either half.
select issue_key, issue_type, story_points
from {{ ref('dim_issue') }}
where (issue_type = 'Epic') <> (story_points is null)
