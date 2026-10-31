"""Tests for the extraction client, against the real raw_data/ through the in-process app.

No server is started: FastAPI's TestClient is an httpx.Client, so it is
injected into JiraClient in place of a network connection.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from ingestion import extract
from ingestion.client import ExtractionError, JiraClient
from ingestion.extract import MANIFEST, PageSizes, run_extraction
from mock_api.app import create_app
from mock_api.serializers import serialize_issue

RAW_DATA = Path(__file__).resolve().parent.parent / "raw_data"

EXPECTED = {
    "boards": 3,
    "sprints": 36,
    "issues": 530,
    "capacity_rows": 36,
    "changelog_status_entries": 1488,
    "changelog_sprint_entries": 320,
    "sprint_issue_entries": 320,
}
"""Shipped dataset (SEED=22) targets, from CLAUDE.md."""

SMALL_PAGES = PageSizes(boards=1, sprints=5, search=7, changelog=3, sprint_issues=4, capacity=5)
"""Page sizes small enough that every endpoint needs several pages."""


# --- Fixtures and helpers -------------------------------------------------


@pytest.fixture(scope="session")
def app() -> TestClient:
    if not RAW_DATA.is_dir():
        pytest.fail(f"{RAW_DATA} is missing: run `python scripts/generate_raw_data.py` first")
    with TestClient(create_app(RAW_DATA)) as client:
        yield client


def make_client(app: TestClient) -> JiraClient:
    return JiraClient(app, token="test-token", backoff_seconds=0)


@pytest.fixture(scope="session")
def landing(app: TestClient, tmp_path_factory) -> Path:
    target = tmp_path_factory.mktemp("default") / "landing"
    run_extraction(make_client(app), target)
    return target


def pages(landing: Path, folder: str) -> list[dict]:
    return [json.loads(p.read_bytes()) for p in sorted((landing / folder).rglob("page-*.json"))]


def landed_counts(landing: Path) -> dict[str, int]:
    """Count records in the landing files (test-side reading only; the client never parses)."""
    items = Counter()
    for page in pages(landing, "changelogs"):
        for history in page["values"]:
            for item in history["items"]:
                items[item["field"]] += 1
    return {
        "boards": sum(len(p["values"]) for p in pages(landing, "boards")),
        "sprints": sum(len(p["values"]) for p in pages(landing, "board_sprints")),
        "issues": sum(len(p["issues"]) for p in pages(landing, "search")),
        "capacity_rows": sum(len(p["values"]) for p in pages(landing, "capacity")),
        "changelog_status_entries": items["status"],
        "changelog_sprint_entries": items["Sprint"],
        "sprint_issue_entries": sum(len(p["issues"]) for p in pages(landing, "sprint_issues")),
    }


def assert_counts(landing: Path) -> None:
    assert landed_counts(landing) == EXPECTED


def snapshot(directory: Path) -> dict[str, bytes]:
    return {str(p.relative_to(directory)): p.read_bytes() for p in sorted(directory.rglob("*")) if p.is_file()}


# --- Counts and content -----------------------------------------------------


def test_landed_counts_match_the_shipped_dataset(landing: Path) -> None:
    assert_counts(landing)


def test_small_pages_land_the_same_records(app: TestClient, landing: Path, tmp_path: Path) -> None:
    small = tmp_path / "landing"
    summary = run_extraction(make_client(app), small, SMALL_PAGES)
    assert_counts(small)
    assert summary["capacity"] == 8 and summary["boards"] == 3  # 36 rows / 5; 3 boards / 1
    default_issues = [i for p in pages(landing, "search") for i in p["issues"]]
    small_issues = [i for p in pages(small, "search") for i in p["issues"]]
    assert small_issues == default_issues


def test_every_landed_issue_equals_the_serializer(app: TestClient, landing: Path) -> None:
    index = app.app.state.catalog.index
    landed = [issue for page in pages(landing, "search") for issue in page["issues"]]
    assert len({issue["key"] for issue in landed}) == 530
    for issue in landed:
        expected = serialize_issue(index.issues_by_key[issue["key"]], index)
        assert issue == expected, issue["key"]
        assert issue["fields"]["duedate"] == expected["fields"]["duedate"]
    assert sum(1 for issue in landed if issue["fields"]["duedate"] is not None) == 320


def test_landing_files_are_the_raw_responses(app: TestClient, landing: Path) -> None:
    manifest = json.loads((landing / MANIFEST).read_text())
    assert len(manifest["files"]) == len(list(landing.rglob("page-*.json")))
    for entry in manifest["files"]:
        response = app.get(entry["path"], params=entry["params"], headers={"Authorization": "Bearer t"})
        assert (landing / entry["file"]).read_bytes() == response.content, entry["file"]


def test_second_run_is_byte_identical(app: TestClient, landing: Path) -> None:
    before = snapshot(landing)
    run_extraction(make_client(app), landing)
    assert snapshot(landing) == before
    leftovers = [p.name for p in landing.parent.iterdir() if p.name.startswith(".landing")]
    assert leftovers == []


# --- Sabotage ---------------------------------------------------------------


def test_sabotage_stopping_one_page_early_fails_the_count_check(
    app: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A client that drops the last page of every multi-page stream must fail the counts."""

    def drop_last_page(paginate):
        def wrapper(self, *args, **kwargs):
            fetched = list(paginate(self, *args, **kwargs))
            yield from fetched[:-1] if len(fetched) > 1 else fetched

        return wrapper

    monkeypatch.setattr(JiraClient, "offset_pages", drop_last_page(JiraClient.offset_pages))
    monkeypatch.setattr(JiraClient, "token_pages", drop_last_page(JiraClient.token_pages))
    sabotaged = tmp_path / "landing"
    run_extraction(make_client(app), sabotaged, SMALL_PAGES)
    with pytest.raises(AssertionError):
        assert_counts(sabotaged)
    counts = landed_counts(sabotaged)
    assert counts["capacity_rows"] == 35 and counts["issues"] < 530


