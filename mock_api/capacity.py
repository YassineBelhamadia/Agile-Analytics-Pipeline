"""The capacity endpoint's row format. Our own design, not a Jira contract.

Per docs/domain-model.md ("Ingestion split"), capacity has no real API
to imitate: companies track headcount and PTO in HR tools, spreadsheets
or side tables, each in its own shape. So unlike mock_api/serializers.py,
which reproduces Atlassian's documented shapes, this module defines a
small schema of our own and nothing in it should be read as Jira-shaped.

A row identifies its team and sprint the way the Jira-shaped endpoints
do — team by project key, sprint by the agile API's numeric sprint id —
so ingestion can join capacity to the Jira data without a mapping table.
Field names are snake_case on purpose, to keep the two contracts visibly
apart. Ground-truth ids (`T-ATLAS`, `ATL-S-1`) are not exposed, the same
as everywhere else in the mock API.
"""

from __future__ import annotations

from mock_api import ids
from simulator.capacity import Capacity
from simulator.teams import Team


def serialize_capacity(capacity: Capacity, team: Team) -> dict:
    """Build one capacity row.

    Args:
        capacity: The ground-truth capacity record.
        team: The team it belongs to (for its key).

    Returns:
        `{team_key, sprint_id, headcount, pto_days}`. `pto_days` is passed
        through at full precision: it is a fractional count of days lost
        to absence, and rounding here would make the API disagree with
        the ground truth it is projected from.
    """
    return {
        "team_key": team.key,
        "sprint_id": ids.sprint_id(capacity.sprint_id),
        "headcount": capacity.headcount,
        "pto_days": capacity.pto_days,
    }
