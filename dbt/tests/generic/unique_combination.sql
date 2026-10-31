{#
    Fails for every combination of `columns` that appears more than once.
    For composite grains (fct_sprint_membership, fct_capacity); written here
    instead of pulling in dbt_utils for one test (D-031).
#}
{% test unique_combination(model, columns) %}
select {{ columns | join(', ') }}, count(*) as occurrences
from {{ model }}
group by {{ columns | join(', ') }}
having count(*) > 1
{% endtest %}
