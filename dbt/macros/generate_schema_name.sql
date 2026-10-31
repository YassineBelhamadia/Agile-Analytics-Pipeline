{#
    Use the configured schema name as is.

    dbt's built-in version returns <target schema>_<custom schema> (marts_stg,
    marts_marts). Here a model with `+schema: stg` lands in `stg`, and one with
    `+schema: marts` in `marts`. A node with no custom schema (tests, for
    instance) falls back to the profile's schema.

    The `schema_suffix` var (default empty) lets the pytest suite build into
    stg_test and marts_test, so tests never touch the real schemas.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}{{ var('schema_suffix', '') }}
    {%- else -%}
        {{ custom_schema_name | trim }}{{ var('schema_suffix', '') }}
    {%- endif -%}
{%- endmacro %}
