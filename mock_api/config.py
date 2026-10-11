"""Fixed, instance-wide constants for the Jira-shaped mock API.

A real Jira Cloud site has one set of status IDs, one set of issue type
IDs, and one ID per custom field, shared by every project on the site.
The mock API models a single Jira instance hosting all three teams, so
these values are hardcoded once here and are identical for every team.
None of them come from the ground truth: Jira assigns them, the
simulator has no reason to know them.

Everything in this module is a deliberate choice for this mock instance,
not a value Jira guarantees. Custom field IDs in particular differ from
site to site in real Jira, which is exactly why the ingestion client is
expected to own the mapping from `customfield_XXXXX` back to a clean name.
"""

from __future__ import annotations

from simulator.clock import WORK_START_HOUR

BASE_URL = "http://localhost:8000"
"""Origin used to build every `self` URL in a payload.

Points at where the mock API will be served locally, so a client that
follows a `self` link lands on the mock rather than on a real site.
"""

STATUS_IDS: dict[str, str] = {
    "To Do": "10000",
    "In Progress": "10001",
    "Code Review": "10002",
    "QA": "10003",
    "Done": "10004",
}
"""Status name to Jira status ID, for the five statuses in the workflow.

Jira returns status IDs as strings, both on `fields.status.id` and in
changelog `from`/`to`. A status that appears in the ground truth but not
here is a configuration error, and the serializers raise on it rather
than inventing an ID.
"""

ISSUE_TYPES: dict[str, dict[str, object]] = {
    "Epic": {"id": "10100", "hierarchyLevel": 1},
    "Story": {"id": "10101", "hierarchyLevel": 0},
    "Task": {"id": "10102", "hierarchyLevel": 0},
    "Bug": {"id": "10103", "hierarchyLevel": 0},
}
"""Issue type name to its fixed ID and hierarchy level.

Jira documents hierarchy level 0 as the base level, with higher integers
above it and negative values for subtasks; Epic sits one level above the
base types. No subtask types are generated, so nothing here is negative.
"""

STORY_POINTS_FIELD = "customfield_10016"
"""Custom field carrying story points, under its opaque Jira field ID."""

SPRINT_FIELD = "customfield_10020"
"""Custom field carrying an issue's sprint membership, as an array."""

SPRINT_BOUNDARY_HOUR = WORK_START_HOUR
"""Hour of day at which a sprint starts and the previous one ends.

The ground truth stores sprint boundaries as bare dates, but the
simulator cuts sprints at the start of a workday, and each sprint's end
date is the next sprint's start date. Pinning both to this hour
reproduces the instant the simulator actually used, so a sprint's
`endDate` and its successor's `startDate` are the same moment.
"""
