"""SimPy process generating a stream of new issues for one team.

lifecycle.py models what happens to an issue once it exists. This module
models where issues come from: a continuous, irregular trickle of new
work arriving at a team that has only so many developers to absorb it.
Putting the two together is what produces the interesting behaviour —
when arrivals outpace capacity, a backlog forms and cycle times stretch,
which is exactly the effect the analytics pipeline downstream is meant to
detect.

This module also introduces the Epic/Story hierarchy into the generated
data for the first time. Epics arrive through the same stream as other
work but are treated as a different kind of thing entirely: they are
containers that later arrivals attach themselves to, not units of work
that move across the board.
"""

from __future__ import annotations

import random
from collections.abc import Generator, Sequence

import numpy as np
import simpy

from simulator.clock import sim_to_real
from simulator.collector import DataCollector
from simulator.lifecycle import TODO, issue_lifecycle
from simulator.teams import Team

EPIC = "Epic"
STORY = "Story"
TASK = "Task"
BUG = "Bug"

ARRIVAL_RATE_PER_HOUR = 0.15
"""Mean number of new issues arriving per working hour.

Chosen relative to what a small team can actually absorb. Over two
ten-day sprints (160 working hours) this yields roughly 24 arrivals,
while a three-developer team working issues of ~4 average story points
can finish somewhere near 18 of them in the same window. Arrivals
therefore run modestly ahead of throughput, so a backlog forms and
queueing becomes visible without the simulation seizing up entirely —
the realistic middle ground between an idle team and total gridlock.
"""

ISSUE_TYPE_WEIGHTS: dict[str, float] = {
    STORY: 0.45,
    TASK: 0.25,
    BUG: 0.22,
    EPIC: 0.08,
}
"""Relative frequency of each issue type among arrivals.

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


def _sample_interarrival_hours() -> float:
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
    """
    return float(np.random.exponential(1.0 / ARRIVAL_RATE_PER_HOUR))


def _sample_issue_type() -> str:
    """Pick an issue type according to ISSUE_TYPE_WEIGHTS."""
    return random.choices(
        list(ISSUE_TYPE_WEIGHTS),
        weights=list(ISSUE_TYPE_WEIGHTS.values()),
    )[0]


def _sample_story_points() -> int:
    """Pick a story point estimate according to STORY_POINT_WEIGHTS."""
    return random.choices(
        list(STORY_POINT_WEIGHTS),
        weights=list(STORY_POINT_WEIGHTS.values()),
    )[0]


def _pick_parent_epic(open_epics: Sequence[str]) -> str | None:
    """Decide which Epic, if any, a new issue belongs to.

    Args:
        open_epics: issue_ids of Epics created so far in this run.

    Returns:
        An epic's issue_id, or None if no epic exists yet or the roll
        against EPIC_LINK_PROBABILITY came up short.
    """
    if not open_epics:
        return None
    if random.random() >= EPIC_LINK_PROBABILITY:
        return None
    return random.choice(open_epics)


