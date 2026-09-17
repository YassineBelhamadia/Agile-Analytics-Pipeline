"""Capacity model for the agile simulation.

Per simulator/domain-model.md's "Ingestion split" decision, Capacity is
architecturally separate from the Jira-shaped entities in teams.py,
users.py, sprints.py, and issues.py. Those entities must be faithful to
Jira's real API contract and are served through the Jira-shaped mock API.
Capacity has no real universal contract to be faithful to — every company
tracks headcount/PTO differently — so it is instead served through its
own separate, custom mock endpoint (e.g. `GET /capacity?team=X&sprint=Y`)
with a schema of our own design, keeping the ingestion layer symmetric
(two clients, two APIs) rather than mixing an API client with a flat-file
reader.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Capacity:
    """Represents a team's tracked headcount and PTO for a given sprint.

    Attributes:
        team_id: Foreign key to the team.
        sprint_id: Foreign key to the sprint.
        headcount: Headcount tracked for the team during the sprint.
        pto_days: Total PTO days taken by the team during the sprint.
    """

    team_id: str
    sprint_id: str
    headcount: int
    pto_days: float
