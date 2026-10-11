"""SimPy process generating a stream of new issues for one team.

lifecycle.py models what happens to an issue once it exists. This module
models where issues come from: a continuous, irregular trickle of new
work arriving at a team that has only so many developers to absorb it.

Arrivals deliberately do not start any work. A newly filed issue lands in
a shared backlog and sits there until something decides to pull it —
which, in the full simulation, is sprint planning or a mid-sprint scope
add (see sprint_cycle.py). That split matters: it is what makes the
backlog a real queue with a real depth, and it is what allows commitment
and scope creep to be modelled as decisions rather than as accidents of
when a ticket happened to be filed.

This module also introduces the Epic/Story hierarchy into the generated
data. Epics arrive through the same stream as other work but are treated
as a different kind of thing entirely: they are containers that later
arrivals attach themselves to, not units of work that move across the
board, and they never enter the backlog.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Generator, Sequence

import numpy as np
import simpy

from simulator.clock import sim_to_real
from simulator.collector import DataCollector
from simulator.issues import Issue
from simulator.lifecycle import TODO
from simulator.rng import choice, weighted_choice
from simulator.teams import Team

EPIC = "Epic"
STORY = "Story"
TASK = "Task"
BUG = "Bug"

ARRIVAL_RATE_PER_HOUR = 0.15
"""Default mean number of new issues arriving per working hour.

A **team-level tunable**, not a simulation-wide constant: intake volume
is a property of a particular team's remit and traffic (see
personalities.py). The value here is the baseline default, used by this
module's own demo; every other caller passes its own value explicitly.

Chosen relative to what the team can actually absorb. Over one ten-day
sprint (80 working hours) this yields roughly 12 arrivals, about 52 story
points, against a four-developer team that delivers somewhere near 40
points in the same window.

Intake therefore runs deliberately ahead of throughput. That gap is the
point: it keeps the backlog genuinely deep, which is what makes sprint
planning a real decision (the team chooses what to pull) rather than a
formality (the team takes whatever happens to have arrived). Set much
lower and the backlog empties, planning stops binding, and the
commitment-versus-capacity relationship the analytics exist to study
disappears.
"""

ISSUE_TYPE_WEIGHTS: dict[str, float] = {
    STORY: 0.45,
    TASK: 0.25,
    BUG: 0.22,
    EPIC: 0.08,
}
"""Default relative frequency of each issue type among arrivals.

A **team-level tunable** — a team maintaining a fragile legacy service
sees a very different bug share from one building something new (see
personalities.py). This is the baseline default, used by this module's
own demo.

Stories dominate as the primary unit of planned work, with tasks and bugs
together making up roughly half of the remainder — a bug share in the low
twenties percent is typical of a team maintaining a live product while
still shipping features. Epics are deliberately rare at 8%: an epic is a
large container spanning weeks of work that a team opens occasionally
when starting a new initiative, not something created alongside every
other ticket. Weighting them anywhere near the others would produce
absurd data — dozens of epics each holding one story.
"""

EPIC_LINK_PROBABILITY = 0.5
"""Chance a new Story/Task/Bug is attached to an existing Epic.

An even split reflects the reality that only about half of a team's work
belongs to a planned initiative. The rest is unplanned or standalone:
production bugs, maintenance chores, one-off requests. Linking every
issue to an epic would erase that distinction and make epic-level rollups
uninformative, since they would simply account for all work.
"""

STORY_POINT_WEIGHTS: dict[int, float] = {
    1: 0.10,
    2: 0.20,
    3: 0.30,
    5: 0.25,
    8: 0.10,
    13: 0.05,
}
"""Relative frequency of each story point value on the Fibonacci scale.

