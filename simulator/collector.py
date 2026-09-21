"""In-memory accumulator for the simulation's ground truth.

Per docs/domain-model.md's "ground truth vs. views" principle, the
simulator's job is to produce one rich, internally-consistent ground
truth dataset; persistence and Jira-shaped serialization are separate
layers built on top of it. DataCollector is the object that holds that
ground truth while the simulation runs.

It is deliberately a dumb accumulator: it knows how to construct each
domain dataclass from field values and append it to the right list, and
nothing else. It has no file I/O, no JSON, no database access, and no
simulation logic (no scheduling, no randomness, no business rules about
when an issue transitions). That separation matters because it keeps the
simulation's behavioral logic (SimPy processes deciding *when* and *why*
something happens) decoupled from how the resulting data is stored and,
later, serialized — the same separation the mock API relies on when it
projects this ground truth into Jira-shaped API responses.
"""

from __future__ import annotations

from datetime import date, datetime

from simulator.capacity import Capacity
from simulator.issues import Issue, StatusTransition
from simulator.sprints import Sprint, SprintIssueMembership
from simulator.teams import Team
from simulator.users import User


class DataCollector:
    """Accumulates every entity produced by a simulation run, in memory.

    Holds one list per entity type. Teams, users, sprints, and capacity
    records are typically all created up front before the simulation
    clock starts moving, while issues, status transitions, and sprint
    memberships are typically emitted as the simulation runs. Either way,
    by the end of a run this object is the single source of the full
    dataset, ready to be handed to a persistence or serialization layer.
    """

    def __init__(self) -> None:
        """Initialize an empty DataCollector with one list per entity type."""
        self.teams: list[Team] = []
        self.users: list[User] = []
        self.sprints: list[Sprint] = []
        self.issues: list[Issue] = []
        self.status_transitions: list[StatusTransition] = []
        self.sprint_issue_memberships: list[SprintIssueMembership] = []
        self.capacities: list[Capacity] = []

        self._next_transition_id = 1

    def _generate_transition_id(self) -> str:
        """Generate the next deterministic transition_id.

        StatusTransition rows are the one entity in this model whose ID is
        assigned at record time rather than known upfront by the caller
        (a Team, User, Sprint, or Issue is typically constructed with its
        ID already decided; a transition is only decided into existence as
        it happens). A simple incrementing counter is used instead of
        uuid4() specifically because the simulation must be reproducible:
        two runs seeded with the same SEED should produce byte-identical
        transition IDs, which a random UUID would not guarantee.
        """
        transition_id = f"TR-{self._next_transition_id}"
        self._next_transition_id += 1
        return transition_id

    def record_team(self, team_id: str, key: str, name: str, base_headcount: int) -> Team:
        """Construct a Team and append it to `teams`."""
        team = Team(team_id=team_id, key=key, name=name, base_headcount=base_headcount)
        self.teams.append(team)
        return team

    def record_user(self, user_id: str, display_name: str, team_id: str) -> User:
        """Construct a User and append it to `users`."""
        user = User(user_id=user_id, display_name=display_name, team_id=team_id)
        self.users.append(user)
        return user

    def record_sprint(
        self,
        sprint_id: str,
        team_id: str,
        name: str,
        state: str,
        start_date: date,
        end_date: date,
    ) -> Sprint:
        """Construct a Sprint and append it to `sprints`."""
        sprint = Sprint(
            sprint_id=sprint_id,
            team_id=team_id,
            name=name,
            state=state,
            start_date=start_date,
            end_date=end_date,
        )
        self.sprints.append(sprint)
        return sprint

    def record_issue(
        self,
        issue_id: str,
        key: str,
        issue_type: str,
        team_id: str,
        parent_id: str | None,
        assignee_id: str | None,
        reporter_id: str | None,
        story_points: int | None,
        status: str,
        created: datetime,
        duedate: date | None,
        resolutiondate: datetime | None,
    ) -> Issue:
        """Construct an Issue and append it to `issues`."""
        issue = Issue(
            issue_id=issue_id,
            key=key,
            issue_type=issue_type,
            team_id=team_id,
            parent_id=parent_id,
            assignee_id=assignee_id,
            reporter_id=reporter_id,
            story_points=story_points,
            status=status,
            created=created,
            duedate=duedate,
            resolutiondate=resolutiondate,
        )
        self.issues.append(issue)
        return issue

    def record_transition(
        self,
        issue_id: str,
        from_status: str,
        to_status: str,
        timestamp: datetime,
        author_id: str,
    ) -> StatusTransition:
        """Construct a StatusTransition and append it to `status_transitions`.

        The transition_id is assigned here, deterministically, via
        `_generate_transition_id` — the caller does not supply one, since
        a transition's identity is only decided at the moment it's
        recorded.
        """
        transition = StatusTransition(
            transition_id=self._generate_transition_id(),
            issue_id=issue_id,
            from_status=from_status,
            to_status=to_status,
            timestamp=timestamp,
            author_id=author_id,
        )
        self.status_transitions.append(transition)
        return transition

    def record_sprint_membership(
        self,
        issue_id: str,
        sprint_id: str,
        added_at: datetime,
    ) -> SprintIssueMembership:
        """Construct a SprintIssueMembership and append it to `sprint_issue_memberships`."""
        membership = SprintIssueMembership(
            issue_id=issue_id,
            sprint_id=sprint_id,
            added_at=added_at,
        )
        self.sprint_issue_memberships.append(membership)
        return membership

    def record_capacity(
        self,
        team_id: str,
        sprint_id: str,
        headcount: int,
        pto_days: float,
    ) -> Capacity:
        """Construct a Capacity and append it to `capacities`."""
        capacity = Capacity(
            team_id=team_id,
            sprint_id=sprint_id,
            headcount=headcount,
            pto_days=pto_days,
        )
        self.capacities.append(capacity)
        return capacity

    def summary(self) -> dict[str, int]:
        """Return the count of recorded entities per entity type.

        Intended for quick sanity-checking after a run (e.g. "did we get
        roughly the number of issues and transitions we expected?")
        without needing to print or inspect the full in-memory dataset.
        """
        return {
            "teams": len(self.teams),
            "users": len(self.users),
            "sprints": len(self.sprints),
            "issues": len(self.issues),
            "status_transitions": len(self.status_transitions),
            "sprint_issue_memberships": len(self.sprint_issue_memberships),
            "capacities": len(self.capacities),
        }