def arrivals_process(
    env: simpy.Environment,
    team: Team,
    dev_resource: simpy.Resource,
    collector: DataCollector,
    author_ids: Sequence[str],
) -> Generator[simpy.Event, None, None]:
    """Generate new issues for `team` for as long as the simulation runs.

    Loops forever, waiting an exponentially distributed gap between
    arrivals and creating one issue each time. The process has no notion
    of when the simulation ends; the caller bounds it with
    `env.run(until=...)`, which simply stops the clock mid-wait.

    Epics are created but never started as lifecycle processes. This is
    the important modelling decision in this module, and it is a
    statement about what an Epic *is* rather than a shortcut. An Epic is
    not a smaller or simpler unit of work — it is a container for other
    issues. It has no story points of its own (its points are the rollup
    of its children, per the domain model), so there is nothing to drive
    an In Progress duration; and "code review" or "QA" have no meaning
    applied to a body of work rather than a change. Giving Epics a
    trivial or no-op lifecycle would be worse than excluding them: it
    would emit status transitions that never happen in real Jira data and
    would corrupt any cycle-time or throughput metric that reads the
    transition table without filtering on issue type.

    Args:
        env: The SimPy environment driving the simulation clock.
        team: The team the generated issues belong to. Its `key` prefixes
            the generated issue keys.
        dev_resource: Developer pool passed through to each issue's
            lifecycle process.
        collector: Accumulator receiving every issue created here.
        author_ids: Candidate users to draw assignees, reporters and
            transition authors from.

    Yields:
        SimPy timeout events, one per gap between arrivals.
    """
    open_epics: list[str] = []
    issue_number = 0

    while True:
        yield env.timeout(_sample_interarrival_hours())

        issue_number += 1
        issue_type = _sample_issue_type()
        is_epic = issue_type == EPIC

        issue = collector.record_issue(
            issue_id=f"I-{issue_number}",
            key=f"{team.key}-{issue_number}",
            issue_type=issue_type,
            team_id=team.team_id,
            # Epics are top-level in v1: no epic-inside-an-epic nesting.
            parent_id=None if is_epic else _pick_parent_epic(open_epics),
            assignee_id=random.choice(author_ids),
            reporter_id=random.choice(author_ids),
            story_points=None if is_epic else _sample_story_points(),
            status=TODO,
            created=sim_to_real(env.now),
            duedate=None,
            resolutiondate=None,
        )

        if is_epic:
            # No lifecycle process: an Epic has no story_points to drive an
            # In Progress duration, and code review and QA are meaningless
            # for a container of work. It exists only to be linked against.
            open_epics.append(issue.issue_id)
        else:
            env.process(issue_lifecycle(env, issue, collector, dev_resource, author_ids))


if __name__ == "__main__":
    from collections import Counter

    from simulator.clock import WORK_HOURS_PER_DAY, seed_all
    from simulator.lifecycle import CODE_REVIEW, DONE, HOURS_PER_POINT, IN_PROGRESS

    DEMO_HEADCOUNT = 3
    SPRINT_LENGTH_DAYS = 10
    SPRINTS_TO_RUN = 2
    RUN_HOURS = SPRINTS_TO_RUN * SPRINT_LENGTH_DAYS * WORK_HOURS_PER_DAY

    seed_all()

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

    dev_resource = simpy.Resource(env, capacity=DEMO_HEADCOUNT)
    env.process(arrivals_process(env, team, dev_resource, collector, author_ids))
    env.run(until=RUN_HOURS)

    print(
        f"{team.name} ({team.key}) — {DEMO_HEADCOUNT} devs, "
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

    done = [issue for issue in workable if issue.status == DONE]
    print(f"Transitions recorded: {len(collector.status_transitions)}")
    print(f"Completed by cutoff:  {len(done)} of {len(workable)} (rest still in flight)")
    print()

    transitions_by_issue: dict[str, list] = {}
    for transition in collector.status_transitions:
        transitions_by_issue.setdefault(transition.issue_id, []).append(transition)

    # An issue leaves To Do and enters In Progress in the same instant, so
    # any wait for a free developer lands inside the In Progress span. Where
    # that span runs well past the estimate, the excess is queueing.
    print("Queueing: In Progress span vs. estimated work")
    print(f"{'key':<8} {'pts':>3}  {'entered In Progress':<20} {'left In Progress':<20} {'est':>7}")
    shown = 0
    for issue in workable:
        history = transitions_by_issue.get(issue.issue_id, [])
        entered = next((t for t in history if t.to_status == IN_PROGRESS), None)
        left = next((t for t in history if t.to_status == CODE_REVIEW), None)
        if entered is None or left is None:
            continue
        print(
            f"{issue.key:<8} {issue.story_points:>3}  "
            f"{entered.timestamp:%Y-%m-%d %H:%M}     "
            f"{left.timestamp:%Y-%m-%d %H:%M}     "
            f"{issue.story_points * HOURS_PER_POINT:>5.0f}h"
        )
        shown += 1
        if shown == 6:
            break
    print()

    print(collector.summary())
