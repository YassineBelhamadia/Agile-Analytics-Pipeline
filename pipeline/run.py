"""Run the whole pipeline with one command, from an empty database to the rpt_ tables.

Run from the repo root, with Postgres up and raw_data/ generated:

    uv run --env-file .env python -m pipeline.run [--landing-dir DIR] [--dbt-project-dir DIR]

Inputs: raw_data/ (served by the mock API this command starts on the host and
port of AAP_API_BASE_URL), AAP_API_TOKEN and the POSTGRES_* variables, read
from the process environment only.
Outputs: landing/, the staging tables, and the stg, marts and rpt_ models.
Documented in docs/etl/05-pipeline.md.

Stages, in order, with a completeness check after each one (D-044):
preflight, mock API up, extraction, mock API down, staging load, dbt build.
Preflight prints the host, port and database the run will modify, before
anything is written (D-048).

Exit codes (D-046):
    0    every stage and every completeness check passed
    1    a stage failed
    2    a precondition failed, and nothing was written
    3    a completeness check failed; nothing is rolled back, so the data that
         failed the check can be inspected
    130  interrupted with Ctrl-C
    143  terminated with SIGTERM, after the same cleanup as Ctrl-C (D-048)

On a failure it prints what is now stale. Each stage keeps its own guarantee:
a failed extraction keeps the previous landing, and a failed staging load
rolls back. A failed dbt build can leave marts mixed.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import psycopg

from ingestion.client import ExtractionError, JiraClient
from ingestion.extract import DEFAULT_LANDING_DIR, ENV_BASE_URL, ENV_TOKEN, client_from_environment, run_extraction
from ingestion.load_staging import StagingError, connect_from_environment, load_staging
from mock_api.app import DEFAULT_RAW_DATA_DIR, RAW_DATA_ENV
from pipeline import completeness
from pipeline.completeness import CompletenessError
from pipeline.mock_api_server import mock_api_server, port_in_use
from simulator.persistence import ENTITY_SPECS

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DBT_PROJECT_DIR = REPO_ROOT / "dbt"
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")

EXIT_OK = 0
EXIT_STAGE_FAILED = 1
EXIT_PRECONDITION = 2
EXIT_INCOMPLETE = 3
EXIT_INTERRUPTED = 130
EXIT_TERMINATED = 143

EXTRACTION, STAGING_LOAD, DBT_BUILD = "extraction", "staging load", "dbt build"

STALE_AFTER = {
    EXTRACTION: "landing/ still holds the previous run. staging and marts are untouched.",
    STAGING_LOAD: "landing/ is this run's. staging rolled back to its previous rows and marts is untouched, so both are behind landing/.",
    DBT_BUILD: (
        "landing/ and staging are this run's. marts may be mixed: models that built are this run's, "
        "and a failed or skipped model keeps its previous table (or is absent on an empty database). "
        "A model whose data test failed was already rebuilt, and its downstream models were skipped."
    ),
}
"""What a failed stage leaves behind (the failure table in docs/etl/05-pipeline.md)."""


class PreconditionError(RuntimeError):
    """The run cannot start. Nothing has been written."""


class StageError(RuntimeError):
    """A stage failed. `stage` keys STALE_AFTER."""

    def __init__(self, stage: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage


class Terminated(KeyboardInterrupt):
    """SIGTERM, raised in the main thread.

    A KeyboardInterrupt, so it takes the path Ctrl-C takes: past `except
    Exception` in `stage`, through every cleanup, and psycopg cancels a running
    query on it. `main` catches it first, for the exit code.
    """


def raise_terminated(signum: int, frame: object) -> None:
    """The SIGTERM handler. A second SIGTERM is ignored, so it can't cut the cleanup short."""
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    raise Terminated


@dataclass
class Report:
    seconds: dict[str, float] = field(default_factory=dict)
    counts: dict[str, dict[str, int]] = field(default_factory=dict)


@dataclass
class Settings:
    client: JiraClient
    host: str
    port: int
    token: str


@contextmanager
def stage(name: str, report: Report) -> Iterator[None]:
    """Time a stage, and turn any failure inside it into a StageError."""
    print(f"--- {name}", flush=True)
    started = time.monotonic()
    try:
        yield
    except CompletenessError:
        raise
    except Exception as exc:
        raise StageError(name, f"{type(exc).__name__}: {exc}") from exc
    report.seconds[name] = time.monotonic() - started


