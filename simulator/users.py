"""User model for the agile simulation.

Per simulator/domain-model.md: USER maps to Jira's `accountId` /
`displayName` as seen on `assignee`, `reporter`, and changelog `author`.
Each user belongs primarily to one team for simulation purposes.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class User:
    """Represents a user who can be an assignee, reporter, or transition author.

    Attributes:
        user_id: Primary key identifying the user.
        display_name: Human-readable display name.
        team_id: Foreign key to the team this user primarily belongs to.
    """

    user_id: str
    display_name: str
    team_id: str