Centred on 3 and right-skewed: teams estimate most work as small, a fair
amount as medium, and only occasionally reach for a 13 — which is usually
a signal the work should have been split. The resulting mean of about 4.1
points is what ARRIVAL_RATE_PER_HOUR was calibrated against.
"""


def _sample_interarrival_hours(rng: np.random.Generator, arrival_rate_per_hour: float) -> float:
    """Sample the gap in working hours until the next issue arrives.

    Exponentially distributed gaps are the standard model for arrivals
    that are independent and memoryless — each new issue is filed without
    regard to when the last one was, so how long the team has already
    waited tells you nothing about how much longer the wait will be. The
    practical consequence is clustering: exponential gaps produce quiet
    stretches punctuated by several issues landing close together, which
    is what real intake looks like.

    A fixed gap would be the wrong model in a way that quietly flatters
    the results. Perfectly evenly spaced arrivals never cluster, so a team
    sized to the average load would never fall behind, and the queueing
    and backlog effects this simulation exists to produce would largely
    disappear.

    Args:
        rng: The team's random stream.
        arrival_rate_per_hour: The team's mean arrivals per working hour.
    """
    return float(rng.exponential(1.0 / arrival_rate_per_hour))


def _sample_issue_type(rng: np.random.Generator, weights: dict[str, float]) -> str:
    """Pick an issue type according to the supplied weights.

    The weights are a parameter rather than a module constant because the
    active mix changes over the course of a run — a bug-incident sprint
    swaps in a bug-heavy distribution (see sprint_cycle.py).

    Args:
        rng: The team's random stream.
        weights: Issue type to relative frequency. Need not sum to 1.
    """
    return weighted_choice(rng, weights)


def _sample_story_points(rng: np.random.Generator) -> int:
    """Pick a story point estimate according to STORY_POINT_WEIGHTS."""
    return weighted_choice(rng, STORY_POINT_WEIGHTS)


def _pick_parent_epic(rng: np.random.Generator, open_epics: Sequence[str]) -> str | None:
    """Decide which Epic, if any, a new issue belongs to.

    Args:
        rng: The team's random stream.
        open_epics: issue_ids of Epics created so far in this run.

    Returns:
        An epic's issue_id, or None if no epic exists yet or the roll
        against EPIC_LINK_PROBABILITY came up short.
    """
    if not open_epics:
        return None
    if rng.random() >= EPIC_LINK_PROBABILITY:
        return None
    return choice(rng, open_epics)


def arrivals_process(
    env: simpy.Environment,
    team: Team,
    backlog: deque[Issue],
    collector: DataCollector,
    author_ids: Sequence[str],
    weights_ref: dict[str, dict[str, float]],
    arrival_rate_per_hour: float,
    issue_type_weights: dict[str, float],
    rng: np.random.Generator,
) -> Generator[simpy.Event, None, None]:
    """Generate new issues for `team` for as long as the simulation runs.

    Loops forever, waiting an exponentially distributed gap between
    arrivals and creating one issue each time. The process has no notion
    of when the simulation ends; the caller bounds it with
    `env.run(until=...)`, which simply stops the clock mid-wait.

    Nothing is started here. Non-Epic issues are appended to `backlog`
    and wait to be pulled by whoever is deciding what to work on — in the
    full simulation, sprint planning and mid-sprint scope adds. Arrivals
    therefore have no opinion about capacity, sprints or scheduling, and
    need no reference to the developer pool.

    The active issue type mix is read from `weights_ref["current"]` on
    every iteration rather than captured once. That indirection is the
    entire coupling between this process and sprint planning: a bug
    incident is triggered by another process swapping the dict entry, and
    arrivals pick up the new mix on their next draw without knowing that
    sprints, incidents, or sprint_cycle.py exist at all.

    This process seeds `weights_ref` from its own `issue_type_weights`
    before its first arrival, writing both `"current"` (the mix in force
    right now) and `"base"` (the team's normal mix, which sprint planning
    reverts to once an incident sprint ends). Seeding here rather than at
    the call site keeps the team's mix in one place, and is safe because
    sprint planning waits out a backlog warmup before it reads either key.

    Epics are created but never enter the backlog. This is a statement
    about what an Epic *is* rather than a shortcut. An Epic is not a
    smaller or simpler unit of work — it is a container for other issues.
    It has no story points of its own (its points are the rollup of its
    children, per the domain model), so there is nothing to drive an In
    Progress duration; and "code review" or "QA" have no meaning applied
    to a body of work rather than a change. Giving Epics a trivial or
    no-op lifecycle would be worse than excluding them: it would emit
    status transitions that never happen in real Jira data and would
    corrupt any cycle-time or throughput metric that reads the transition
    table without filtering on issue type.

    Args:
        env: The SimPy environment driving the simulation clock.
        team: The team the generated issues belong to. Its `key` prefixes
            the generated issue keys.
        backlog: Shared FIFO queue of unstarted work. Non-Epic issues are
            appended here in creation order; the caller pulls from the
            left to work oldest-first.
        collector: Accumulator receiving every issue created here.
        author_ids: Candidate users to draw assignees and reporters from.
        weights_ref: Shared dict holding the active issue type weights
            under "current" (re-read on each arrival) and the team's
            normal mix under "base". Populated here at process start.
        arrival_rate_per_hour: The team's mean arrivals per working hour.
        issue_type_weights: The team's normal issue type mix.
        rng: The team's own random stream (see simulator/rng.py). Every
            draw this process makes comes from it.

    Yields:
        SimPy timeout events, one per gap between arrivals.
    """
    weights_ref["base"] = issue_type_weights
    weights_ref["current"] = issue_type_weights

    open_epics: list[str] = []
    issue_number = 0

    while True:
        yield env.timeout(_sample_interarrival_hours(rng, arrival_rate_per_hour))

        issue_number += 1
        issue_type = _sample_issue_type(rng, weights_ref["current"])
        is_epic = issue_type == EPIC

        issue = collector.record_issue(
            # Namespaced by team key: several teams share one DataCollector,
            # and their per-process counters both start at 1.
            issue_id=f"{team.key}-I-{issue_number}",
            key=f"{team.key}-{issue_number}",
            issue_type=issue_type,
            team_id=team.team_id,
            # Epics are top-level in v1: no epic-inside-an-epic nesting.
            parent_id=None if is_epic else _pick_parent_epic(rng, open_epics),
            assignee_id=choice(rng, author_ids),
            reporter_id=choice(rng, author_ids),
            story_points=None if is_epic else _sample_story_points(rng),
            status=TODO,
            created=sim_to_real(env.now),
            duedate=None,
            resolutiondate=None,
        )

        if is_epic:
            # Never queued for work: an Epic has no story_points to drive an
            # In Progress duration, and code review and QA are meaningless
            # for a container of work. It exists only to be linked against.
            open_epics.append(issue.issue_id)
        else:
            backlog.append(issue)


if __name__ == "__main__":
    from collections import Counter

    from simulator.clock import SEED, WORK_HOURS_PER_DAY
    from simulator.rng import team_rng

    DEMO_HEADCOUNT = 3
    SPRINT_LENGTH_DAYS = 10
    SPRINTS_TO_RUN = 2
    RUN_HOURS = SPRINTS_TO_RUN * SPRINT_LENGTH_DAYS * WORK_HOURS_PER_DAY

    env = simpy.Environment()
    collector = DataCollector()

    team = collector.record_team(
        team_id="T-1",
        key="AAP",
        name="Platform",
        base_headcount=DEMO_HEADCOUNT,
    )
    for n in range(1, DEMO_HEADCOUNT + 1):
        collector.record_user(user_id=f"U-{n}", display_name=f"Dev {n}", team_id=team.team_id)
    author_ids = [user.user_id for user in collector.users]

    # Standalone, nothing pulls from the backlog, so it only ever grows.
    backlog: deque[Issue] = deque()
    # Populated by arrivals_process itself from the weights passed below.
    weights_ref: dict[str, dict[str, float]] = {}

    env.process(
        arrivals_process(
            env,
            team,
            backlog,
            collector,
            author_ids,
            weights_ref,
            arrival_rate_per_hour=ARRIVAL_RATE_PER_HOUR,
            issue_type_weights=ISSUE_TYPE_WEIGHTS,
            rng=team_rng(SEED, team.key),
        )
    )
    env.run(until=RUN_HOURS)

    print(
        f"{team.name} ({team.key}) — intake only, no work started: "
        f"{SPRINTS_TO_RUN} sprints x {SPRINT_LENGTH_DAYS} days = {RUN_HOURS:.0f} working hours"
    )
    print()

    by_type = Counter(issue.issue_type for issue in collector.issues)
    print(f"Issues created: {len(collector.issues)}")
    for issue_type in ISSUE_TYPE_WEIGHTS:
        print(f"  {issue_type:<6} {by_type[issue_type]:>3}")
    print()

    workable = [issue for issue in collector.issues if issue.issue_type != EPIC]
    linked = [issue for issue in workable if issue.parent_id is not None]
    print(f"Epic linkage across {len(workable)} non-Epic issues:")
    print(f"  linked to an Epic  {len(linked):>3}")
    print(f"  standalone         {len(workable) - len(linked):>3}")
    print()

    backlog_points = sum(issue.story_points for issue in backlog)
    print(f"Backlog depth at cutoff: {len(backlog)} issues, {backlog_points} points")
    print(f"  (Epics are absent by design: {by_type[EPIC]} created, 0 queued)")
    print(f"  oldest first: {[issue.key for issue in list(backlog)[:8]]}")
    print()

    print(collector.summary())
