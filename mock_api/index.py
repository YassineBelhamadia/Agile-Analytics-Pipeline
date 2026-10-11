"""Read-only lookups over the simulator's ground truth.

Every serializer needs to hop between entities — an issue to its team,
its assignee, its transitions, its sprints. The DataCollector holds flat
lists, so this module builds the dictionaries those hops need, once, when
the dataset is loaded.

The index holds references to the collector's own entity objects, not
copies, and never assigns to them. Per docs/domain-model.md the mock API
keeps no second copy of the data; this is a set of pointers into the one
copy that exists.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from mock_api import ids
from simulator.collector import DataCollector
from simulator.issues import Issue, StatusTransition
from simulator.persistence import load_from_json
from simulator.sprints import Sprint
from simulator.teams import Team
from simulator.users import User


class GroundTruthIndex:
    """Lookup tables over one DataCollector, keyed the way serializers need.

    Attributes:
        collector: The ground truth this index points into.
        teams: Team by team_id.
        users: User by user_id.
        sprints: Sprint by sprint_id.
        issues: Issue by issue_id.
        issues_by_key: Issue by Jira issue key.
        transitions_by_issue: Each issue's transitions, oldest first.
        sprints_by_issue: Each issue's sprints, earliest start first.
    """

    def __init__(self, collector: DataCollector) -> None:
        self.collector = collector
        self.teams: dict[str, Team] = _unique_by(collector.teams, "team_id")
        self.users: dict[str, User] = _unique_by(collector.users, "user_id")
        self.sprints: dict[str, Sprint] = _unique_by(collector.sprints, "sprint_id")
        self.issues: dict[str, Issue] = _unique_by(collector.issues, "issue_id")
        self.issues_by_key: dict[str, Issue] = _unique_by(collector.issues, "key")

        transitions: dict[str, list[StatusTransition]] = defaultdict(list)
        for transition in collector.status_transitions:
            transitions[transition.issue_id].append(transition)
        # Sorted into new lists: the collector's own list order is left
        # exactly as loaded.
        self.transitions_by_issue: dict[str, list[StatusTransition]] = {
            issue_id: sorted(rows, key=lambda t: t.timestamp)
            for issue_id, rows in transitions.items()
        }

        memberships: dict[str, list[Sprint]] = defaultdict(list)
        for membership in collector.sprint_issue_memberships:
            memberships[membership.issue_id].append(self.sprints[membership.sprint_id])
        self.sprints_by_issue: dict[str, list[Sprint]] = {
            issue_id: sorted(rows, key=lambda s: s.start_date)
            for issue_id, rows in memberships.items()
        }

        # Checked once here rather than per request: a collision would
        # make two entities indistinguishable in every payload.
        ids.assert_unique("accountId", self.users, ids.account_id)
        ids.assert_unique("issue", self.issues, ids.issue_id)
        ids.assert_unique("project", self.teams, ids.project_id)
        ids.assert_unique("board", self.teams, ids.board_id)
        ids.assert_unique("sprint", self.sprints, ids.sprint_id)
        ids.assert_unique(
            "changelog",
            (t.transition_id for t in collector.status_transitions),
            ids.changelog_id,
        )

    @classmethod
    def from_json(cls, input_dir: Path | str) -> GroundTruthIndex:
        """Load a persisted ground truth directory and index it.

        Args:
            input_dir: Directory written by `persistence.dump_to_json`.

        Returns:
            An index over the reloaded collector.
        """
        return cls(load_from_json(input_dir))

    def transitions_for(self, issue: Issue) -> list[StatusTransition]:
        """Return an issue's status transitions, oldest first.

        Args:
            issue: The issue whose history is wanted.

        Returns:
            The transitions, empty for an issue that never left To Do.
        """
        return self.transitions_by_issue.get(issue.issue_id, [])

    def sprints_for(self, issue: Issue) -> list[Sprint]:
        """Return the sprints an issue has been a member of, earliest first.

        Args:
            issue: The issue whose sprint membership is wanted.

        Returns:
            The sprints, empty for an issue never pulled into a sprint.
        """
        return self.sprints_by_issue.get(issue.issue_id, [])


def _unique_by(rows: list, attribute: str) -> dict:
    """Index `rows` by one attribute, refusing duplicate values.

    A duplicate key would make every lookup on it silently return
    whichever row happened to be indexed last, so it is raised instead.

    Args:
        rows: Entities to index.
        attribute: Name of the attribute to key them by.

    Returns:
        Attribute value to entity.

    Raises:
        ValueError: If two rows share a value for `attribute`.
    """
    indexed: dict = {}
    for row in rows:
        value = getattr(row, attribute)
        if value in indexed:
            raise ValueError(f"duplicate {type(row).__name__}.{attribute}: {value!r}")
        indexed[value] = row
    return indexed
