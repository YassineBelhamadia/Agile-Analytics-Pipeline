-- is_rollup is true exactly on the rows with a rollup label, in both tables
-- that mix totals with detail rows (D-047). Returns the rows that disagree.
select 'rpt_q3_breach_rate' as model, team_name, is_rollup
from {{ ref('rpt_q3_breach_rate') }}
where is_rollup is distinct from (team_name = 'All teams' or issue_type = 'All types' or commitment = 'All')

union all

select 'rpt_q4_time_in_status', team_name, is_rollup
from {{ ref('rpt_q4_time_in_status') }}
where is_rollup is distinct from (team_name = 'All teams')
