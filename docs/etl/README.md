# ETL stages

One page per pipeline stage, in run order. Each page uses the template below. The commit plan that builds these stages is [`../etl-plan.md`](../etl-plan.md). Decisions behind them are in [`../decisions.md`](../decisions.md).

| # | Stage | Page | Status |
|---|---|---|---|
| 1 | Extraction: mock API → `landing/` | [01-extraction.md](01-extraction.md) | done |
| 2 | Staging: `landing/` → Postgres `staging` schema | [02-staging.md](02-staging.md) | done (Session 2) |
| 3 | dbt star schema: `staging` → `stg` views → `marts` tables | [03-dbt-star-schema.md](03-dbt-star-schema.md) | done (Session 3) |
| 4 | Sprint metrics and business questions: `marts` → `rpt_` tables | [04-metrics.md](04-metrics.md), [`../metrics/`](../metrics/README.md) | done (Session 4) |
| 5 | One-command pipeline | [05-pipeline.md](05-pipeline.md) | in progress (Session 5) |

Upstream of stage 1, and documented elsewhere:

- simulator → `raw_data/`: [`../domain-model.md`](../domain-model.md)
- mock API: [`../mock-api.md`](../mock-api.md)

## Page template

Copy this for a new stage. Keep every heading. Write "none" rather than deleting one.

```markdown
# NN. <Stage name>

Status: planned | in progress | done

## Purpose
What this stage does and why it exists, in two or three sentences.

## Inputs and outputs
- Inputs: files, tables, environment variables.
- Outputs: files or tables written, and whether they are replaced or appended.

## Schema or contract
Tables and columns (with types and keys), or the file layout and request shapes.

## How to run
Exact commands from the repo root, with prerequisites.

## How it is verified
Tests and checks, what each proves, the expected counts, and the sabotage test that shows a check can fail.

## Known limits
What it does not handle, stated plainly.

## Options rejected and why
Each alternative that was considered, and the reason it lost. Link to decisions.md entries.
```
