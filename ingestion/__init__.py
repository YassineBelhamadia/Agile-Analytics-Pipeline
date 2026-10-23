"""Extraction (and, later, parsing) of the mock API into the warehouse.

Inputs: the mock API (or a Jira site) reached over HTTP.
Outputs: raw response pages and a manifest under landing/ (`extract`);
staging tables in Postgres are planned (docs/etl-plan.md, Session 2).

Modules:
    client: authenticated GETs, retries and the two pagination styles.
    extract: discovery order, landing layout and manifest.
"""
