"""Sprint planning, scope creep and PTO, driving one shared developer pool.

This is where the simulation stops being a queue of independent tickets
and starts behaving like a team working in sprints. Three forces meet
here, and the design goal throughout is that they interact *causally*
rather than being scripted:

- **Planning** decides how much work to commit to, based on an average
  view of capacity that ignores what this particular sprint actually
  looks like.
- **PTO** removes real capacity from the same sprint, in an amount
  planning never saw.
- **Scope creep** pulls extra work in mid-sprint, after commitment.

No sprint is ever told to miss. A sprint misses when an optimistic
commitment meets an unlucky PTO draw, a cluster of large tickets, or a
bug incident — which is exactly how real sprints miss. The resulting
dataset therefore contains a genuine, explainable relationship between
capacity and delivery, which is the relationship the analytics pipeline
exists to surface.

Everything runs against a single `simpy.Resource` created once for the
whole simulation and never resized; see `pto_process` for why that works.
"""

from __future__ import annotations

import random
from collections import deque
from collections.abc import Generator, Sequence

import numpy as np
import simpy

from simulator.arrivals import BUG, ISSUE_TYPE_WEIGHTS
from simulator.clock import WORK_HOURS_PER_DAY, sim_to_real
from simulator.collector import DataCollector
from simulator.issues import Issue
from simulator.lifecycle import HOURS_PER_POINT, issue_lifecycle
from simulator.sprints import Sprint
from simulator.teams import Team

HEADCOUNT = 4
"""Developers on the team, and the fixed capacity of the shared resource.

Small enough that one person's absence is a visible fraction of capacity
(a quarter), which is what makes PTO matter to sprint outcomes rather
than washing out in the averages.
"""

SPRINT_LENGTH_HOURS = WORK_HOURS_PER_DAY * 10
"""Length of a sprint in working hours: ten working days, a two-week sprint.

Derived from clock.py's workday length rather than hardcoded, so the
working calendar has exactly one definition across the simulator.
"""

BACKLOG_WARMUP_HOURS = SPRINT_LENGTH_HOURS
"""How long arrivals accumulate before the first sprint is planned.

Real teams do not start sprint one against an empty backlog — they start
against a pile of work that predates the project. Waiting one sprint's
worth of arrivals before planning anything simulates that pre-existing
backlog. Without it, sprint one would commit to whatever handful of
tickets happened to exist in the first minutes of the run and would be
unrepresentative of every sprint that follows.
"""

AVERAGE_PTO_DAYS_PER_SPRINT = 1.5
"""Typical PTO days per sprint, used *only* to size the commitment.

This is the team's planning assumption, not the PTO that actually
happens: the real draw comes from `_sample_pto_days` and is usually
different. That gap is deliberate and is the central mechanism of this
module. A team plans against its normal capacity because at planning time
it does not know who will be sick, which holiday will land mid-sprint, or
whose leave got approved late. Sizing the commitment against each
sprint's true capacity instead would produce a team that hits every
sprint, which would be both unrealistic and analytically worthless —
there would be nothing for a capacity-versus-velocity analysis to find.
"""

LOW_PTO_RANGE = (0.0, 2.0)
"""Normal-sprint PTO range in days: the odd day off, nothing coordinated."""

HIGH_PTO_RANGE = (4.0, 6.0)
"""Heavy-absence PTO range in days: a holiday cluster or extended leave."""

HIGH_PTO_SPRINT_PROBABILITY = 0.2
"""Chance a sprint draws from HIGH_PTO_RANGE instead of LOW_PTO_RANGE.

Roughly one sprint in five. Absence is not smoothly distributed in real
teams — it clumps around public holidays, school breaks and planned
leave. A single unimodal distribution would smear those clumps into a
gentle wobble; a two-regime draw keeps the occasional genuinely
short-staffed sprint that makes the capacity signal legible.
"""

CAPACITY_REALIZATION_FACTOR = 0.35
"""Fraction of nominal point-capacity the team can actually commit to.

Raw capacity math — developer hours divided by HOURS_PER_POINT — badly
over-predicts what a sprint can deliver, because HOURS_PER_POINT is the
*estimate* for a point and several things systematically push the real
cost above it:

- log-normal overruns, which pull the mean above the estimate (~1.13x)
- QA bounces sending work back for rework (~1.18x)
- mid-sprint scope creep consuming capacity that was never committed
- carryover: a sprint opens with unfinished work from the last one still
  holding developers

Those terms alone multiply out to roughly 1.7x. The realization factor is
lower than 1/1.7 because they do not merely multiply — they feed back.
Every point of unfinished work lengthens the queue for developers, which
lengthens cycle times, which increases what carries into the next sprint,
which lengthens the queue again. Calibration is therefore empirical,
measured against the simulation's own steady-state throughput, rather
than derived from the list above.

Committing without this factor is exactly the failure this module would
otherwise exhibit: the team commits ~80% beyond what it can deliver, work
in progress grows without bound, and after a few sprints nothing finishes
inside its own sprint at all. Real teams avoid that trap by planning from
*historical velocity* rather than from headcount arithmetic — which is
precisely what this factor encodes. OPTIMISM_FACTOR_RANGE then remains
what it is meant to be: the team's small deliberate over-reach on top of
a realistic baseline, rather than a rounding error on top of a wildly
optimistic one.
"""

