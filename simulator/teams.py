"""Team model for the agile simulation.

Per simulator/domain-model.md: TEAM maps to a Jira "project" (one project
per team). `base_headcount` is simulator-internal only — Jira has no
concept of headcount.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Team:
    """Represents a team (~ Jira Project).

    Attributes:
        team_id: Primary key identifying the team.
        key: Short project-style key (as in a Jira project key).
        name: Human-readable team name.
        base_headcount: Simulator-internal nominal headcount for the team;
            has no equivalent in Jira's data model.
    """

    team_id: str
    key: str
    name: str
    base_headcount: int
