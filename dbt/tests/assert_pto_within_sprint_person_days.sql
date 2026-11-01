-- PTO is person-days taken in a sprint, so it lies between 0 and headcount x
-- the sprint's working days. Returns the capacity rows outside that range,
-- which would make available person-days zero or negative in Q2.
select team_key, sprint_id, headcount, pto_days, headcount * {{ var('sprint_working_days') }} as max_person_days
from {{ ref('fct_capacity') }}
where pto_days < 0
   or pto_days > headcount * {{ var('sprint_working_days') }}
