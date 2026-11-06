# 05. One-command pipeline

Status: done (Session 5, [`../etl-plan.md`](../etl-plan.md)). CI is planned on its own branch.

## Purpose

Takes an empty database to the built and tested `rpt_` tables with one command, and proves after each stage that nothing was lost on the way. It adds no transformation of its own. It runs the existing stages in order, starts and stops the mock API, and compares record counts between adjacent stages. There is no scheduler ([D-006](../decisions.md#d-006-orchestration-and-the-dbt-vs-plain-sql-write-up-are-deferred)).

The code is three modules in `pipeline/`:

- `pipeline/run.py`: the entry point, the preflight, the stage order, the exit codes and the summary.
- `pipeline/mock_api_server.py`: the mock API as a `uvicorn` subprocess, in a context manager that always stops it ([D-043](../decisions.md#d-043-the-pipeline-starts-the-mock-api-as-a-subprocess-and-always-stops-it)).
- `pipeline/completeness.py`: the runtime completeness checks ([D-044](../decisions.md#d-044-the-completeness-check-is-its-own-module-and-compares-counts-between-adjacent-stages)).

## Inputs and outputs

**Inputs**

- **`raw_data/`.** A precondition, not a stage: the simulator plays the source system, and the pipeline never regenerates it. If it is missing, the run stops with exit 2 and the command that creates it. `AAP_RAW_DATA_DIR` points elsewhere, as it does for the mock API.
- **`AAP_API_BASE_URL`, `AAP_API_TOKEN`.** As for extraction ([01-extraction.md](01-extraction.md#inputs-and-outputs)). The URL must be a local `http://` URL, because this command starts the server there.
- **`POSTGRES_*`.** As for the staging load and dbt. Postgres must be running; the command does not start it.
- **The process environment only**, supplied by `uv run --env-file .env`. There is no new variable.
- **`--landing-dir`, `--dbt-project-dir`** (optional, defaults `landing/` and `dbt/`). The tests use them to leave the real ones alone.

**Outputs**

The same as running the stages by hand: `landing/` replaced, the 8 `staging` tables reloaded, and the `stg` views and `marts` tables rebuilt and tested. A summary on stdout gives the seconds per stage and the counts per layer.

## Schema or contract

**Stage order** (`run_pipeline`):

1. **Preflight.** Variables, a Postgres connection, `raw_data/`, a local API URL, a free API port. Nothing is written before it passes. As soon as the connection opens, it prints the host, port and database the run will modify, taken from that connection, for example `Target database: aap_warehouse on localhost:5433`. It never prints the password ([D-048](../decisions.md#d-048-sigterm-stops-the-run-like-ctrl-c-and-the-run-names-its-target-database)).
2. **Extraction.** Starts the mock API on the host and port of `AAP_API_BASE_URL`, waits until an authenticated `GET /rest/agile/1.0/board?maxResults=1` answers 200 (15 s at most), extracts, and stops the server. Then the landing check.
3. **Staging load.** Then the landing-to-staging check.
4. **dbt build.** `dbt build` as a subprocess, the same command as a manual run. Then the staging-to-marts and marts-to-`rpt_` checks.

**The mock API.** A port that is already in use stops the run with exit 2: it never extracts from a server it did not start, which could be serving different data. On leaving the context manager, for any reason, the server gets SIGTERM and, after 10 s, SIGKILL. It is down during the load and the dbt build.

**Exit codes** ([D-046](../decisions.md#d-046-exit-codes-by-kind-of-failure-and-each-stage-keeps-its-own-guarantee)):

| Code | Meaning |
|---|---|
| 0 | every stage and every completeness check passed |
| 1 | a stage failed: the mock API did not start, or an extraction, staging or `dbt build` error |
| 2 | a precondition failed, and nothing was written |
| 3 | a completeness check failed |
| 130 | interrupted with Ctrl-C, after the same cleanup |
| 143 | terminated with SIGTERM, after the same cleanup as Ctrl-C ([D-048](../decisions.md#d-048-sigterm-stops-the-run-like-ctrl-c-and-the-run-names-its-target-database)) |

**What a failed run leaves behind.** The runner prints this on stderr. The mock API is stopped in every row.

| Failed at | `landing/` | `staging` | `marts` | Exit |
|---|---|---|---|---|
| preflight | untouched | untouched | untouched | 2 |
| mock API start or extraction | previous run's, unchanged (scratch removed) | untouched | untouched | 1 |
| staging load | this run's | previous rows, rolled back | untouched (now behind `landing/`) | 1 |
| `dbt build`, a model fails | this run's | this run's | mixed: the failed model and everything downstream keep their previous tables (absent on an empty database); every other model is rebuilt, including those that run after the failure | 1 |
| `dbt build`, a data test fails | this run's | this run's | mixed: the tested model is **already rebuilt** and holds this run's rows; everything downstream of it is skipped and keeps its previous table | 1 |
| a completeness check | as written by the stages that passed | as written | as written | 3 |

After a completeness failure nothing is rolled back: the data that failed the check is what is needed to investigate it.

Ctrl-C and SIGTERM leave what a failure at that stage leaves: extraction removes its scratch directory, the staging load rolls back, and `dbt build` is killed, which can leave `marts` mixed. The mock API is stopped in every case.

**Completeness checks.** Only counts between stages, never fixed numbers, so another seed passes too. The fixed SEED=22 numbers belong to the tests. A failure names the stage, the table and both counts, for example:

```
Completeness check failed at stage 'landing to staging': staging.status_transitions has 1,487 rows, landing has 1,488 status changelog items
```

| Stage in the message | Compared |
|---|---|
| `extraction` | Every manifest entry has its file and every file has its entry. No `.landing.partial` or `.landing.previous` is left. One changelog folder per landed issue, one `sprint_issues` folder per landed sprint. |
| `landing to staging` | `teams` = distinct board project keys. `users` = distinct account IDs among assignees, reporters and changelog authors. `boards`, `sprints`, `capacity`, `issues` = the landed records. `status_transitions` = status changelog items. `sprint_memberships` = Sprint changelog items, and also the sprint-issue entries. |
| `staging to marts` | The 4 dimensions and 3 facts each equal their staging table. |
| `marts to rpt_ tables` | `rpt_sprint_metrics`, `rpt_q1_velocity`, `rpt_q2_velocity_vs_capacity` = `dim_sprint`. `rpt_q6_team_portfolio` = `dim_team`. `rpt_data_as_of` = 1. `rpt_q3_deadline_outcomes`, and the `rpt_q3_breach_rate` grand total, = issues with a due date. `rpt_q4_status_stays` = transitions + non-Epic issues. `rpt_q5_bug_to_story_ratio` = sprints + teams. `rpt_q4_time_in_status` is not empty. |

Landed records are counted from the raw JSON in `pipeline/completeness.py`, without `ingestion/parse.py`, so the check can't share a bug with the parser. The membership rule holds because v1 has no carryover: an issue never leaves a sprint. A version with carryover changes it.

## How to run

From the repo root, with `raw_data/` generated, `.env` copied from `.env.example` and Postgres up:

```bash
uv run --env-file .env python -m pipeline.run
```

Port 8000 (or the port of `AAP_API_BASE_URL`) must be free, so stop any mock API started by hand. Measured on 2026-11-06 on a warehouse that was already built, before step 5.3: 13.0 s in all (extraction 3.3 s, staging load 0.2 s, dbt build 9.5 s). From a fresh clone: [`../clean-run.md`](../clean-run.md).

## How it is verified

### The pipeline (step 5.1)

`uv run --env-file .env python -m pytest tests/test_pipeline.py -rs`: 30 tests, 0 skipped. They never touch `landing/`, `staging`, `stg` or `marts`. The first test creates a throwaway database, `aap_pipeline_test`, fills it with a real `python -m pipeline.run` and drops it at the end. Any test that changes data works on its own clone (`create database ... template ...`). dbt runs on a copy of `dbt/`.

| Test | What it proves |
|---|---|
| `test_one_run_from_an_empty_database_reaches_every_count` | A real process, from an empty database and no landing, exits 0. All 8 staging, 7 marts and 10 `rpt_` counts equal the shipped targets, 579 files are landed, and the API port is free again. |
| `test_a_second_run_exits_0_with_identical_counts` | A rerun on a built database changes nothing. |
| `test_sabotage_a_page_lost_after_extraction_exits_3` | **Sabotage:** a changelog page deleted after extraction, its manifest entry kept. Exit 3, naming the stage, the file and both counts. |
| `test_sabotage_a_row_lost_from_landing_to_staging_exits_3` | **Sabotage:** one `staging.status_transitions` row deleted. Exit 3: 1,487 rows against 1,488 items. |
| `test_sabotage_a_row_lost_from_staging_to_marts_exits_3` | **Sabotage:** one `marts.fct_sprint_membership` row deleted. Exit 3: 319 rows against 320. |
| `test_sabotage_a_row_lost_from_marts_to_the_rpt_tables_exits_3` | **Sabotage:** one `marts.rpt_q1_velocity` row deleted. Exit 3: 35 rows against 36. |
| `test_an_untouched_clone_passes_every_check_so_the_sabotage_is_what_fails` | The same setup with no deleted row exits 0. |
| `test_a_file_missing_from_the_manifest_fails_the_landing_check`, `test_a_missing_manifest_fails_the_landing_check` | The manifest is compared in both directions. |
| `test_sabotage_a_busy_api_port_exits_2_and_writes_nothing` | **Sabotage:** a listener on the API port. Exit 2, no landing. |
| `test_a_failed_precondition_exits_2_and_writes_nothing` (5 cases) | Missing `raw_data/`, a blank token, a blank Postgres user, a remote API URL, a database that does not exist. |
| `test_a_failed_stage_exits_1_and_says_what_is_stale` (2 cases) | A failed extraction and a failed load exit 1, name what is stale and leave the port free. |
| `test_sabotage_a_failed_model_keeps_its_previous_table` | **Sabotage, on a real failed `dbt build`:** see below. |
| `test_sabotage_a_failed_data_test_leaves_the_model_rebuilt_and_skips_downstream` | **Sabotage, on a real failed `dbt build`:** see below. |
| `test_a_failed_model_on_an_empty_database_leaves_no_table` | With no previous run, the failed model and its downstream model do not exist. Its parents do. |
| `test_the_mock_api_stops_when_the_block_raises`, `test_a_mock_api_that_cannot_start_is_reported`, `test_a_mock_api_that_never_answers_is_stopped` | The server is stopped on an exception and on a failed wait, and an early exit is reported. |
| `test_ctrl_c_exits_130_and_stops_the_mock_api` | SIGINT to a real process while the server is up: exit 130, port free, no landing. |
| `test_sigterm_exits_143_and_stops_the_mock_api` | SIGTERM to the runner alone, in its own process group, while the server is up: exit 143, port free, no process left, no landing or scratch directory. Removing the handler fails it with `-15 == 143`, and skipping the cleanup fails it on the port. |
| `test_sigterm_during_the_dbt_build_exits_143_and_stops_dbt` | SIGTERM while dbt runs: exit 143, and no dbt process is left. |
| `test_a_signal_during_the_staging_load_rolls_back` | The signal arrives with the tables truncated and half refilled: exit 143, and every staging count is unchanged. |
| `test_a_second_sigterm_cannot_interrupt_the_cleanup` | A second SIGTERM during the cleanup is ignored, and `main` restores the previous handler. |
| `test_the_target_line_comes_before_any_stage_and_never_shows_the_password` | In a real run, the first line of output names the host, port and database, exactly once, before the first stage, and the password appears nowhere. Adding the password, or dropping or changing the database name, fails it. |
| `test_the_target_line_names_the_database_of_this_run_before_a_precondition_stops_it` | On another database the line names that database, and it is printed even when a precondition stops the run. |

**SIGTERM through `uv run`.** The tests signal the Python process. Checked by hand on 2026-11-06 with the documented command: a SIGTERM sent to the `uv` process alone reaches the runner, which exits 143, and `uv` exits 143 too. The API port was closed afterwards and nothing was landed.

**What a failed dbt model leaves behind, measured.** dbt-postgres builds a table under a temporary name and swaps it in, so the failure table above was checked rather than assumed. Rebuilding a table gives it a new `pg_class` oid, so comparing oids before and after a failed build shows what was touched:

- With `rpt_q1_velocity` broken by an unknown column: it keeps its oid and its 36 rows. Its downstream model `rpt_q6_team_portfolio` is skipped and keeps its oid. `dim_issue`, `rpt_q3_breach_rate` and `rpt_q5_bug_to_story_ratio` get new oids: models that don't depend on the failure are still rebuilt.
- With a data test failing on `rpt_q1_velocity`: it has a **new** oid, because dbt had already replaced the table when the test ran. `rpt_q6_team_portfolio` keeps its old one.

### The mock API's verification scripts (step 5.2)

`tests/test_mock_api_verify.py`: 4 tests. They need no server and no database. `mock_api.verify` and `mock_api.verify_app` still run on their own.

| Test | What it proves |
|---|---|
| `test_verify_passes_on_the_ground_truth`, `test_verify_app_passes_on_the_ground_truth` | `main` returns 0 on the real `raw_data/` and prints its final `ALL ... PASSED` line. |
| `test_sabotage_a_shifted_due_date_fails_verify` | **Sabotage:** due dates one day late in the serializer `mock_api.verify` uses. `main` returns 1 and prints `FAILED:`. |
| `test_sabotage_a_dropped_field_fails_verify_app` | **Sabotage:** the app drops `customfield_10016`. Its responses no longer equal the serializer, and `main` returns 1. |

`scripts/check_warehouse.py` has no wrapper: every Postgres test already connects through `connect_or_fail`, and `--persistence` restarts the container, which a test must never do.

### The whole suite (step 5.2)

```bash
uv run --env-file .env python -m pytest -rs            # the gate: 140 tests, 0 skipped
uv run --env-file .env python -m pytest -rs --reverse  # the same tests, backwards
uv run python -m pytest -m "not postgres"              # the fast subset: no database, not a gate
```

Tests that only read share one warehouse, built once in its own database behind a read-only connection, and tests that change data keep their own schemas ([D-045](../decisions.md#d-045-tests-that-only-read-share-one-warehouse-built-once)).

Measured on 2026-11-06 on one machine (WSL2, Postgres in Docker). The times depend on the machine.

| Run | Tests | Result | Time |
|---|---|---|---|
| Full suite, before the shared build (after step 5.1) | 127 | 127 passed, 0 skipped | 431.75 s (7 min 12 s) |
| Full suite, after it | 132 | 132 passed, 0 skipped | 338.97 s (5 min 39 s) |
| Full suite, reverse order | 132 | 132 passed, 0 skipped | 330.36 s (5 min 30 s) |
| Fast subset, no `POSTGRES_*` set | 56 | 56 passed, 76 deselected | 24.5 s |
| Full suite, after step 5.3 | 134 | 134 passed, 0 skipped | 352.49 s (5 min 52 s) |
| Full suite, after the SIGTERM fix ([D-048](../decisions.md#d-048-sigterm-stops-the-run-like-ctrl-c-and-the-run-names-its-target-database)) | 140 | 140 passed, 0 skipped | 319.74 s (5 min 20 s) |
| Full suite after the SIGTERM fix, reverse order | 140 | 140 passed, 0 skipped | 320.98 s (5 min 21 s) |
| Fast subset after the SIGTERM fix, no `POSTGRES_*` set | 57 | 57 passed, 83 deselected | 24.1 s |

### Hooks

`pipeline/` joined the pre-commit docs check: `sh scripts/test_hooks.sh` gives 30 passed. Without the hooks, 19 cases fail.

## Clean-run acceptance

Run on 2026-11-06, on commit `e09d20f`, by following [`../clean-run.md`](../clean-run.md): a fresh clone with no `raw_data/` or `landing/`, and a separate Compose project (`aap-cleanrun`) on port 5434, whose database started with 0 tables and only the `public` schema. The real warehouse was not touched by this run: the oid of every `staging`, `stg` and `marts` object, and `_loaded_at`, were the same before and after.

| Check | Result |
|---|---|
| `uv run --env-file .env python -m pipeline.run` | exit 0 |
| `dbt build`, inside it | `PASS=254 WARN=0 ERROR=0 SKIP=0` |
| Counts against the documented numbers (raw data, landed files, 8 staging tables, 7 marts tables, 10 `rpt_` tables, due dates, `is_rollup` splits, sprint hits, breach rates) | 46 of 46 match |
| **Sabotage:** one expected number changed, 1,488 transitions to 1,489 | 45 of 46, and only that table reported |
| Full suite in the clone, against that database | 134 passed, 0 skipped |
| `sh scripts/test_hooks.sh` | 30 passed, 0 failed |
| `mock_api.verify`, `mock_api.verify_app` | `ALL CHECKS PASSED`, `ALL APP CHECKS PASSED` |
| All 25 tables (8 `staging`, 17 `marts`), hashed without `_loaded_at`, against the existing warehouse | 25 identical |

| Step | Time |
|---|---|
| `uv sync` (uv cache warm) | 0.2 s |
| `generate_raw_data.py` | 0.8 s |
| `docker compose ... up -d --wait` (image present) | 6.5 s |
| `pipeline.run` | 16.9 s: extraction 2.4 s, staging load 0.2 s, dbt build 13.0 s, plus start-up |
| `pytest -rs` | 357.23 s (5 min 57 s) |

The comparison script was kept in the scratch clone and not committed: `tests/test_pipeline.py` checks the same counts on every suite run.

A first attempt on the same day is not part of this record. `POSTGRES_*` variables exported in the shell took priority over the clone's `.env`, so Compose failed on port 5433 and the pipeline ran against the existing warehouse. That is why [`../clean-run.md`](../clean-run.md) starts by checking the shell.

## Known limits

- **CI is not in place yet.** Nothing runs the tests on push. GitHub Actions is planned on its own branch ([step 5.4](../etl-plan.md#step-54-github-actions-ci-planned-separate-branch)).
- **SIGKILL can't be handled.** No process can catch it, so the `uvicorn` child would keep running and hold the port. The next run then stops with exit 2.
- **Exported variables win over `.env`.** `uv run --env-file` and Compose both give the shell priority, so a stale `POSTGRES_PORT` selects another database ([`../clean-run.md`](../clean-run.md#prerequisites)). The target line shows which one, but the run does not stop to ask ([D-048](../decisions.md#d-048-sigterm-stops-the-run-like-ctrl-c-and-the-run-names-its-target-database)).
- **A failed `dbt build` can leave `marts` mixed,** as the failure table says. Refresh the dashboard only after exit 0.
- **Completeness checks count, they don't compare values.** A row with a wrong value and the right count passes. Proving the values is the job of the tests.
- **`rpt_q3_breach_rate` and `rpt_q4_time_in_status` have no exact count check,** because their row counts depend on which combinations have data. Q3's grand total is checked, and Q4 only for being non-empty.
- **The membership rule assumes no carryover** ([D-044](../decisions.md#d-044-the-completeness-check-is-its-own-module-and-compares-counts-between-adjacent-stages)).
- **It doesn't start Postgres or generate `raw_data/`.** Both are preconditions, checked before anything is written.
- **Local mock API only.** A remote `AAP_API_BASE_URL` is refused, because the command starts the server itself.
- **Full refresh, in sequence.** Every run extracts, reloads and rebuilds everything.

## Options rejected and why

- **An orchestrator (Airflow or similar).** A scheduler adds infrastructure without making v1 more correct ([D-006](../decisions.md#d-006-orchestration-and-the-dbt-vs-plain-sql-write-up-are-deferred)).
- **The mock API in process, in a thread, or started by hand.** See [D-043](../decisions.md#d-043-the-pipeline-starts-the-mock-api-as-a-subprocess-and-always-stops-it).
- **Generating `raw_data/` in the run.** It would hide a missing or changed source ([D-043](../decisions.md#d-043-the-pipeline-starts-the-mock-api-as-a-subprocess-and-always-stops-it)).
- **Completeness in dbt tests, inside each stage, or in pytest.** See [D-044](../decisions.md#d-044-the-completeness-check-is-its-own-module-and-compares-counts-between-adjacent-stages).
- **One exit code for everything, or one per stage.** See [D-046](../decisions.md#d-046-exit-codes-by-kind-of-failure-and-each-stage-keeps-its-own-guarantee).
- **Sharing exit 130 for SIGTERM, or a confirmation prompt before writing.** See [D-048](../decisions.md#d-048-sigterm-stops-the-run-like-ctrl-c-and-the-run-names-its-target-database).
- **Rolling back every stage, or a shadow schema swap for `marts`.** See [D-046](../decisions.md#d-046-exit-codes-by-kind-of-failure-and-each-stage-keeps-its-own-guarantee). The swap is a v1.1 candidate.
