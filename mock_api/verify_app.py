"""Checks the FastAPI app end to end against the persisted ground truth.

Run from the repository root:

    python -m mock_api.verify_app [raw_data_dir]

Drives the app in-process with FastAPI's TestClient. Every check compares
HTTP responses with what the serializers produce directly, or with the
ground truth itself, so a pass means the transport layer adds nothing and
loses nothing. Exits non-zero on the first failure.
"""

from __future__ import annotations

import sys
from collections import Counter

from fastapi.testclient import TestClient

from mock_api import ids
from mock_api.app import create_app
from mock_api.serializers import serialize_changelog, serialize_issue, serialize_sprint

AUTH = {"Authorization": "Basic bW9jazptb2Nr"}


class CheckFailed(AssertionError):
    pass


def check(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailed(message)


def is_jira_error(response, status: int) -> bool:
    body = response.json()
    return (
        response.status_code == status
        and set(body) == {"errorMessages", "errors"}
        and isinstance(body["errorMessages"], list)
        and isinstance(body["errors"], dict)
        and bool(body["errorMessages"] or body["errors"])
    )


def walk_search(client: TestClient, jql: str, page_size: int, **params) -> tuple[list[dict], list[str]]:
    """Follow nextPageToken from the first page to the last; return issues and tokens."""
    issues: list[dict] = []
    tokens: list[str] = []
    token = None
    while True:
        query = {"jql": jql, "maxResults": page_size, **params}
        if token:
            query["nextPageToken"] = token
        response = client.get("/rest/api/3/search/jql", params=query, headers=AUTH)
        check(response.status_code == 200, f"search {jql!r} page {len(tokens)}: {response.status_code}")
        body = response.json()
        check("total" not in body and "startAt" not in body, "search page carries total/startAt")
        issues.extend(body["issues"])
        if body["isLast"]:
            check("nextPageToken" not in body, "last search page carries a nextPageToken")
            return issues, tokens
        check(bool(body.get("nextPageToken")), "non-last search page has no nextPageToken")
        check(len(body["issues"]) == min(page_size, 5000), "non-last page is short")
        token = body["nextPageToken"]
        tokens.append(token)


def check_search(client: TestClient, catalog) -> str:
    projects = [team.key for team in catalog.teams]
    expected = {ids.issue_id(i.issue_id) for i in catalog.issues}
    for size in (1, 7, 50, 5000):
        seen: Counter = Counter()
        # The subset needs a restriction, so the whole dataset is the union
        # of one walk per project.
        for key in projects:
            issues, _ = walk_search(client, f"project = {key}", size)
            check(all(set(i) == {"id"} for i in issues), "default search returned more than ids")
            seen.update(i["id"] for i in issues)
        check(sum(seen.values()) == len(expected) == 530, f"size {size}: {sum(seen.values())} issues, want 530")
        check(set(seen) == expected and max(seen.values()) == 1, f"size {size}: an issue is missing or repeated")

    # Order: ASC by created, and DESC is its exact reverse.
    asc, _ = walk_search(client, "project = PHX ORDER BY created ASC", 13, fields="created")
    desc, _ = walk_search(client, "project = PHX ORDER BY created DESC", 13, fields="created")
    created = [i["fields"]["created"] for i in asc]
    check(created == sorted(created), "ORDER BY created ASC is not ascending")
    check([i["id"] for i in desc] == [i["id"] for i in reversed(asc)], "DESC is not the reverse of ASC")

    # Tokens are stable across calls and bound to their query.
    _, first = walk_search(client, "project = ATL AND status = Done", 5)
    _, second = walk_search(client, "PROJECT = atl and STATUS = 'done'", 5)
    check(first == second and first, "page tokens differ between identical walks")
    other = client.get(
        "/rest/api/3/search/jql",
        params={"jql": "project = NMD", "nextPageToken": first[0]},
        headers=AUTH,
    )
    check(is_jira_error(other, 400), "token replayed on another query was not rejected")

    # fields=... returns full issue objects, projected.
    response = client.get(
        "/rest/api/3/search/jql",
        params={"jql": "issuetype = Bug AND status = 'In Progress'", "fields": "summary,status"},
        headers=AUTH,
    ).json()
    for issue in response["issues"]:
        check(set(issue) == {"id", "key", "self", "fields"}, "search issue with fields lacks id/key/self")
        check(set(issue["fields"]) == {"summary", "status"}, "search fields projection wrong")
        check(issue["fields"]["status"]["name"] == "In Progress", "search status filter wrong")
    return "page sizes 1, 7, 50, 5000 x 3 projects: 530 issues each time, each exactly once"


def check_changelogs(client: TestClient, catalog) -> str:
    busiest = sorted(catalog.issues, key=lambda i: -len(catalog.index.transitions_for(i)))[:15]
    quiet = [i for i in catalog.issues if not catalog.index.transitions_for(i)][:3]
    for issue in busiest + quiet:
        single = client.get(f"/rest/api/3/issue/{issue.key}/changelog", headers=AUTH).json()
        check(single == serialize_changelog(issue, catalog.index), f"{issue.key}: changelog != serializer")
        check(single["maxResults"] == 100, "changelog default page size is not 100")
        for size in (1, 2, 3):
            walked, start = [], 0
            while True:
                page = client.get(
                    f"/rest/api/3/issue/{issue.key}/changelog",
                    params={"startAt": start, "maxResults": size},
                    headers=AUTH,
                ).json()
                walked.extend(page["values"])
                if page["isLast"]:
                    break
                start += size
            check(walked == single["values"], f"{issue.key}: paged changelog differs at size {size}")
    return f"{len(busiest) + len(quiet)} issues x 3 page sizes match the single page"


def check_issues(client: TestClient, catalog) -> str:
    for issue in catalog.issues:
        expected = serialize_issue(issue, catalog.index)
        by_key = client.get(f"/rest/api/3/issue/{issue.key}", headers=AUTH)
        check(by_key.status_code == 200 and by_key.json() == expected, f"{issue.key}: response != serializer")
        by_id = client.get(f"/rest/api/3/issue/{expected['id']}", headers=AUTH).json()
        check(by_id == expected, f"{issue.key}: lookup by numeric id differs")

    sample = max(catalog.issues, key=lambda i: len(catalog.index.transitions_for(i)))
    projected = client.get(
        f"/rest/api/3/issue/{sample.key}", params={"fields": "summary,customfield_10016"}, headers=AUTH
    ).json()
    check(set(projected["fields"]) == {"summary", "customfield_10016"}, "GET issue fields projection")
    excluded = client.get(f"/rest/api/3/issue/{sample.key}", params={"fields": "-assignee"}, headers=AUTH).json()
    check("assignee" not in excluded["fields"] and "status" in excluded["fields"], "GET issue -field exclusion")
    expanded = client.get(f"/rest/api/3/issue/{sample.key}", params={"expand": "changelog"}, headers=AUTH).json()
    oldest_first = serialize_changelog(sample, catalog.index)["values"]
    check(expanded["changelog"]["histories"] == list(reversed(oldest_first)), "expand=changelog not newest-first")
    return f"all {len(catalog.issues)} issues equal serialize_issue, by key and by numeric id"


def check_agile(client: TestClient, catalog) -> str:
    boards, start = [], 0
    while True:
        page = client.get("/rest/agile/1.0/board", params={"startAt": start, "maxResults": 1}, headers=AUTH).json()
        boards.extend(page["values"])
        if page["isLast"]:
            break
        start += 1
    check(len(boards) == 3, f"{len(boards)} boards listed, want 3")

    reached: list[int] = []
    for board in boards:
        start = 0
        while True:
            page = client.get(
                f"/rest/agile/1.0/board/{board['id']}/sprint",
                params={"startAt": start, "maxResults": 5},
                headers=AUTH,
            ).json()
            reached.extend(s["id"] for s in page["values"])
            if page["isLast"]:
                break
            start += 5
        closed = client.get(
            f"/rest/agile/1.0/board/{board['id']}/sprint", params={"state": "closed"}, headers=AUTH
        ).json()
        check(closed["total"] == 12, "state=closed filter wrong")
        future = client.get(
            f"/rest/agile/1.0/board/{board['id']}/sprint", params={"state": "future,active"}, headers=AUTH
        ).json()
        check(future["total"] == 0 and future["isLast"], "state=future,active filter wrong")

    all_sprints = {ids.sprint_id(s.sprint_id): s for s in catalog.index.collector.sprints}
    check(len(reached) == len(set(reached)) == 36 and set(reached) == set(all_sprints), "not all 36 sprints reachable")

    membership_total = 0
    for jira_id, sprint in all_sprints.items():
        got = client.get(f"/rest/agile/1.0/sprint/{jira_id}", headers=AUTH).json()
        check(got == serialize_sprint(sprint), f"{sprint.sprint_id}: sprint != serializer")
        members = {m.issue_id for m in catalog.index.collector.sprint_issue_memberships if m.sprint_id == sprint.sprint_id}
        listed, start = [], 0
        while True:
            page = client.get(
                f"/rest/agile/1.0/sprint/{jira_id}/issue",
                params={"startAt": start, "maxResults": 4},
                headers=AUTH,
            ).json()
            listed.extend(page["issues"])
            if start + 4 >= page["total"]:
                break
            start += 4
        keys = [i["key"] for i in listed]
        expected_keys = {catalog.index.issues[i].key for i in members}
        check(len(keys) == len(set(keys)) and set(keys) == expected_keys, f"{sprint.sprint_id}: issues != memberships")
        for item in listed:
            check(item == serialize_issue(catalog.index.issues_by_key[item["key"]], catalog.index), "sprint issue != serializer")
        membership_total += len(keys)
    check(membership_total == len(catalog.index.collector.sprint_issue_memberships), "membership total mismatch")
    return f"3 boards, all 36 sprints reachable; sprint issues match all {membership_total} memberships"


def check_errors(client: TestClient, catalog) -> str:
    issue = catalog.issues[0]
    cases = [
        ("no Authorization header", client.get(f"/rest/api/3/issue/{issue.key}"), 401),
        ("empty Basic credential", client.get(f"/rest/api/3/issue/{issue.key}", headers={"Authorization": "Basic "}), 401),
        ("unsupported scheme", client.get(f"/rest/api/3/issue/{issue.key}", headers={"Authorization": "Token abc"}), 401),
        ("Bearer accepted", client.get(f"/rest/api/3/issue/{issue.key}", headers={"Authorization": "Bearer x"}), 200),
        ("unknown issue key", client.get("/rest/api/3/issue/PHX-999999", headers=AUTH), 404),
        ("unknown issue id", client.get("/rest/api/3/issue/1", headers=AUTH), 404),
        ("unknown issue changelog", client.get("/rest/api/3/issue/NOPE-1/changelog", headers=AUTH), 404),
        ("unknown sprint", client.get("/rest/agile/1.0/sprint/1", headers=AUTH), 404),
        ("unknown sprint issues", client.get("/rest/agile/1.0/sprint/1/issue", headers=AUTH), 404),
        ("unknown board", client.get("/rest/agile/1.0/board/1/sprint", headers=AUTH), 404),
        ("removed /search endpoint", client.get("/rest/api/3/search", params={"jql": "project = PHX"}, headers=AUTH), 404),
        ("maxResults=0", client.get("/rest/api/3/search/jql", params={"jql": "project = PHX", "maxResults": 0}, headers=AUTH), 400),
        ("maxResults not an int", client.get(f"/rest/api/3/issue/{issue.key}/changelog", params={"maxResults": "x"}, headers=AUTH), 400),
        ("negative startAt", client.get("/rest/agile/1.0/board", params={"startAt": -1}, headers=AUTH), 400),
        ("non-integer sprintId", client.get("/rest/agile/1.0/sprint/abc", headers=AUTH), 400),
        ("bad state filter", client.get(f"/rest/agile/1.0/board/{ids.board_id(catalog.teams[0].team_id)}/sprint", params={"state": "done"}, headers=AUTH), 400),
        ("garbage page token", client.get("/rest/api/3/search/jql", params={"jql": "project = PHX", "nextPageToken": "!!!"}, headers=AUTH), 400),
    ]
    for jql in ("project = PHX OR project = ATL", "project in (PHX, ATL)", "status != Done",
                "assignee = currentUser()", "project = PHX ORDER BY key", "ORDER BY created", "",
                "status = Blocked", "project = PHX AND"):
        cases.append((f"JQL {jql!r}", client.get("/rest/api/3/search/jql", params={"jql": jql}, headers=AUTH), 400))
    for label, response, status in cases:
        if status == 200:
            check(response.status_code == 200, f"{label}: got {response.status_code}")
        else:
            check(is_jira_error(response, status), f"{label}: got {response.status_code} {response.text[:200]}")
    unauthorized = cases[0][1]
    check("WWW-Authenticate" in unauthorized.headers, "401 without WWW-Authenticate")
    return f"{len(cases)} error cases return the right status and Jira's errorMessages/errors shape"


def expected_capacity_rows(catalog) -> list[tuple]:
    """Capacity rows built straight from the ground truth, not via serialize_capacity."""
    key = {t.team_id: t.key for t in catalog.teams}
    return [
        (key[c.team_id], ids.sprint_id(c.sprint_id), c.headcount, c.pto_days)
        for c in catalog.index.collector.capacities
    ]


def capacity_rows_match(api_rows: list[dict], expected: list[tuple]) -> bool:
    """Whether the API rows are exactly the expected rows: same values, each once."""
    got = Counter((r["team_key"], r["sprint_id"], r["headcount"], r["pto_days"]) for r in api_rows)
    fields_ok = all(set(r) == {"team_key", "sprint_id", "headcount", "pto_days"} for r in api_rows)
    return fields_ok and got == Counter(expected)


def walk_capacity(client: TestClient, page_size: int, **params) -> list[dict]:
    rows, start = [], 0
    while True:
        page = client.get(
            "/capacity", params={"startAt": start, "maxResults": page_size, **params}, headers=AUTH
        ).json()
        check(set(page) == {"startAt", "maxResults", "total", "isLast", "values"}, "capacity page envelope")
        rows.extend(page["values"])
        if page["isLast"]:
            check(len(rows) == page["total"], "capacity rows walked != total")
            return rows
        check(len(page["values"]) == page_size, "non-last capacity page is short")
        start += page_size


def check_capacity(client: TestClient, catalog) -> str:
    expected = expected_capacity_rows(catalog)
    check(len(expected) == 36, f"{len(expected)} capacity rows in the ground truth, want 36")
    for size in (1, 5, 7, 50, 100):
        check(capacity_rows_match(walk_capacity(client, size), expected), f"capacity rows differ at page size {size}")

    for team in catalog.teams:
        rows = walk_capacity(client, 5, team=team.key.lower())
        check(len(rows) == 12 and all(r["team_key"] == team.key for r in rows), f"team filter {team.key}")
        check(capacity_rows_match(rows, [e for e in expected if e[0] == team.key]), f"team {team.key} rows differ")
    for sprint in catalog.index.collector.sprints:
        jira_id = ids.sprint_id(sprint.sprint_id)
        rows = walk_capacity(client, 50, sprint=jira_id)
        check(capacity_rows_match(rows, [e for e in expected if e[1] == jira_id]) and len(rows) == 1, f"sprint filter {jira_id}")
    other_team = next(t.key for t in catalog.teams if t.team_id != catalog.index.collector.sprints[0].team_id)
    mismatch = client.get(
        "/capacity", params={"team": other_team, "sprint": ids.sprint_id(catalog.index.collector.sprints[0].sprint_id)}, headers=AUTH
    ).json()
    check(mismatch["total"] == 0 and mismatch["values"] == [], "team + sprint of another team should be empty")

    # Sabotage: the row-equality check must reject a changed value and a missing row.
    rows = walk_capacity(client, 50)
    tampered = [dict(r) for r in rows]
    tampered[0]["pto_days"] += 0.5
    check(not capacity_rows_match(tampered, expected), "row check accepted a changed pto_days")
    check(not capacity_rows_match(rows[1:], expected), "row check accepted a missing row")

    for label, response, status in (
        ("no auth", client.get("/capacity"), 401),
        ("unknown team", client.get("/capacity", params={"team": "ZZZ"}, headers=AUTH), 404),
        ("unknown sprint", client.get("/capacity", params={"sprint": 1}, headers=AUTH), 404),
        ("non-integer sprint", client.get("/capacity", params={"sprint": "abc"}, headers=AUTH), 400),
        ("maxResults=0", client.get("/capacity", params={"maxResults": 0}, headers=AUTH), 400),
        ("negative startAt", client.get("/capacity", params={"startAt": -1}, headers=AUTH), 400),
    ):
        check(is_jira_error(response, status), f"capacity {label}: got {response.status_code} {response.text[:200]}")
    return "36 rows at page sizes 1, 5, 7, 50, 100; team and sprint filters exact; sabotage caught; 6 error cases"


def check_refuses_to_start() -> str:
    try:
        with TestClient(create_app("/nonexistent/raw_data")):
            pass
    except RuntimeError as exc:
        check("generate_raw_data.py" in str(exc), "startup error does not say how to fix it")
        return f"missing raw_data refuses startup: {str(exc)[:80]}..."
    raise CheckFailed("app started without raw_data")


def main(raw_dir: str = "raw_data") -> int:
    try:
        with TestClient(create_app(raw_dir)) as client:
            catalog = client.app.state.catalog
            for name, run in (
                ("search", check_search),
                ("changelog", check_changelogs),
                ("issues", check_issues),
                ("agile", check_agile),
                ("errors", check_errors),
                ("capacity", check_capacity),
            ):
                print(f"{name:<10}: {run(client, catalog)}")
        print(f"{'startup':<10}: {check_refuses_to_start()}")
    except CheckFailed as failure:
        print(f"FAILED: {failure}")
        return 1
    print("ALL APP CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
