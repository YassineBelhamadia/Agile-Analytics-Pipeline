"""SimPy process modelling one issue's journey through its status workflow.

This is the first behavioural layer in the simulator: everything below it
(clock.py, collector.py, the dataclasses) is passive infrastructure, and
this module is what actually decides *when* things happen. It models one
issue's path across the board; many such processes run concurrently and
compete for a shared pool of developers.

The workflow modelled is the common Jira-style board:

    To Do -> In Progress -> Code Review -> QA -> Done

with the realistic wrinkle that QA sometimes rejects work and sends it
back to In Progress.

Every tunable number lives in a module-level constant rather than inline,
because these values are the knobs that determine whether the generated
dataset looks like a plausible engineering team or like noise; they will
be tuned once real analytics run on top of the output.
"""

from __future__ import annotations

import random
from collections.abc import Generator, Sequence

import numpy as np
import simpy

from simulator.clock import sim_to_real
from simulator.collector import DataCollector
from simulator.issues import Issue

TODO = "To Do"
IN_PROGRESS = "In Progress"
CODE_REVIEW = "Code Review"
QA = "QA"
DONE = "Done"

HOURS_PER_POINT = 5.0
"""Default working hours one story point nominally represents.

This is the conversion that turns an abstract estimate into simulated
time. It is the *estimate*, not the outcome — actual time spent is
sampled around it, so a team's velocity can diverge from its estimates
the way a real team's does.

This is a **team-level tunable**, not a simulation-wide constant: how
many hours a point really means is a property of how a particular team
estimates, and teams differ (see personalities.py). The value here is the
baseline default, used by this module's own demo. Every other caller
passes its own value explicitly.
"""

IN_PROGRESS_LOGNORMAL_SIGMA = 0.5
"""Spread of the log-normal In Progress duration, in log-space.

Larger values mean a fatter tail of overruns. 0.5 gives a distribution
where most tickets land within roughly half-to-double their estimate,
with occasional severe overruns.
"""

TODO_MIN_HOURS = 1.0
TODO_MAX_HOURS = 6.0
"""Bounds on how long a ticket waits in To Do before someone picks it up."""

CODE_REVIEW_MIN_HOURS = 1.0
CODE_REVIEW_MAX_HOURS = 6.0
"""Bounds on how long a ticket waits in Code Review."""

QA_MIN_HOURS = 1.0
QA_MAX_HOURS = 8.0
"""Bounds on how long a ticket spends being tested in QA."""

QA_BOUNCE_PROBABILITY = 0.15
"""Default chance that QA rejects a ticket and sends it back to In Progress.

Like HOURS_PER_POINT, this is a **team-level tunable** rather than a
simulation-wide constant — a team's bounce rate reflects its code quality
and review rigour, which is exactly the kind of thing that differs
between teams (see personalities.py). The value here is the baseline
default, used by this module's own demo.
"""

MAX_QA_BOUNCES = 2
"""Hard cap on how many times one issue may bounce back from QA.

Without a cap, an unlucky tail case could bounce indefinitely and the
process would never terminate. Real tickets do occasionally ping-pong
several times, but a bounded number keeps every simulation run finite and
reproducible while still producing the rework signal the analytics care
about.
"""


def _sample_todo_hours() -> float:
    """Sample how long an issue sits in To Do before being picked up.

    A flat range is deliberate: pickup latency is not what this block is
    trying to model faithfully, and a uniform draw avoids implying more
    precision than intended.
    """
    return random.uniform(TODO_MIN_HOURS, TODO_MAX_HOURS)


