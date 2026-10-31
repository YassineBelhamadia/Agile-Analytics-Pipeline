# Stack

The tools for the pipeline are locked. This page records what each one does here, why it was chosen, and what was deliberately left for later. Changing a locked choice needs a written reason in `docs/decisions.md` first.

## Locked

| Layer | Tool | Why |
|---|---|---|
| Extraction and parsing | **Python** | The simulator and mock API are already Python, so one language covers the whole upstream half. The HTTP client, pagination and JSON flattening are ordinary Python, with `httpx` and the standard library. |
| Warehouse | **PostgreSQL in Docker** | Free, standard SQL, and `jsonb` lets raw API responses land untouched before staging. Docker Compose gives every machine, and CI, the same database from one file. |
| Transformations and tests | **dbt Core** | Models are plain SQL with an explicit dependency graph, plus built-in data tests (unique, not null, relationships) and generated docs. Free, and a standard skill for analytics engineering roles. |
| CI | **GitHub Actions** | Lives next to the code, free for this repository, and can start a Postgres service container so the pipeline and dbt tests run against a real database on every push. |
| Dashboard | **Power BI** | Connects directly to PostgreSQL, and Power BI Desktop is free. It is a common requirement in the analytics roles this portfolio targets. |

Versions in use: dbt-core 1.12 with the dbt-postgres 1.11 adapter, both added with uv ([D-026](decisions.md#d-026-dbt-core-112-with-dbt-postgres-111-in-dbt-profile-from-the-environment)).

Supporting tools, also fixed: `uv` for dependencies, `pytest` for tests, FastAPI and uvicorn for the mock API.

**Constraint to plan for:** Power BI Desktop runs only on Windows. On macOS, building the dashboard needs a Windows VM or machine. The Power BI web service can view and lightly edit reports, but it is not a full replacement for Desktop.

## Deferred on purpose

- **Orchestration** (Airflow, Dagster, Prefect or similar). v1 runs as one command from an empty database (ETL Session 5). A scheduler adds infrastructure to run and explain without making v1 more correct. Revisit once the pipeline works end to end.
- **The dbt vs. plain SQL tradeoff.** dbt is the v1 choice. The written comparison is deferred until the dbt project exists, so it can rest on what dbt actually cost and saved here rather than on general claims. That means its setup overhead and learning curve against tests, lineage and docs, compared with versioned SQL scripts run in order.
