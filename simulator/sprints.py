"""Sprint and sprint-membership models for the agile simulation.

Per simulator/domain-model.md: SPRINT maps to the sprint object embedded
in Jira's custom field array (e.g. `customfield_10020`). Note that
committed_points, completed_points, and scope_added_points are NOT stored
fields on Sprint — they are computed from SPRINT_ISSUE_MEMBERSHIP +
STATUS_TRANSITION, the same way Jira's own Sprint Report computes them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass
class Sprint:
    """Represents a sprint owned by a team.

    Attributes:
        sprint_id: Primary key identifying the sprint.
        team_id: Foreign key to the team that runs this sprint.
        name: Human-readable sprint name.
        state: Current sprint state (e.g. future/active/closed).
        start_date: Date the sprint starts.
        end_date: Date the sprint ends.
    """

    sprint_id: str
    team_id: str
    name: str
    state: str
    start_date: date
    end_date: date


@dataclass
class SprintIssueMembership:
    """Represents an issue's membership in a sprint.

    Added during domain modeling to track when an issue joined a sprint.
    Without `added_at`, "committed vs. completed vs. scope-added" cannot
    be computed: you'd only know an issue's current sprint, not whether it
    was there from day one or added mid-sprint. This is the internal
    equivalent of what Jira's Sprint Report computes on the fly.

    Attributes:
        issue_id: Foreign key to the issue.
        sprint_id: Foreign key to the sprint.
        added_at: Timestamp at which the issue joined the sprint.
    """

    issue_id: str
    sprint_id: str
    added_at: datetime