def _sample_in_progress_hours(story_points: int, hours_per_point: float) -> float:
    """Sample actual hours of active work for an issue of `story_points`.

    The log-normal distribution is the key modelling choice here. Real
    task durations are right-skewed: they cannot be negative, most land
    near the estimate, and the ones that go wrong go *badly* wrong — a
    ticket can take three times its estimate, but never negative three
    times. A symmetric (normal) distribution would get all three of those
    properties wrong, and a uniform one would make a 3x overrun exactly as
    likely as an on-time finish.

    The distribution is parameterised so its median is exactly
    `story_points * hours_per_point`: sampling `lognormal(mu, sigma)` with
    `mu = ln(base_hours)` yields a median of `base_hours`, so half of all
    tickets come in under estimate and half over, with the mean pulled
    above the estimate by the overrun tail. That mean-above-median gap is
    what makes a simulated team systematically finish slightly less than
    it commits to, which is the realistic behaviour being reproduced.

    Args:
        story_points: The issue's point estimate. Must not be None —
            Epics carry no points (their points are a rollup of children,
            per the domain model) and are not worked through this process.
        hours_per_point: The owning team's hours-per-point conversion.
            Passed in rather than read from the module constant, since
            teams estimate differently.

    Returns:
        Hours of active work, always positive.
    """
    base_hours = story_points * hours_per_point
    return float(np.random.lognormal(mean=np.log(base_hours), sigma=IN_PROGRESS_LOGNORMAL_SIGMA))


def _sample_code_review_hours() -> float:
    """Sample how long an issue waits in Code Review.

    Shorter than In Progress and flat, since review latency is dominated
    by a reviewer's availability rather than by the size of the change.
    """
    return random.uniform(CODE_REVIEW_MIN_HOURS, CODE_REVIEW_MAX_HOURS)


def _sample_qa_hours() -> float:
    """Sample how long an issue spends in QA before a verdict is reached."""
    return random.uniform(QA_MIN_HOURS, QA_MAX_HOURS)


def _move(
    env: simpy.Environment,
    issue: Issue,
    collector: DataCollector,
    from_status: str,
    to_status: str,
    author_ids: Sequence[str],
) -> None:
    """Record one status change and update the issue's current status.

    Writes a StatusTransition row stamped with the real calendar time
    corresponding to the current simulation clock, then updates
    `issue.status`. Both happen together because `Issue.status` is a
    denormalized mirror of the most recent transition (per the domain
    model) — letting them drift apart would make the ground truth
    internally inconsistent.

    Args:
        env: The SimPy environment, read for its current clock value.
        issue: The issue being moved.
        collector: Where the resulting transition row is accumulated.
        from_status: Status the issue is leaving.
        to_status: Status the issue is entering.
        author_ids: Candidate users who could have performed this
            transition; one is chosen at random.
    """
    collector.record_transition(
        issue_id=issue.issue_id,
        from_status=from_status,
        to_status=to_status,
        timestamp=sim_to_real(env.now),
        author_id=random.choice(author_ids),
    )
    issue.status = to_status


