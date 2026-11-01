-- In rpt_q3_breach_rate, each rollup row equals the sum of the rows it rolls
-- up, along each of the three dimensions, and the grand total is every issue
-- in rpt_q3_deadline_outcomes. Returns the rollups that differ.
with rates as (
    select * from {{ ref('rpt_q3_breach_rate') }}
),

detail as (
    select team_name, issue_type, commitment,
           due_issue_count, late_count, on_time_count, open_overdue_count, open_not_due_count
    from rates
),

checks as (
    select 'team' as rolled_up, r.issue_type, r.commitment,
           r.due_issue_count as total, sum(d.due_issue_count) as parts,
           r.late_count as total_late, sum(d.late_count) as parts_late,
           r.open_overdue_count as total_overdue, sum(d.open_overdue_count) as parts_overdue
    from rates r join detail d
      on d.team_name <> 'All teams' and d.issue_type = r.issue_type and d.commitment = r.commitment
    where r.team_name = 'All teams'
    group by r.issue_type, r.commitment, r.due_issue_count, r.late_count, r.open_overdue_count

    union all

    select 'issue_type', r.team_name, r.commitment,
           r.due_issue_count, sum(d.due_issue_count), r.late_count, sum(d.late_count),
           r.open_overdue_count, sum(d.open_overdue_count)
    from rates r join detail d
      on d.issue_type <> 'All types' and d.team_name = r.team_name and d.commitment = r.commitment
    where r.issue_type = 'All types'
    group by r.team_name, r.commitment, r.due_issue_count, r.late_count, r.open_overdue_count

    union all

    select 'commitment', r.team_name, r.issue_type,
           r.due_issue_count, sum(d.due_issue_count), r.late_count, sum(d.late_count),
           r.open_overdue_count, sum(d.open_overdue_count)
    from rates r join detail d
      on d.commitment <> 'All' and d.team_name = r.team_name and d.issue_type = r.issue_type
    where r.commitment = 'All'
    group by r.team_name, r.issue_type, r.due_issue_count, r.late_count, r.open_overdue_count

    union all

    select 'grand total', null, null,
           (select due_issue_count from rates where team_name = 'All teams' and issue_type = 'All types' and commitment = 'All'),
           (select count(*) from {{ ref('rpt_q3_deadline_outcomes') }}),
           0, 0, 0, 0
)

select * from checks
where total is distinct from parts
   or total_late is distinct from parts_late
   or total_overdue is distinct from parts_overdue
