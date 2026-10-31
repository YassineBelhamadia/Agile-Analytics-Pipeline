{#
    Fails unless the model has exactly `expected` rows. The targets are the
    shipped dataset's (SEED=22, CLAUDE.md); a new seed means new targets.
#}
{% test expected_row_count(model, expected) %}
select count(*) as actual_rows, {{ expected }} as expected_rows
from {{ model }}
having count(*) <> {{ expected }}
{% endtest %}