def preflight() -> Settings:
    """Check everything the run needs before anything is written.

    Raises:
        PreconditionError: Naming what is missing and how to fix it.
    """
    try:
        client = client_from_environment()
        connection = connect_from_environment()
    except (ExtractionError, StagingError) as exc:
        raise PreconditionError(str(exc)) from exc
    except psycopg.OperationalError as exc:
        where = f"{os.environ['POSTGRES_HOST']}:{os.environ['POSTGRES_PORT']}"
        raise PreconditionError(
            f"Postgres is unreachable at {where}. Start it with "
            f"`docker compose --env-file .env -f warehouse/docker-compose.yml up -d --wait`. ({exc})"
        ) from exc
    info = connection.info  # the connection actually opened, not what the variables were meant to say
    print(f"Target database: {info.dbname} on {info.host}:{info.port}", flush=True)
    connection.close()

    raw_data_dir = Path(os.environ.get(RAW_DATA_ENV) or DEFAULT_RAW_DATA_DIR)
    missing = [spec.filename for spec in ENTITY_SPECS if not (raw_data_dir / spec.filename).is_file()]
    if missing:
        raise PreconditionError(
            f"{raw_data_dir} is missing {', '.join(missing)}. "
            "Generate it with `uv run python scripts/generate_raw_data.py`."
        )

    url = urlsplit(os.environ[ENV_BASE_URL].strip())
    if url.scheme != "http" or url.hostname not in LOCAL_HOSTS:
        raise PreconditionError(
            f"{ENV_BASE_URL} must be a local http:// URL, because this command starts the mock API itself."
        )
    port = url.port or 80
    if port_in_use(url.hostname, port):
        raise PreconditionError(
            f"Port {port} is already in use. This command starts its own mock API and never extracts "
            "from a server it did not start. Stop the other server, or change AAP_API_BASE_URL."
        )
    return Settings(client, url.hostname, port, os.environ[ENV_TOKEN])


def build_dbt(project_dir: Path) -> None:
    """`dbt build` as a subprocess, the same command as a manual run.

    Raises:
        RuntimeError: If dbt exits non-zero. Its own output says which node failed.
    """
    dbt = Path(sys.executable).with_name("dbt")
    result = subprocess.run([str(dbt), "build", "--project-dir", str(project_dir), "--profiles-dir", str(project_dir)])
    if result.returncode != 0:
        raise RuntimeError(f"dbt build exited with code {result.returncode}.")


def run_pipeline(landing_dir: Path, dbt_project_dir: Path) -> Report:
    """Run every stage in order. Raises on the first failure."""
    report = Report()
    settings = preflight()

    with stage(EXTRACTION, report):
        with mock_api_server(settings.host, settings.port, settings.token):
            run_extraction(settings.client, landing_dir)
        report.counts["landing"] = landed = completeness.check_landing(landing_dir)

    with stage(STAGING_LOAD, report), connect_from_environment() as conn:
        load_staging(conn, landing_dir)
        report.counts["staging"] = completeness.check_staging(conn, landed)

    with stage(DBT_BUILD, report):
        build_dbt(dbt_project_dir)
        with connect_from_environment() as conn:
            report.counts["marts"] = completeness.check_marts(conn)
            report.counts["rpt_ tables"] = completeness.check_reports(conn)
    return report


def print_summary(report: Report) -> None:
    print(f"Pipeline finished in {sum(report.seconds.values()):.1f} s")
    for name, seconds in report.seconds.items():
        print(f"  {name:<28} {seconds:>7.1f} s")
    for layer, counts in report.counts.items():
        print(f"{layer}:")
        for name, count in counts.items():
            print(f"  {name:<28} {count:>7,}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipeline.run", description=__doc__.splitlines()[0])
    parser.add_argument("--landing-dir", default=DEFAULT_LANDING_DIR, type=Path)
    parser.add_argument("--dbt-project-dir", default=DEFAULT_DBT_PROJECT_DIR, type=Path)
    args = parser.parse_args(argv)
    previous_handler = signal.signal(signal.SIGTERM, raise_terminated)
    try:
        print_summary(run_pipeline(args.landing_dir, args.dbt_project_dir))
    except PreconditionError as exc:
        print(f"Pipeline not started: {exc}", file=sys.stderr)
        return EXIT_PRECONDITION
    except StageError as exc:
        print(f"Pipeline failed at {exc.stage}: {exc}\n{STALE_AFTER[exc.stage]}", file=sys.stderr)
        return EXIT_STAGE_FAILED
    except CompletenessError as exc:
        print(f"{exc}\nNothing was rolled back, so the data that failed the check can be inspected.", file=sys.stderr)
        return EXIT_INCOMPLETE
    except Terminated:
        print("Pipeline terminated by SIGTERM. The mock API was stopped.", file=sys.stderr)
        return EXIT_TERMINATED
    except KeyboardInterrupt:
        print("Pipeline interrupted. The mock API was stopped.", file=sys.stderr)
        return EXIT_INTERRUPTED
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