OPTIMISM_FACTOR_RANGE = (1.05, 1.15)
"""Multiplier applied to baseline capacity when committing, sampled per sprint.

Teams reliably commit to slightly more than they can deliver. The
planning fallacy is one of the most robust findings in software
estimation, and a simulated team that committed to exactly its capacity
would look nothing like a real one. Five to fifteen percent over is a
mild, believable amount — enough to make the team's margin thin so that
an unlucky sprint tips into a miss, without guaranteeing failure.
"""

BUG_INCIDENT_SPRINT_PROBABILITY = 1 / 6
"""Chance a sprint is a bug-incident sprint, roughly one in six.

Represents a production incident, a bad release, or a quality problem
surfacing — an external shock the team did not plan for, arriving through
the intake stream rather than through planning.
"""

INCIDENT_BUG_SHARE = 0.40
"""Bug share of arrivals during an incident sprint, up from the usual 22%."""

INCIDENT_ISSUE_TYPE_WEIGHTS: dict[str, float] = {
    issue_type: (
        INCIDENT_BUG_SHARE
        if issue_type == BUG
        else weight * (1.0 - INCIDENT_BUG_SHARE) / (1.0 - ISSUE_TYPE_WEIGHTS[BUG])
    )
    for issue_type, weight in ISSUE_TYPE_WEIGHTS.items()
}
"""Issue type mix during a bug-incident sprint.

Derived from the base weights rather than written out, so the two stay in
sync if the base mix is retuned. Bugs rise to INCIDENT_BUG_SHARE and
every other type is scaled down proportionally, preserving their
relative proportions and keeping the total at 1.0.
"""

SCOPE_ADD_POISSON_MEAN = 2.0
"""Mean number of issues pulled into a sprint after it has been committed.

A Poisson draw is the natural fit: scope adds are a count of independent
events occurring over a fixed interval, so the distribution is integer
valued by construction, cannot go negative, and is right-skewed for free
— most sprints take on a couple of extra tickets and the occasional
sprint takes on six. Hand-built weights would have to approximate that
shape and would need retuning whenever the mean changed.
"""


def _sample_pto_days() -> float:
    """Draw this sprint's actual PTO days from one of two regimes.

    Returns:
        Days of PTO, from HIGH_PTO_RANGE with probability
        HIGH_PTO_SPRINT_PROBABILITY, otherwise from LOW_PTO_RANGE.
    """
    if random.random() < HIGH_PTO_SPRINT_PROBABILITY:
        return random.uniform(*HIGH_PTO_RANGE)
    return random.uniform(*LOW_PTO_RANGE)


def pto_process(
    env: simpy.Environment,
    dev_resource: simpy.Resource,
    pto_hours: float,
    offset_hours: float,
) -> Generator[simpy.Event, None, None]:
    """Hold one developer slot idle to represent someone being away.

    PTO *occupies* capacity rather than removing it. The absent developer
    takes a slot in the pool and does nothing with it for the duration,
    so the remaining developers contend for what is left.

    Modelling it this way means the simulation needs exactly one Resource
    object for its entire lifetime, never resized. That matters more than
    it might appear. SimPy resources cannot be resized safely while
    requests are queued against them, and issues routinely straddle sprint
    boundaries — a ticket that entered In Progress late in one sprint is
    still holding or waiting for a developer well into the next.
    Swapping in a smaller resource at each boundary would orphan those
    pending requests and quietly break the continuity of work in flight.
    Occupying a slot instead leaves every in-flight issue competing for
    the same real pool, exactly as it would in a real team where someone
    going on leave does not reset anyone else's work.

    The absence starts at a random point inside the sprint rather than on
    the boundary, because real leave rarely aligns with sprint dates, and
    an absence that always began on day one would be far easier for the
    team to absorb than a mid-sprint one.

    Args:
        env: The SimPy environment driving the simulation clock.
        dev_resource: The shared developer pool.
        pto_hours: How long the slot is held idle.
        offset_hours: Delay before the absence begins.

    Yields:
        A timeout for the offset, a resource request, and a timeout for
        the absence itself.
    """
    yield env.timeout(offset_hours)
    with dev_resource.request() as req:
        yield req
        yield env.timeout(pto_hours)