# --- Auth and failures ------------------------------------------------------


def test_missing_environment_fails_loudly(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.delenv(extract.ENV_BASE_URL, raising=False)
    monkeypatch.delenv(extract.ENV_TOKEN, raising=False)
    with pytest.raises(ExtractionError, match="AAP_API_BASE_URL and AAP_API_TOKEN"):
        extract.client_from_environment()
    assert extract.main([]) == 1
    assert "Missing AAP_API_BASE_URL and AAP_API_TOKEN" in capsys.readouterr().err

    monkeypatch.setenv(extract.ENV_BASE_URL, "http://localhost:8000")
    with pytest.raises(ExtractionError, match="Missing AAP_API_TOKEN"):
        extract.client_from_environment()


def test_env_example_lists_the_extraction_settings() -> None:
    # A .env copied from .env.example must be enough for `uv run --env-file .env`.
    example = (extract.REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    keys = {line.split("=", 1)[0] for line in example.splitlines() if "=" in line and not line.startswith("#")}
    assert {extract.ENV_BASE_URL, extract.ENV_TOKEN} <= keys


@pytest.mark.parametrize("url", ["localhost:8000", "http//localhost:8000", "ftp://localhost:8000", "http://"])
def test_malformed_base_url_fails_before_any_request(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    monkeypatch.setenv(extract.ENV_BASE_URL, url)
    monkeypatch.setenv(extract.ENV_TOKEN, "any")
    with pytest.raises(ExtractionError, match=r"AAP_API_BASE_URL .*http://localhost:8000"):
        extract.client_from_environment()


def test_empty_token_is_refused_before_any_request(app: TestClient) -> None:
    with pytest.raises(ExtractionError, match="token is empty"):
        JiraClient(app, token="   ")


def test_rejected_token_fails_loudly_and_keeps_the_previous_landing(app: TestClient, tmp_path: Path) -> None:
    previous = tmp_path / "landing"
    previous.mkdir()
    (previous / "marker.txt").write_text("previous good run")

    client = make_client(app)
    client.headers["Authorization"] = "Token not-accepted"  # the real app answers 401
    with pytest.raises(ExtractionError, match="401 Unauthorized.*AAP_API_TOKEN"):
        run_extraction(client, previous)
    assert snapshot(previous) == {"marker.txt": b"previous good run"}
    assert not (tmp_path / ".landing.partial").exists()


# --- Retries ----------------------------------------------------------------


def scripted_client(responses: list) -> tuple[JiraClient, list]:
    """A client whose transport plays back `responses` (status codes or exceptions) in order."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        outcome = responses[min(len(calls), len(responses)) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        body = {"values": [], "isLast": True} if outcome == 200 else {"errorMessages": ["x"], "errors": {}}
        return httpx.Response(outcome, json=body)

    http = httpx.Client(base_url="http://mock", transport=httpx.MockTransport(handler))
    return JiraClient(http, token="t", backoff_seconds=0), calls


def test_transient_errors_are_retried() -> None:
    client, calls = scripted_client([503, 200])
    assert client.get("/x").data["isLast"] is True and len(calls) == 2

    client, calls = scripted_client([httpx.ConnectError("refused"), 200])
    assert client.get("/x").data["isLast"] is True and len(calls) == 2

    client, calls = scripted_client([500, 500, 500, 200])
    with pytest.raises(ExtractionError, match="failed after 3 attempts"):
        client.get("/x")
    assert len(calls) == 3


@pytest.mark.parametrize("status", [401, 404, 400])
def test_client_errors_are_never_retried(status: int) -> None:
    client, calls = scripted_client([status, 200])
    with pytest.raises(ExtractionError, match=f"{status}"):
        client.get("/x")
    assert len(calls) == 1
