-- Q4: time per status, by team (grain: team_name x status_name), plus "All
-- teams" rows. Mean, median and 90th percentile (continuous, interpolated) of
-- completed stays in calendar hours. Open stays are counted, with their median
-- age at the as-of instant, but never mixed into the statistics: their
-- durations are not known yet (D-040). Done is terminal and has no row.
select
    case when grouping(team_name) = 1 then 'All teams' else team_name end as team_name,
    case when grouping(team_name) = 1 then 'ALL' else team_key end        as team_key,
    status_name,
    status_order,
    count(*) filter (where stay_kind = 'completed')                         as stay_count,
    count(distinct issue_id) filter (where stay_kind = 'completed')         as issue_count,
    count(*) filter (where stay_kind = 'completed' and visit_number > 1)    as repeat_visit_count,
    avg(duration_hours)                                                     as mean_hours,
    percentile_cont(0.5) within group (order by duration_hours)             as median_hours,
    percentile_cont(0.9) within group (order by duration_hours)             as p90_hours,
    count(*) filter (where stay_kind = 'open')                              as open_stay_count,
    percentile_cont(0.5) within group (order by open_age_hours)             as open_median_age_hours
from {{ ref('rpt_q4_status_stays') }}
where stay_kind <> 'terminal'
group by grouping sets ((team_name, team_key, status_name, status_order), (status_name, status_order))
