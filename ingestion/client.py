"""HTTP client for the Jira mock API (and, unchanged, for a real Jira site).

Knows how to make one authenticated GET and how to follow the two
pagination styles the API uses. It knows nothing about what the responses
mean: every page is returned as the raw bytes the server sent, so the
caller can land them untouched.

The underlying `httpx.Client` is injectable. Production code builds one
for a base URL; the tests pass FastAPI's TestClient (itself an
httpx.Client) so the whole extraction runs against the app in-process,
with no server started.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx

DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_ATTEMPTS = 3
"""Total tries per request: one, plus two retries on transient failures."""

DEFAULT_BACKOFF_SECONDS = 0.5
"""Wait before the first retry; doubled before each later one."""


class ExtractionError(RuntimeError):
    """A request failed in a way retrying cannot fix. Stops the extraction."""


@dataclass(frozen=True)
class Page:
    """One raw response page.

    Attributes:
        path: Request path, relative to the base URL.
        params: Query parameters sent.
        body: The response body exactly as received.
        data: The body decoded as JSON, used only to drive pagination.
    """

    path: str
    params: dict[str, Any]
    body: bytes
    data: dict


class JiraClient:
    """Authenticated GETs with timeouts, bounded retries, and pagination.

    Args:
        http: The httpx client to send requests through. Its base URL must
            already point at the API.
        token: Sent as `Authorization: Bearer <token>`. Must be non-empty.
        attempts: Total tries per request on transient failures.
        backoff_seconds: Initial wait between retries, doubled each time.
    """

    def __init__(
        self,
        http: httpx.Client,
        token: str,
        attempts: int = DEFAULT_ATTEMPTS,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    ) -> None:
        if not token or not token.strip():
            raise ExtractionError("The API token is empty. Set AAP_API_TOKEN to a non-empty value.")
        self.http = http
        self.headers = {"Authorization": f"Bearer {token.strip()}", "Accept": "application/json"}
        self.attempts = attempts
        self.backoff_seconds = backoff_seconds

    @classmethod
    def for_base_url(
        cls, base_url: str, token: str, timeout: float = DEFAULT_TIMEOUT_SECONDS
    ) -> JiraClient:
        """Build a client with its own httpx connection pool for `base_url`."""
        return cls(httpx.Client(base_url=base_url, timeout=timeout), token)

    def get(self, path: str, params: dict[str, Any] | None = None) -> Page:
        """GET one resource, retrying only transient failures.

        Retried: 5xx responses and connection-level errors (refused,
        reset, timed out). Not retried: any 4xx. A 401 or 404 means the
        request itself is wrong, and repeating it would only hide that.

        Raises:
            ExtractionError: On a non-retryable status, or when every
                attempt failed.
        """
        params = dict(params or {})
        delay = self.backoff_seconds
        last_problem = ""
        for attempt in range(1, self.attempts + 1):
            try:
                response = self.http.get(path, params=params, headers=self.headers)
            except httpx.TransportError as exc:
                last_problem = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code == 200:
                    return Page(path, params, response.content, response.json())
                if response.status_code < 500:
                    raise ExtractionError(self._describe(response, path, params))
                last_problem = f"HTTP {response.status_code}"
            if attempt < self.attempts:
                time.sleep(delay)
                delay *= 2
        raise ExtractionError(f"GET {path} {params} failed after {self.attempts} attempts: {last_problem}")

    @staticmethod
    def _describe(response: httpx.Response, path: str, params: dict) -> str:
        try:
            body = response.json()
            detail = "; ".join(body.get("errorMessages", [])) or json.dumps(body.get("errors", {}))
        except ValueError:
            detail = response.text[:200]
        if response.status_code == 401:
            return (
                f"GET {path} was rejected with 401 Unauthorized ({detail}). "
                "Check AAP_API_TOKEN: it must be accepted by the API."
            )
        return f"GET {path} {params} failed with HTTP {response.status_code}: {detail}"

    def offset_pages(
        self,
        path: str,
        page_size: int,
        params: dict[str, Any] | None = None,
        items: str = "values",
    ) -> Iterator[Page]:
        """Follow startAt/maxResults paging to the last page.

        Used by the changelog, agile and capacity endpoints. The next
        startAt advances by the number of items actually returned, not by
        the page size asked for, since the agile API documents that it may
        return fewer. Pages with `isLast` stop on it. Pages without it
        (the sprint issue list) stop once `total` items have been seen.

        Args:
            path: Endpoint path.
            page_size: maxResults to request.
            params: Extra query parameters.
            items: Name of the list in each page: `values`, or `issues`
                for the sprint issue list.

        Raises:
            ExtractionError: If a non-last page comes back empty, which
                would otherwise loop forever.
        """
        start = 0
        while True:
            page = self.get(path, {**(params or {}), "startAt": start, "maxResults": page_size})
            yield page
            count = len(page.data.get(items, []))
            start += count
            if "isLast" in page.data:
                done = bool(page.data["isLast"])
            elif "total" in page.data:
                done = start >= page.data["total"]
            else:
                raise ExtractionError(f"GET {path} page has neither isLast nor total; cannot tell whether to continue")
            if done:
                return
            if count == 0:
                raise ExtractionError(f"GET {path} returned an empty page before the last one (startAt={start})")

    def token_pages(self, path: str, page_size: int, params: dict[str, Any] | None = None) -> Iterator[Page]:
        """Follow nextPageToken/isLast paging to the last page (enhanced search).

        Raises:
            ExtractionError: If a non-last page has no nextPageToken.
        """
        token: str | None = None
        while True:
            query = {**(params or {}), "maxResults": page_size}
            if token is not None:
                query["nextPageToken"] = token
            page = self.get(path, query)
            yield page
            if "isLast" not in page.data:
                raise ExtractionError(f"GET {path} page has no isLast flag; cannot tell whether to continue")
            if page.data["isLast"]:
                return
            token = page.data.get("nextPageToken")
            if not token:
                raise ExtractionError(f"GET {path} page is not last but has no nextPageToken")
