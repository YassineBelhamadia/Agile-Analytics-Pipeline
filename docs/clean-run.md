# Clean-run guide

How to go from a fresh clone and an empty database to the built and tested `rpt_` tables, and how to check that every count is right. The result of the last acceptance run is recorded in [`etl/05-pipeline.md`](etl/05-pipeline.md#clean-run-acceptance).

## Prerequisites

Linux or WSL2 Ubuntu, [Docker](https://docs.docker.com/engine/install/) with Compose, and [uv](https://docs.astral.sh/uv/). Port 5433 (Postgres) and port 8000 (mock API) must be free.

**Check your shell first.** No `POSTGRES_*` or `AAP_*` variable should be exported:

```bash
env | grep -E '^(POSTGRES_|AAP_)'     # should print nothing
```

`uv run --env-file .env` does not override a variable already in the shell, and Compose gives the shell priority too. An exported `POSTGRES_PORT` therefore points the pipeline at another database. The first line of a run names its target, for example `Target database: aap_warehouse on localhost:5433`, but the run does not wait for you to read it.

## Steps

All commands run from the repository root.

```bash
# 1. Clone, configure, install
git clone https://github.com/YassineBelhamadia/Agile-Analytics-Pipeline.git
cd Agile-Analytics-Pipeline
cp .env.example .env      # then replace the two placeholders with any local values
uv sync

# 2. Generate the simulated ground truth (seeded: the same files on every run)
uv run python scripts/generate_raw_data.py

# 3. Start Postgres
docker compose --env-file .env -f warehouse/docker-compose.yml up -d --wait

# 4. Run the whole pipeline
uv run --env-file .env python -m pipeline.run

# 5. Run the full test suite (Postgres tests use their own schemas and databases)
uv run --env-file .env python -m pytest -rs
```

The pipeline starts and stops the mock API itself. Its exit codes and what a failed run leaves behind are in [`etl/05-pipeline.md`](etl/05-pipeline.md#schema-or-contract).

## What success looks like

`pipeline.run` starts with the `Target database:` line, exits 0, and ends with a summary of the seconds per stage and the counts per layer. `dbt build`, inside it, ends with `PASS=254 WARN=0 ERROR=0`.

| Layer | Expected |
|---|---|
| `raw_data/` | 3 teams, 12 users, 36 sprints, 36 capacity rows, 530 issues, 1,488 status transitions, 320 sprint memberships |
| `landing/` | 579 files: 578 pages and `manifest.json` |
| `staging` (8 tables) | teams 3, users 12, boards 3, sprints 36, capacity 36, issues 530, status_transitions 1,488, sprint_memberships 320 |
| `marts`: dimensions | `dim_team` 3, `dim_user` 12, `dim_sprint` 36, `dim_issue` 530 (320 with a due date, 210 without) |
| `marts`: facts | `fct_status_transitions` 1,488, `fct_sprint_membership` 320, `fct_capacity` 36 |
| `marts`: `rpt_` tables (10) | `rpt_sprint_metrics` 36, `rpt_data_as_of` 1, `rpt_q1_velocity` 36, `rpt_q2_velocity_vs_capacity` 36, `rpt_q3_deadline_outcomes` 320, `rpt_q3_breach_rate` 48 (18 detail, 30 rollup), `rpt_q4_status_stays` 1,988, `rpt_q4_time_in_status` 16 (12 detail, 4 rollup), `rpt_q5_bug_to_story_ratio` 39, `rpt_q6_team_portfolio` 3 |
| Headline figures | sprints hit: Phoenix 5, Nomad 6, Atlas 9. Breach rate, resolved only: Story 20.4%, Task 28.9%, Bug 38.8% |
| `pytest -rs` | 140 passed, 0 skipped |

## How long it takes

Measured on 2026-11-06 with the Docker image and the uv cache already downloaded. The times depend on the machine: this one has 16 logical cores and 15 GB of memory under WSL2.

| Step | Time |
|---|---|
| `uv sync` | 0.2 s |
| `generate_raw_data.py` | 0.8 s |
| `docker compose ... up -d --wait` | 6.5 s |
| `pipeline.run`, on an empty database | 16.9 s |
| `pytest -rs` | 319.74 s (5 min 20 s), with 140 tests |

A first run also downloads the Postgres image and the Python packages.

## Trying it without touching an existing warehouse

Use a separate Compose project and port in the fresh clone. In that clone's `.env`, set `POSTGRES_PORT=5434`, then:

```bash
env | grep -E '^(POSTGRES_|AAP_)'     # must print nothing, or this run reaches the other database
docker compose --env-file .env -p aap-cleanrun -f warehouse/docker-compose.yml up -d --wait
docker ps --filter name=aap-cleanrun  # must show 127.0.0.1:5434, healthy
uv run --env-file .env python -m pipeline.run
```

The first line of the run must say `on localhost:5434`. If it names another port, stop the run at once with Ctrl-C.

Stop it with the same compose command and `down`. Its data stays in the volume `aap-cleanrun_pgdata` until you remove that volume.

## Starting over

- **Rerun.** `pipeline.run` is safe to repeat: it replaces `landing/`, reloads `staging`, and rebuilds `stg` and `marts`.
- **A truly empty database.** Stop Postgres and delete the volume, which **deletes all warehouse data**:

  ```bash
  docker compose --env-file .env -f warehouse/docker-compose.yml down -v
  ```

  Then go back to step 3.