def scope_add_issue_process(
    env: simpy.Environment,
    offset_hours: float,
    backlog: deque[Issue],
    sprint: Sprint,
    dev_resource: simpy.Resource,
    collector: DataCollector,
    author_ids: Sequence[str],
) -> Generator[simpy.Event, None, None]:
    """Pull one extra issue into an already-committed sprint.

    Waits until some point inside the sprint and then takes the oldest
    waiting issue. The membership it records carries an `added_at` after
    the sprint's start date, which is precisely what lets the sprint
    report separate scope-added points from committed points (see
    sprint_report.py) — the same distinction Jira's own Sprint Report
    draws by comparing sprint contents at start versus end.

    If the backlog is empty the scope add simply does not happen. That is
    a normal outcome, not an error: there is nothing to pull in, so the
    sprint keeps the scope it committed to.

    Args:
        env: The SimPy environment driving the simulation clock.
        offset_hours: How far into the sprint the pull happens.
        backlog: Shared FIFO queue of unstarted work.
        sprint: The sprint the issue is being added to.
        dev_resource: The shared developer pool, passed to the lifecycle.
        collector: Accumulator receiving the membership row.
        author_ids: Candidate users for the issue's transitions.

    Yields:
        A timeout for the offset.
    """
    yield env.timeout(offset_hours)

    if not backlog:
        return

    issue = backlog.popleft()
    collector.record_sprint_membership(
        issue_id=issue.issue_id,
        sprint_id=sprint.sprint_id,
        added_at=sim_to_real(env.now),
    )
    env.process(issue_lifecycle(env, issue, collector, dev_resource, author_ids))


def sprint_cycle_process(
    env: simpy.Environment,
    team: Team,
    dev_resource: simpy.Resource,
    backlog: deque[Issue],
    collector: DataCollector,
    author_ids: Sequence[str],
    weights_ref: dict[str, dict[str, float]],
    num_sprints: int,
) -> Generator[simpy.Event, None, None]:
    """Run `num_sprints` consecutive sprints for one team.

    Each sprint: draw the PTO that will actually happen, record the
    Sprint and its Capacity, schedule the absence, decide whether this is
    a bug-incident sprint, size a commitment, pull that much work from
    the backlog and start it, then schedule a few mid-sprint scope adds
    and advance to the next boundary.

    The commitment is sized against *average* capacity
    (AVERAGE_PTO_DAYS_PER_SPRINT), never against the PTO actually drawn
    for this sprint, then scaled by CAPACITY_REALIZATION_FACTOR and
    inflated by a sampled optimism factor. The team is therefore always
    committing slightly beyond a realistic baseline — and in a sprint
    that draws heavy absence, it is committing to work that cannot fit.
    Nothing in this function decides whether a sprint hits or misses;
    that falls out of how the commitment, the absence, the issue sizes
    and the arrival mix happen to land.

    Args:
        env: The SimPy environment driving the simulation clock.
        team: The team running these sprints.
        dev_resource: The shared developer pool, created once by the
            caller and never resized.
        backlog: Shared FIFO queue of unstarted work, filled by
            arrivals_process.
        collector: Accumulator receiving sprints, capacities and
            memberships.
        author_ids: Candidate users for assignees and transition authors.
        weights_ref: Single-key dict whose "current" entry this process
            swaps to steer the arrival mix during incident sprints.
        num_sprints: How many sprints to run.

    Yields:
        The warmup timeout, then one sprint-length timeout per sprint.
    """
    yield env.timeout(BACKLOG_WARMUP_HOURS)

    for sprint_number in range(1, num_sprints + 1):
        pto_days = _sample_pto_days()
        sprint_start = sim_to_real(env.now)

        sprint = collector.record_sprint(
            sprint_id=f"S-{sprint_number}",
            team_id=team.team_id,
            name=f"Sprint {sprint_number}",
            state="closed",
            start_date=sprint_start.date(),
            # Advance the sim clock first, then convert: adding 80 calendar
            # hours to a datetime would land three days out, not ten
            # working days.
            end_date=sim_to_real(env.now + SPRINT_LENGTH_HOURS).date(),
        )
        collector.record_capacity(
            team_id=team.team_id,
            sprint_id=sprint.sprint_id,
            headcount=HEADCOUNT,
            pto_days=pto_days,
        )

        if pto_days > 0:
            env.process(
                pto_process(
                    env,
                    dev_resource,
                    pto_hours=pto_days * WORK_HOURS_PER_DAY,
                    offset_hours=random.uniform(0, SPRINT_LENGTH_HOURS),
                )
            )

        is_incident = random.random() < BUG_INCIDENT_SPRINT_PROBABILITY
        weights_ref["current"] = INCIDENT_ISSUE_TYPE_WEIGHTS if is_incident else ISSUE_TYPE_WEIGHTS

        optimism = random.uniform(*OPTIMISM_FACTOR_RANGE)
        baseline_available_hours = (
            HEADCOUNT * SPRINT_LENGTH_HOURS - AVERAGE_PTO_DAYS_PER_SPRINT * WORK_HOURS_PER_DAY
        )
        committed_target_points = (
            baseline_available_hours / HOURS_PER_POINT * CAPACITY_REALIZATION_FACTOR * optimism
        )

        # Greedy FIFO planning: keep pulling the oldest waiting issue until
        # the target is reached. The issue that crosses the threshold is
        # taken whole — a team commits to tickets, not to fractions of one.
        committed_points = 0.0
        while backlog and committed_points < committed_target_points:
            issue = backlog.popleft()
            collector.record_sprint_membership(
                issue_id=issue.issue_id,
                sprint_id=sprint.sprint_id,
                added_at=sprint_start,
            )
            env.process(issue_lifecycle(env, issue, collector, dev_resource, author_ids))
            committed_points += issue.story_points

        for _ in range(int(np.random.poisson(SCOPE_ADD_POISSON_MEAN))):
            env.process(
                scope_add_issue_process(
                    env,
                    offset_hours=random.uniform(0, SPRINT_LENGTH_HOURS),
                    backlog=backlog,
                    sprint=sprint,
                    dev_resource=dev_resource,
                    collector=collector,
                    author_ids=author_ids,
                )
            )

        yield env.timeout(SPRINT_LENGTH_HOURS)


