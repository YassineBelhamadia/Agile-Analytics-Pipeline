"""Computes commitment, scope creep and completion for a sprint.

Implements the three formulas set out in docs/domain-model.md under
"Where 'commit vs. completion vs. scope-added' actually comes from".

The important property of this module is what it does *not* do: it stores
nothing. Committed, completed and scope-added points are not fields on
Sprint and never will be, because they are not fields on a sprint in real
Jira either — Jira's own Sprint Report derives them on demand by
comparing what was in the sprint at the start against what was in it at
the end. Recomputing them here from SprintIssueMembership and the issues'
resolution dates keeps the simulator's ground truth in the same shape as
the real system, so the mock API layer can serve a sprint report by
calling this rather than by inventing numbers of its own.
"""

from __future__ import annotations

from typing import Any

from simulator.collector import DataCollector
from simulator.sprints import Sprint

BUG = "Bug"


def compute_sprint_report(sprint: Sprint, collector: DataCollector) -> dict[str, Any]:
    """Derive a sprint's commitment, scope creep and completion figures.

    Per docs/domain-model.md:

    - **Committed** is the points of issues whose membership `added_at`
      falls at or before the sprint start — the work the team signed up
      for at planning.
    - **Scope added** is the points of issues that joined after the start
      — work pulled in mid-sprint, which is why SprintIssueMembership
      records `added_at` at all.
    - **Completed** is the points of member issues resolved on or before
      the sprint end date.

    A sprint counts as hit when completed points reach the committed
    figure. Note that completed work includes scope-added issues, so a
    team can hit its commitment on paper while having delivered something
    other than what it committed to — a real and deliberately preserved
    ambiguity in this metric, and one reason the scope-added figure is
    reported alongside rather than folded in.

    The date-level comparison is inherited from the domain model's
    formulas. One consequence worth knowing: a scope add that lands on
    the sprint's own start date is indistinguishable from committed work
    at this granularity and will be counted as committed.

    Args:
        sprint: The sprint to report on.
        collector: Ground truth holding the issues and memberships.

    Returns:
        A dict with the sprint's `name`, `committed`, `scope_added`,
        `completed`, `hit`, and `bug_count`/`total_count` across its
        member issues.
    """
    issues_by_id = {issue.issue_id: issue for issue in collector.issues}
    members = [
        (membership, issues_by_id[membership.issue_id])
        for membership in collector.sprint_issue_memberships
        if membership.sprint_id == sprint.sprint_id
    ]

    committed = sum(
        issue.story_points
        for membership, issue in members
        if membership.added_at.date() <= sprint.start_date
    )
    scope_added = sum(
        issue.story_points
        for membership, issue in members
        if membership.added_at.date() > sprint.start_date
    )
    completed = sum(
        issue.story_points
        for _, issue in members
        if issue.resolutiondate is not None and issue.resolutiondate.date() <= sprint.end_date
    )

    return {
        "name": sprint.name,
        "committed": committed,
        "scope_added": scope_added,
        "completed": completed,
        "hit": bool(completed >= committed),
        "bug_count": sum(1 for _, issue in members if issue.issue_type == BUG),
        "total_count": len(members),
    }