def issue_lifecycle(
    env: simpy.Environment,
    issue: Issue,
    collector: DataCollector,
    dev_resource: simpy.Resource,
    author_ids: Sequence[str],
    hours_per_point: float,
    qa_bounce_probability: float,
) -> Generator[simpy.Event, None, None]:
    """Drive one issue from To Do through to Done.

    Time is always charged to the status the issue is *leaving*, never the
    one it is entering: the process waits for the duration of the current
    status and only then records the transition out of it. This is what
    makes time-in-status arithmetic honest downstream — the gap between
    two consecutive transition timestamps is exactly the time spent in the
    status named by the earlier transition's `to_status`. Charging time on
    entry instead would shift every measurement by one status and make
    "Done" look like it had a duration, which it does not.

    After QA, a Bernoulli trial decides whether the work is accepted or
    bounced back to In Progress for rework. The bounce loop is what
    produces realistic rework signal — a ticket that visits In Progress
    three times and takes far longer in total than its estimate suggests —
    which is precisely the pattern that makes cycle-time analytics
    interesting. It is capped at MAX_QA_BOUNCES so no run can loop
    forever on an unlucky streak.

    Only the In Progress phase consumes a developer from `dev_resource`.
    To Do, Code Review and QA are left unconstrained in this block — a
    deliberate scope decision, not an assertion that review and testing
    are free. Modelling reviewer and QA pools as their own resources is a
    later refinement; for now the single scarce resource is developer
    time, which is the bottleneck that dominates real cycle times.

    Because the In Progress transition is recorded before the developer
    is acquired, an issue that arrives when every developer is busy waits
    *in* In Progress rather than in To Do. That is deliberate and matches
    how real boards behave — a ticket moved to In Progress by a busy
    assignee accumulates In Progress time whether or not anyone is
    actively typing — and it is precisely the signal that makes WIP and
    queueing show up in cycle-time analytics.

    This function must never be called with an Epic. Epics carry no
    `story_points` (theirs is a rollup of their children, per the domain
    model) and represent a body of work that is not itself coded,
    reviewed or tested — only their child Stories, Tasks and Bugs move
    through this state machine. Enforcing that is the caller's
    responsibility (see arrivals.py); the assert below is a fail-fast
    guard, not the enforcement point.

    Args:
        env: The SimPy environment driving the simulation clock.
        issue: The issue to move. Assumed to currently be in To Do, and to
            carry a non-None `story_points` (Epics are rollups and are not
            worked through this process). Its `status` and
            `resolutiondate` are mutated in place as the process runs.
        collector: Accumulator receiving each StatusTransition.
        dev_resource: Pool of developers. One slot is held for the
            duration of each In Progress phase, including rework after a
            QA bounce.
        author_ids: Candidate users who may perform the transitions; one
            is chosen at random per transition.
        hours_per_point: The owning team's hours-per-point conversion.
        qa_bounce_probability: The owning team's chance of a QA rejection
            on each pass. Both are per-team tunables rather than module
            constants, so several teams with different characters can run
            against the same code in one simulation.

    Yields:
        SimPy timeout and resource-request events as the issue advances.
    """
    assert issue.story_points is not None, (
        f"issue_lifecycle called with unpointed issue {issue.key} "
        f"(issue_type={issue.issue_type!r}); Epics have no story_points and "
        f"must not be run through the lifecycle state machine"
    )

    yield env.timeout(_sample_todo_hours())
    _move(env, issue, collector, TODO, IN_PROGRESS, author_ids)

    bounces = 0
    while True:
        with dev_resource.request() as req:
            yield req
            yield env.timeout(_sample_in_progress_hours(issue.story_points, hours_per_point))
        _move(env, issue, collector, IN_PROGRESS, CODE_REVIEW, author_ids)

        yield env.timeout(_sample_code_review_hours())
        _move(env, issue, collector, CODE_REVIEW, QA, author_ids)

        yield env.timeout(_sample_qa_hours())
        if bounces < MAX_QA_BOUNCES and random.random() < qa_bounce_probability:
            bounces += 1
            _move(env, issue, collector, QA, IN_PROGRESS, author_ids)
            continue

        _move(env, issue, collector, QA, DONE, author_ids)
        break

    issue.resolutiondate = sim_to_real(env.now)


if __name__ == "__main__":
    from simulator.clock import PROJECT_START, seed_all

    seed_all()

    env = simpy.Environment()
    collector = DataCollector()

    issue = collector.record_issue(
        issue_id="I-1",
        key="AAP-1",
        issue_type="Story",
        team_id="T-1",
        parent_id=None,
        assignee_id="U-1",
        reporter_id="U-2",
        story_points=5,
        status=TODO,
        created=PROJECT_START,
        duedate=None,
        resolutiondate=None,
    )

    # A single issue in isolation can never queue, so capacity is 1 here:
    # contention is exercised from arrivals.py, not from this demo.
    dev_resource = simpy.Resource(env, capacity=1)

    env.process(
        issue_lifecycle(
            env,
            issue,
            collector,
            dev_resource,
            author_ids=["U-1", "U-2", "U-3"],
            hours_per_point=HOURS_PER_POINT,
            qa_bounce_probability=QA_BOUNCE_PROBABILITY,
        )
    )
    env.run()

    print(f"{issue.key} ({issue.issue_type}, {issue.story_points} points)")
    print(f"  created: {issue.created}")
    print()
    for transition in collector.status_transitions:
        print(
            f"  {transition.transition_id:<6} "
            f"{transition.timestamp:%Y-%m-%d %H:%M} "
            f"{transition.from_status:>12} -> {transition.to_status:<12} "
            f"by {transition.author_id}"
        )
    print()
    print(f"  resolved: {issue.resolutiondate}")
    print(f"  final status: {issue.status}")
    print()
    print(collector.summary())
