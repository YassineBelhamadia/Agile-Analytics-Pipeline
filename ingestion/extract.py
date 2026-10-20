"""Extract everything from the mock API and land the raw responses.

Run from the repo root with the mock API serving:

    python -m ingestion.extract [landing_dir]

Discovery goes through the API only, the way a client of a real Jira site
would have to:

1. boards                       -> board ids and project keys
2. each board's sprints         -> sprint ids
3. a JQL search per project     -> issues, with all fields (the API
   rejects unbounded JQL, so there is one search per project)
4. each issue's changelog       -> from the dedicated changelog endpoint,
   paged to the end (not expand=changelog, which is capped at 100)
5. each sprint's issue list
6. capacity                     -> our own endpoint, not Jira's

Nothing is parsed or transformed. Each response page is written exactly as
the server sent it, one file per page, plus a manifest recording which
request produced each file. JSON is decoded only to follow pagination and
to discover the next requests.

Runs are idempotent. Everything is written to a scratch directory first and
then swapped into place, so a finished run fully replaces the previous
landing (no stale or duplicate pages), and a failed run leaves the
previous landing untouched.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from ingestion.client import ExtractionError, JiraClient, Page

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LANDING_DIR = REPO_ROOT / "landing"
ENV_BASE_URL = "AAP_API_BASE_URL"
ENV_TOKEN = "AAP_API_TOKEN"
MANIFEST = "manifest.json"


@dataclass(frozen=True)
class PageSizes:
    """maxResults requested per endpoint. Smaller values only add pages."""

    boards: int = 50
    sprints: int = 50
    search: int = 100
    changelog: int = 100
    sprint_issues: int = 50
    capacity: int = 50


class _Landing:
    """Writes raw pages under a directory and remembers what produced each one."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.entries: list[dict] = []

    def write(self, relative: str, page: Page) -> None:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(page.body)
        self.entries.append({"file": relative, "path": page.path, "params": page.params})

    def write_manifest(self) -> None:
        body = json.dumps({"files": self.entries}, indent=2, ensure_ascii=False) + "\n"
        (self.root / MANIFEST).write_text(body, encoding="utf-8")


def _pages(landing: _Landing, folder: str, pages) -> list[dict]:
    """Land every page from a paginator under `folder`; return the decoded pages."""
    decoded = []
    for number, page in enumerate(pages):
        landing.write(f"{folder}/page-{number:04d}.json", page)
        decoded.append(page.data)
    return decoded


def run_extraction(client: JiraClient, landing_dir: Path | str, sizes: PageSizes = PageSizes()) -> dict:
    """Extract every endpoint into `landing_dir`, replacing what was there.

    Args:
        client: The API client (inject one wrapping the in-process app to test).
        landing_dir: Where the raw responses go.
        sizes: maxResults per endpoint.

    Returns:
        How many pages were landed per endpoint folder.

    Raises:
        ExtractionError: On any request that fails. The previous landing,
            if any, is left as it was.
    """
    landing_dir = Path(landing_dir)
    scratch = landing_dir.with_name(f".{landing_dir.name}.partial")
    if scratch.exists():
        shutil.rmtree(scratch)
    scratch.mkdir(parents=True)
    landing = _Landing(scratch)

    try:
        boards = [
            board
            for page in _pages(landing, "boards", client.offset_pages("/rest/agile/1.0/board", sizes.boards))
            for board in page["values"]
        ]

        sprint_ids: list[int] = []
        for board in boards:
            pages = client.offset_pages(f"/rest/agile/1.0/board/{board['id']}/sprint", sizes.sprints)
            for page in _pages(landing, f"board_sprints/{board['id']}", pages):
                sprint_ids.extend(sprint["id"] for sprint in page["values"])

        project_keys = list(dict.fromkeys(board["location"]["projectKey"] for board in boards))
        issue_keys: list[str] = []
        for key in project_keys:
            pages = client.token_pages(
                "/rest/api/3/search/jql",
                sizes.search,
                {"jql": f"project = {key} ORDER BY created ASC", "fields": "*all"},
            )
            for page in _pages(landing, f"search/{key}", pages):
                issue_keys.extend(issue["key"] for issue in page["issues"])

        for key in issue_keys:
            _pages(landing, f"changelogs/{key}", client.offset_pages(f"/rest/api/3/issue/{key}/changelog", sizes.changelog))

        for sprint_id in sprint_ids:
            pages = client.offset_pages(f"/rest/agile/1.0/sprint/{sprint_id}/issue", sizes.sprint_issues, items="issues")
            _pages(landing, f"sprint_issues/{sprint_id}", pages)

        _pages(landing, "capacity", client.offset_pages("/capacity", sizes.capacity))
        landing.write_manifest()
    except BaseException:
        shutil.rmtree(scratch, ignore_errors=True)
        raise

    # Swap the finished run into place: the old landing is only removed once
    # the new one is complete.
    previous = landing_dir.with_name(f".{landing_dir.name}.previous")
    if previous.exists():
        shutil.rmtree(previous)
    if landing_dir.exists():
        landing_dir.rename(previous)
    scratch.rename(landing_dir)
    if previous.exists():
        shutil.rmtree(previous)

    summary: dict[str, int] = {}
    for entry in landing.entries:
        folder = entry["file"].split("/")[0]
        summary[folder] = summary.get(folder, 0) + 1
    return summary


def client_from_environment() -> JiraClient:
    """Build a client from AAP_API_BASE_URL and AAP_API_TOKEN.

    Raises:
        ExtractionError: Naming whichever variable is missing.
    """
    missing = [name for name in (ENV_BASE_URL, ENV_TOKEN) if not os.environ.get(name, "").strip()]
    if missing:
        raise ExtractionError(
            f"Missing {' and '.join(missing)}. Set the mock API's base URL and a token, "
            f"e.g. {ENV_BASE_URL}=http://localhost:8000 {ENV_TOKEN}=any-non-empty-value."
        )
    return JiraClient.for_base_url(os.environ[ENV_BASE_URL].strip(), os.environ[ENV_TOKEN])


def main(argv: list[str]) -> int:
    landing_dir = Path(argv[0]) if argv else DEFAULT_LANDING_DIR
    try:
        summary = run_extraction(client_from_environment(), landing_dir)
    except ExtractionError as exc:
        print(f"Extraction failed: {exc}", file=sys.stderr)
        return 1
    print(f"Landed raw responses in {landing_dir}")
    for folder, pages in summary.items():
        print(f"  {folder:<15} {pages:>5} pages")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
