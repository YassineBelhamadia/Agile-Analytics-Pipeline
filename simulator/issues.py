"""Issue and status transition models for the agile simulation.

Per simulator/domain-model.md: ISSUE maps to `fields.*` on a Jira issue
payload. STATUS_TRANSITION is the fact-table grain, mapping to one entry
in `changelog.histories[].items[]` where `field == "status"`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass
class Issue:
    """Represents a single tracked work item (~ a Jira issue).

    Notes:
        - `parent_id` is self-referential and handles both Epic->Story and
          Story->Task/Bug links — a simplification of Jira's real split
          between `parent` (subtasks) and the Epic Link custom field, made
          deliberately for v1.
        - `story_points` is not applicable to the Epic issue_type: Epics
          have no direct story_points field, and if shown at all it is a
          rollup (sum) of child stories' points, computed rather than
          stored. Story, Task, and Bug all carry a real story_points
          value (Bug is deliberately included so it counts toward
          velocity, per the domain model's "Story points by issue type"
          decision).
        - `status` is the issue's current status (a denormalized
          convenience); the authoritative history lives in
          StatusTransition.

    Attributes:
        issue_id: Primary key identifying the issue.
        key: Human-readable issue key (as in a Jira issue key).
        issue_type: One of epic/story/task/bug.
        team_id: Foreign key to the owning team.
        parent_id: Self-referential foreign key to a parent Issue, or None.
        assignee_id: Foreign key to the assigned User, or None.
        reporter_id: Foreign key to the reporting User, or None.
        story_points: Story point estimate; not applicable for Epics.
        status: Current status of the issue.
        created: Date the issue was created.
        duedate: Due date for the issue, or None.
        resolutiondate: Date the issue was resolved, or None.
    """

    issue_id: str
    key: str
    issue_type: str
    team_id: str
    parent_id: str | None
    assignee_id: str | None
    reporter_id: str | None
    story_points: int | None
    status: str
    created: date
    duedate: date | None
    resolutiondate: date | None


@dataclass
class StatusTransition:
    """Represents a single status change in an issue's history.

    The fact-table grain: one row per status change, with a timestamp —
    this is what makes cycle-time and time-in-status questions answerable.

    Attributes:
        transition_id: Primary key identifying the transition.
        issue_id: Foreign key to the issue that transitioned.
        from_status: Status the issue transitioned from.
        to_status: Status the issue transitioned to.
        timestamp: Datetime the transition occurred.
        author_id: Foreign key to the User who performed the transition.
    """

    transition_id: str
    issue_id: str
    from_status: str
    to_status: str
    timestamp: datetime
    author_id: str