if __name__ == "__main__":
    from simulator.arrivals import arrivals_process
    from simulator.clock import seed_all
    from simulator.sprint_report import compute_sprint_report

    NUM_SPRINTS = 12

    seed_all()

    env = simpy.Environment()
    collector = DataCollector()

    team = collector.record_team(
        team_id="T-1",
        key="AAP",
        name="Platform",
        base_headcount=HEADCOUNT,
    )
    for n in range(1, HEADCOUNT + 1):
        collector.record_user(user_id=f"U-{n}", display_name=f"Dev {n}", team_id=team.team_id)
    author_ids = [user.user_id for user in collector.users]

    backlog: deque[Issue] = deque()
    weights_ref: dict[str, dict[str, float]] = {"current": ISSUE_TYPE_WEIGHTS}

    # One pool for the whole run. PTO occupies slots in it; it is never
    # resized, so work in flight across a sprint boundary is unaffected.
    dev_resource = simpy.Resource(env, capacity=HEADCOUNT)

    env.process(arrivals_process(env, team, backlog, collector, author_ids, weights_ref))
    env.process(
        sprint_cycle_process(
            env,
            team,
            dev_resource,
            backlog,
            collector,
            author_ids,
            weights_ref,
            num_sprints=NUM_SPRINTS,
        )
    )
    env.run(until=BACKLOG_WARMUP_HOURS + NUM_SPRINTS * SPRINT_LENGTH_HOURS)

    print(f"{team.name} ({team.key}) — {HEADCOUNT} devs, {NUM_SPRINTS} sprints")
    print()
    print(
        f"{'sprint':<10} {'commit':>7} {'scope+':>7} {'done':>6} "
        f"{'result':>7} {'completion':>11} {'bugs':>6} {'pto':>6}"
    )

    capacity_by_sprint = {cap.sprint_id: cap for cap in collector.capacities}
    hits = 0
    for sprint in collector.sprints:
        report = compute_sprint_report(sprint, collector)
        committed = report["committed"]
        completion = f"{report['completed'] / committed:.0%}" if committed else "n/a"
        bug_ratio = (
            f"{report['bug_count'] / report['total_count']:.0%}" if report["total_count"] else "n/a"
        )
        hits += report["hit"]
        print(
            f"{report['name']:<10} {committed:>7} {report['scope_added']:>7} "
            f"{report['completed']:>6} {'HIT' if report['hit'] else 'MISS':>7} "
            f"{completion:>11} {bug_ratio:>6} "
            f"{capacity_by_sprint[sprint.sprint_id].pto_days:>5.1f}d"
        )

    print()
    print(f"Hit rate: {hits}/{len(collector.sprints)} sprints ({hits / len(collector.sprints):.0%})")
    print(f"Backlog remaining at cutoff: {len(backlog)} issues")
    print()
    print(collector.summary())
