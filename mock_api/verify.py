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
from datetime import date, datetime, time, timedelta

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
from simulator.clock import WORK_START_HOUR
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


BUG_DEADLINE_WORKDAYS = 5
"""The bug SLA from simulator/sprint_cycle.py, restated so the check does not import the rule it checks."""


def _workday(d: date) -> bool:
    return d.weekday() < 5


def _last_workday_before(d: date) -> date:
    d -= timedelta(days=1)
    while not _workday(d):
        d -= timedelta(days=1)
    return d


def _workdays_after(d: date, n: int) -> date:
    for _ in range(n):
        d += timedelta(days=1)
        while not _workday(d):
            d += timedelta(days=1)
    return d


def check_duedates(index: GroundTruthIndex) -> Counter:
    """Check every serialized duedate against the deadline rule, derived independently.

    Rule (docs/domain-model.md, "Deadline (duedate) rule"): an issue gets a
    due date when it first joins a sprint. Story and Task are due on the
    sprint's last workday; Bug is due 5 workdays after the day it joined,
    capped at the sprint's last workday. Issues never in a sprint, which
    includes every Epic, have no due date.
    """
    tally: Counter = Counter()
    for issue in index.issues.values():
        payload_due = serialize_issue(issue, index)["fields"]["duedate"]
        memberships = index.memberships_for(issue)
        where = issue.key
        if not memberships:
            check(payload_due is None, f"{where}: due date set on an issue never in a sprint")
            tally["no sprint, null"] += 1
            continue
        check(issue.issue_type != "Epic", f"{where}: an Epic is in a sprint")
        first = memberships[0]
        sprint = index.sprints[first.sprint_id]
        last_workday = _last_workday_before(sprint.end_date)
        if issue.issue_type == "Bug":
            expected = min(_workdays_after(first.added_at.date(), BUG_DEADLINE_WORKDAYS), last_workday)
        else:
            expected = last_workday
        check(payload_due is not None, f"{where}: in a sprint but has no due date")
        due = date.fromisoformat(payload_due)
        check(due == expected, f"{where}: due date {due}, rule gives {expected}")
        check(due >= first.added_at.date(), f"{where}: due date before it joined the sprint")
        check(due <= last_workday, f"{where}: due date {due} after the sprint's last workday {last_workday}")
        if issue.resolutiondate is not None and issue.issue_type != "Bug":
            sprint_end = datetime.combine(sprint.end_date, time(WORK_START_HOUR))
            check(
                (issue.resolutiondate.date() <= due) == (issue.resolutiondate <= sprint_end),
                f"{where}: date rule disagrees with the exact sprint-end comparison",
            )
        tally[f"{issue.issue_type} with due date"] += 1
    return tally


def check_changelogs(index: GroundTruthIndex) -> Counter:
    """Check history counts, ordering, and that each history replays its source row."""
    tally: Counter = Counter()
    for issue in index.issues.values():
        transitions = index.transitions_for(issue)
        memberships = index.memberships_for(issue)
        expected = len(transitions) + len(memberships)
        page = serialize_changelog(issue, index)
        histories = page["values"]
        where = issue.key

        check(page["total"] == expected, f"{where}: total")
        check(len(histories) == expected, f"{where}: history count")

        created = [parse_jira_ts(h["created"]) for h in histories]
        check(created == sorted(created), f"{where}: histories not oldest-first")
        check(len(set(created)) == len(created), f"{where}: two histories share a timestamp")
        check(len({h["id"] for h in histories}) == len(histories), f"{where}: duplicate history id")

        status_histories = [h for h in histories if h["items"][0]["field"] == "status"]
        sprint_histories = [h for h in histories if h["items"][0]["field"] == "Sprint"]
        check(
            len(status_histories) + len(sprint_histories) == len(histories),
            f"{where}: history with an unexpected field",
        )

        for history, t in zip(status_histories, transitions, strict=True):
            (item,) = history["items"]
            check(item["fromString"] == t.from_status, f"{where}: fromString")
            check(item["toString"] == t.to_status, f"{where}: toString")
            check(item["from"] == STATUS_IDS[t.from_status], f"{where}: from id")
            check(item["to"] == STATUS_IDS[t.to_status], f"{where}: to id")
            check(history["author"]["accountId"] == ids.account_id(t.author_id), f"{where}: author")
            check(history["created"] == jira_datetime(t.timestamp), f"{where}: created")

        for history, m in zip(sprint_histories, memberships, strict=True):
            (item,) = history["items"]
            check(item["fieldtype"] == "custom", f"{where}: sprint fieldtype")
            check(item["fieldId"] == SPRINT_FIELD, f"{where}: sprint fieldId")
            check(
                len(split_list(item["to"])) == len(split_list(item["toString"])),
                f"{where}: sprint IDs and names disagree in length",
            )
            check(
                history["author"]["accountId"] == ids.account_id(issue.reporter_id),
                f"{where}: sprint history author is not the reporter",
            )
            check(history["created"] == jira_datetime(m.added_at), f"{where}: sprint created")

        # The status histories must replay to the issue's current status.
        for prev, nxt in zip(status_histories, status_histories[1:]):
            check(
                prev["items"][0]["toString"] == nxt["items"][0]["fromString"],
                f"{where}: status chain broken",
            )
        final = status_histories[-1]["items"][0]["toString"] if status_histories else "To Do"
        check(final == issue.status, f"{where}: history ends at {final}, issue is {issue.status}")

        check(fetch_all_histories(issue, index) == histories, f"{where}: paged histories differ")

        tally["status histories"] += len(status_histories)
        tally["sprint histories"] += len(sprint_histories)
        tally["issues with no history" if not histories else "issues with history"] += 1
    return tally


