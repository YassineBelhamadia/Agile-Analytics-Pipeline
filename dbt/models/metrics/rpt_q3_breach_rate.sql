-- Q3: deadline breach rate by team, issue type and commitment, with rollups
-- (grain: team_name x issue_type x commitment). "All teams", "All types" and
-- "All" rows hold the totals, so the headline by issue type is the rows with
-- team_name = 'All teams' and commitment = 'All'. Never sum across them.
-- - breach_rate_resolved: late / resolved (the headline, as in D-020);
-- - breach_rate_incl_overdue: (late + open_overdue) / (resolved + open_overdue).
-- open_not_due issues count in neither rate.
select
    case when grouping(team_name) = 1 then 'All teams' else team_name end as team_name,
    case when grouping(team_name) = 1 then 'ALL' else team_key end        as team_key,
    case when grouping(issue_type) = 1 then 'All types' else issue_type end as issue_type,
    case when grouping(commitment) = 1 then 'All' else commitment end    as commitment,
    count(*)                                                            as due_issue_count,
    count(*) filter (where outcome in ('late', 'on_time'))              as resolved_count,
    count(*) filter (where outcome = 'on_time')                         as on_time_count,
    count(*) filter (where outcome = 'late')                            as late_count,
    count(*) filter (where outcome = 'open_overdue')                    as open_overdue_count,
    count(*) filter (where outcome = 'open_not_due')                    as open_not_due_count,
    count(*) filter (where outcome = 'late')::numeric
        / nullif(count(*) filter (where outcome in ('late', 'on_time')), 0) as breach_rate_resolved,
    count(*) filter (where outcome in ('late', 'open_overdue'))::numeric
        / nullif(count(*) filter (where outcome in ('late', 'on_time', 'open_overdue')), 0) as breach_rate_incl_overdue,
    max(as_of_at)                                                       as as_of_at
from {{ ref('rpt_q3_deadline_outcomes') }}
group by cube ((team_name, team_key), issue_type, commitment)
