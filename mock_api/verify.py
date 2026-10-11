"""Checks the serializers against the persisted ground truth in raw_data/.

Run from the repository root:

    python -m mock_api.verify [raw_data_dir]

Every check reads values back out of the serialized payload and compares
them with the source entities, so a passing run means the payload says
what the ground truth says — not merely that serialization did not crash.
Exits non-zero on the first category of failure.
"""

from __future__ import annotations

import copy
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime

from mock_api import ids
from mock_api.config import SPRINT_FIELD, STATUS_IDS, STORY_POINTS_FIELD
from mock_api.index import GroundTruthIndex
from mock_api.serializers import (
    UnknownStatusError,
    jira_datetime,
    serialize_changelog,
    serialize_issue,
    serialize_sprint,
)
from simulator.persistence import ENTITY_SPECS

JIRA_TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%f%z"


class CheckFailed(AssertionError):
    pass


def check(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailed(message)


def parse_jira_ts(value: str) -> datetime:
    return datetime.strptime(value, JIRA_TS_FORMAT)


def check_status_coverage(index: GroundTruthIndex) -> set[str]:
    """Fail loudly if any status in the data has no configured ID."""
    seen = {issue.status for issue in index.issues.values()}
    for t in index.collector.status_transitions:
        seen |= {t.from_status, t.to_status}
    missing = seen - STATUS_IDS.keys()
    if missing:
        raise UnknownStatusError(f"statuses with no ID in STATUS_IDS: {sorted(missing)}")
    return seen


def check_issues(index: GroundTruthIndex) -> Counter:
    """Serialize every issue and read key fields back against the source."""
    status_names = {sid: name for name, sid in STATUS_IDS.items()}
    user_by_account = {ids.account_id(u): u for u in index.users}
    tally: Counter = Counter()

    for issue in index.issues.values():
        payload = serialize_issue(issue, index)
        json.dumps(payload)  # must be plain JSON, not just a dict
        f = payload["fields"]
        where = issue.key

        check(payload["key"] == issue.key, f"{where}: key")
        check(payload["id"] == ids.issue_id(issue.issue_id), f"{where}: id")

        check(f[STORY_POINTS_FIELD] == issue.story_points, f"{where}: story points")
        if issue.issue_type == "Epic":
            check(f[STORY_POINTS_FIELD] is None, f"{where}: Epic has story points")

        check(f["status"]["name"] == issue.status, f"{where}: status name")
        check(status_names[f["status"]["id"]] == issue.status, f"{where}: status id")

        if issue.assignee_id is None:
            check(f["assignee"] is None, f"{where}: assignee should be null")
        else:
            back = user_by_account[f["assignee"]["accountId"]]
            check(back == issue.assignee_id, f"{where}: assignee accountId")
            check(
                f["assignee"]["displayName"] == index.users[issue.assignee_id].display_name,
                f"{where}: assignee displayName",
            )
        if issue.reporter_id is not None:
            check(
                user_by_account[f["reporter"]["accountId"]] == issue.reporter_id,
                f"{where}: reporter accountId",
            )

        check(f["issuetype"]["name"] == issue.issue_type, f"{where}: issuetype")
        check(f["project"]["key"] == index.teams[issue.team_id].key, f"{where}: project key")
        check(f["created"] == jira_datetime(issue.created), f"{where}: created")
        check(
            parse_jira_ts(f["created"]).replace(tzinfo=None)
            == issue.created.replace(microsecond=issue.created.microsecond // 1000 * 1000),
            f"{where}: created does not parse back to the source instant",
        )
        if issue.resolutiondate is None:
            check(f["resolutiondate"] is None, f"{where}: resolutiondate should be null")
            tally["unresolved"] += 1
        else:
            check(f["resolutiondate"] == jira_datetime(issue.resolutiondate), f"{where}: resolutiondate")

        if issue.parent_id is None:
            check("parent" not in f, f"{where}: parent present without parent_id")
            tally["no parent"] += 1
        else:
            check(f["parent"]["key"] == index.issues[issue.parent_id].key, f"{where}: parent key")
            tally["with parent"] += 1

        sprints = index.sprints_for(issue)
        if sprints:
            check(
                [s["id"] for s in f[SPRINT_FIELD]] == [ids.sprint_id(s.sprint_id) for s in sprints],
                f"{where}: sprint field",
            )
            tally["in a sprint"] += 1
        else:
            check(f[SPRINT_FIELD] is None, f"{where}: sprint field should be null")

        tally[issue.issue_type] += 1
        tally["total"] += 1
    return tally


def check_changelogs(index: GroundTruthIndex) -> Counter:
    """Check history counts, ordering, and that each history replays its transition."""
    tally: Counter = Counter()
    for issue in index.issues.values():
        transitions = index.transitions_for(issue)
        page = serialize_changelog(issue, index)
        histories = page["values"]
        where = issue.key

        check(page["total"] == len(transitions), f"{where}: total")
        check(len(histories) == len(transitions), f"{where}: history count")

        created = [parse_jira_ts(h["created"]) for h in histories]
        check(created == sorted(created), f"{where}: histories not oldest-first")
        check(len(set(created)) == len(created), f"{where}: two histories share a timestamp")

        for history, t in zip(histories, transitions, strict=True):
            (item,) = history["items"]
            check(item["field"] == "status", f"{where}: item field")
            check(item["fromString"] == t.from_status, f"{where}: fromString")
            check(item["toString"] == t.to_status, f"{where}: toString")
            check(item["from"] == STATUS_IDS[t.from_status], f"{where}: from id")
            check(item["to"] == STATUS_IDS[t.to_status], f"{where}: to id")
            check(history["author"]["accountId"] == ids.account_id(t.author_id), f"{where}: author")
            check(history["created"] == jira_datetime(t.timestamp), f"{where}: created")

        # The history must replay to the issue's current status.
        for prev, nxt in zip(histories, histories[1:]):
            check(
                prev["items"][0]["toString"] == nxt["items"][0]["fromString"],
                f"{where}: history chain broken",
            )
        final = histories[-1]["items"][0]["toString"] if histories else "To Do"
        check(final == issue.status, f"{where}: history ends at {final}, issue is {issue.status}")

        # Paging must partition the same sequence the single page returns.
        paged = []
        start = 0
        while True:
            chunk = serialize_changelog(issue, index, start_at=start, max_results=2)
            paged.extend(chunk["values"])
            if chunk["isLast"]:
                check("nextPage" not in chunk, f"{where}: nextPage on last page")
                break
            start += 2
        check(paged == histories, f"{where}: paged histories differ from single page")

        tally["histories"] += len(histories)
        tally["issues with no history" if not histories else "issues with history"] += 1
    return tally


def check_sprints(index: GroundTruthIndex) -> int:
    """Serialize every sprint and check boundaries line up within each team."""
    by_team = defaultdict(list)
    for sprint in index.sprints.values():
        payload = serialize_sprint(sprint)
        json.dumps(payload)
        check(payload["name"] == sprint.name, f"{sprint.sprint_id}: name")
        check(payload["state"] == sprint.state, f"{sprint.sprint_id}: state")
        check(
            parse_jira_ts(payload["startDate"]).date() == sprint.start_date,
            f"{sprint.sprint_id}: startDate",
        )
        check(
            parse_jira_ts(payload["endDate"]).date() == sprint.end_date,
            f"{sprint.sprint_id}: endDate",
        )
        by_team[sprint.team_id].append((sprint.start_date, payload))

    for team_id, rows in by_team.items():
        rows.sort(key=lambda r: r[0])
        for (_, a), (_, b) in zip(rows, rows[1:]):
            check(a["endDate"] == b["startDate"], f"{team_id}: {a['name']} end != {b['name']} start")
    return len(index.sprints)


def main(raw_dir: str = "raw_data") -> int:
    index = GroundTruthIndex.from_json(raw_dir)
    snapshot = copy.deepcopy(index.collector)

    statuses = check_status_coverage(index)
    print(f"status coverage     : all {len(statuses)} statuses mapped {sorted(statuses)}")

    try:
        issues = check_issues(index)
        changelogs = check_changelogs(index)
        sprint_count = check_sprints(index)
    except CheckFailed as failure:
        print(f"FAILED: {failure}")
        return 1

    print(f"issues serialized   : {dict(issues)}")
    print(f"changelogs          : {dict(changelogs)} "
          f"(ground truth: {len(index.collector.status_transitions)} transitions)")
    print(f"sprints serialized  : {sprint_count}, boundaries contiguous per team")

    for spec in ENTITY_SPECS:
        before = getattr(snapshot, spec.attribute)
        after = getattr(index.collector, spec.attribute)
        if before != after:
            print(f"FAILED: ground truth {spec.attribute} was mutated by serialization")
            return 1
    print("ground truth        : unchanged after serializing everything")
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