def split_list(value: str | None) -> list[str]:
    """Split a multi-value changelog string ("1, 2") into its parts."""
    return [part.strip() for part in value.split(",")] if value else []


def fetch_all_histories(issue, index: GroundTruthIndex, page_size: int = 2) -> list[dict]:
    """Walk the changelog endpoint page by page, as an API client would.

    The small page size makes sure paging boundaries are exercised even
    on issues with only a handful of histories.
    """
    histories: list[dict] = []
    start = 0
    while True:
        chunk = serialize_changelog(issue, index, start_at=start, max_results=page_size)
        histories.extend(chunk["values"])
        if chunk["isLast"]:
            check("nextPage" not in chunk, f"{issue.key}: nextPage on last page")
            return histories
        check("nextPage" in chunk, f"{issue.key}: no nextPage on a non-last page")
        start += page_size


def check_membership_reconstruction(index: GroundTruthIndex) -> int:
    """Rebuild every sprint membership from the serialized changelogs alone.

    For each Sprint history the sprint that was added is whatever appears
    in `to` but not in `from`; the moment it was added is the history's
    `created`. The rebuilt (issue, sprint, added_at) rows must equal the
    ground-truth memberships exactly, with added_at compared at the
    millisecond precision the payload carries.
    """
    sprint_by_jira_id = {serialize_sprint(s)["id"]: s.sprint_id for s in index.sprints.values()}
    rebuilt: Counter = Counter()

    for key, issue in index.issues_by_key.items():
        for history in fetch_all_histories(issue, index):
            for item in history["items"]:
                if item["fieldId"] != SPRINT_FIELD:
                    continue
                before = [int(x) for x in split_list(item["from"])]
                after = [int(x) for x in split_list(item["to"])]
                check(after[: len(before)] == before, f"{key}: a sprint was removed or reordered")
                added = after[len(before) :]
                check(len(added) == 1, f"{key}: history adds {len(added)} sprints, expected 1")
                added_at = parse_jira_ts(history["created"]).replace(tzinfo=None)
                rebuilt[(issue.issue_id, sprint_by_jira_id[added[0]], added_at)] += 1

    truth = Counter(
        (m.issue_id, m.sprint_id, m.added_at.replace(microsecond=m.added_at.microsecond // 1000 * 1000))
        for m in index.collector.sprint_issue_memberships
    )
    missing = truth - rebuilt
    extra = rebuilt - truth
    check(
        not missing and not extra,
        f"membership reconstruction mismatch: {sum(missing.values())} missing "
        f"(e.g. {next(iter(missing), None)}), {sum(extra.values())} extra (e.g. {next(iter(extra), None)})",
    )
    return sum(rebuilt.values())


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
        duedates = check_duedates(index)
        changelogs = check_changelogs(index)
        rebuilt = check_membership_reconstruction(index)
        sprint_count = check_sprints(index)
    except CheckFailed as failure:
        print(f"FAILED: {failure}")
        return 1

    print(f"issues serialized   : {dict(issues)}")
    print(f"due dates           : {dict(duedates)}, all match the rule")
    print(f"changelogs          : {dict(changelogs)} "
          f"(ground truth: {len(index.collector.status_transitions)} transitions, "
          f"{len(index.collector.sprint_issue_memberships)} memberships)")
    print(f"memberships rebuilt : {rebuilt} from changelogs alone, equal to ground truth")
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
