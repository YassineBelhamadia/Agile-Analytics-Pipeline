"""Fixtures shared across test files, and the --reverse option.

`landing` and `built_warehouse` live here so that each exists once per session:
a session fixture imported into several test modules would run once per module.

`built_warehouse` is for tests that only read: it builds the warehouse once
(D-045). It sits in its own database, so tests that change data can't reach it,
and its connection is read-only, so a test on it can't change it either. Tests
that change data keep the function-scoped `warehouse` fixture in test_dbt.py.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ingestion import load_staging as loader
from ingestion.client import JiraClient
from ingestion.extract import run_extraction
from mock_api.app import create_app
from test_dbt import failed_tests, run_dbt
from test_staging import RAW_DATA, TEST_SCHEMA, connect_or_fail, connect_to, drop_database, recreate_database

SHARED_DATABASE = "aap_test_shared"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--reverse", action="store_true", help="run the tests in reverse order, to prove none depends on another")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--reverse"):
        items.reverse()


@pytest.fixture(scope="session")
def landing(tmp_path_factory) -> Path:
    if not RAW_DATA.is_dir():
        pytest.fail(f"{RAW_DATA} is missing: run `uv run python scripts/generate_raw_data.py` first")
    target = tmp_path_factory.mktemp("staging") / "landing"
    with TestClient(create_app(RAW_DATA)) as app:
        run_extraction(JiraClient(app, token="test-token", backoff_seconds=0), target)
    return target


@pytest.fixture(scope="session")
def built_warehouse(landing: Path, tmp_path_factory):
    """A read-only connection to a warehouse built once, with every dbt model and test passing."""
    connect_or_fail().close()
    recreate_database(SHARED_DATABASE)
    try:
        with connect_to(SHARED_DATABASE) as connection:
            loader.load_staging(connection, landing, schema=TEST_SCHEMA)
        work = tmp_path_factory.mktemp("built_warehouse")
        result = run_dbt(work, "build", env=os.environ | {"POSTGRES_DB": SHARED_DATABASE})
        assert result.returncode == 0, result.stdout
        assert failed_tests(work) == set()
        with connect_to(SHARED_DATABASE, options="-c default_transaction_read_only=on") as connection:
            yield connection
    finally:
        drop_database(SHARED_DATABASE)
