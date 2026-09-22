"""Runs several teams concurrently in one simulation.

Everything the simulator does for a single team already exists; this
module is the wiring that runs three of them side by side. Each team gets
its own developer pool and its own backlog — teams share nothing
operationally, because a real organisation's teams don't lend each other
developers mid-sprint or pull from each other's queues.

What they *do* share is the Environment and the DataCollector. The shared
clock means the three teams' timelines line up, so a sprint boundary
means the same calendar date for everyone. The shared collector means all
three teams' issues, sprints and transitions land in one ground truth,
which is what makes cross-team questions answerable at all: comparing
Phoenix's velocity to Atlas's is only meaningful if both were measured on
the same clock and stored in the same shape.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

import simpy

from simulator.arrivals import arrivals_process
from simulator.collector import DataCollector
from simulator.issues import Issue
from simulator.personalities import TeamPersonality
from simulator.sprint_cycle import sprint_cycle_process
from simulator.teams import Team

if TYPE_CHECKING:
    from collections.abc import Sequence


def run_team(
    env: simpy.Environment,
    personality: TeamPersonality,
    dev_resource: simpy.Resource,
    backlog: deque[Issue],
    collector: DataCollector,
    author_ids: Sequence[str],
    team: Team,
    num_sprints: int,
) -> None:
    """Start one team's arrivals and sprint cycle inside a shared environment.

    Wires a `TeamPersonality` into the two long-running processes that
    make up a team: intake, which files new issues into the backlog, and
    the sprint cycle, which plans, staffs and works them. This is what
    sprint_cycle.py's demo does for one hardcoded team, generalized so
    every tunable comes from the personality rather than a module
    constant.

    The two processes communicate only through `backlog` and a
    `weights_ref` dict created here, both private to this team. Nothing
    about them reaches another team.

    Args:
        env: The shared SimPy environment.
        personality: The team's complete tuning profile.
        dev_resource: This team's own developer pool, sized to the
            personality's headcount by the caller.
        backlog: This team's own FIFO queue of unstarted work.
        collector: The shared ground-truth accumulator.
        author_ids: This team's users, drawn on for assignees, reporters
            and transition authors.
        team: The already-recorded Team row these processes attach to.
        num_sprints: How many sprints this team runs.
    """
    # Populated by arrivals_process at time zero, from the weights below.
    weights_ref: dict[str, dict[str, float]] = {}

    env.process(
        arrivals_process(
            env,
            team,
            backlog,
            collector,
            author_ids,
            weights_ref,
            arrival_rate_per_hour=personality.arrival_rate_per_hour,
            issue_type_weights=personality.issue_type_weights,
        )
    )
    env.process(
        sprint_cycle_process(
            env,
            team,
            dev_resource,
            backlog,
            collector,
            author_ids,
            weights_ref,
            num_sprints=num_sprints,
            headcount=personality.headcount,
            average_pto_days_per_sprint=personality.average_pto_days_per_sprint,
            optimism_factor_range=personality.optimism_factor_range,
            capacity_realization_factor=personality.capacity_realization_factor,
            hours_per_point=personality.hours_per_point,
            qa_bounce_probability=personality.qa_bounce_probability,
            high_pto_probability=personality.high_pto_probability,
            low_pto_range=personality.low_pto_range,
            high_pto_range=personality.high_pto_range,
        )
    )


if __name__ == "__main__":
    import statistics

    from simulator.clock import seed_all
    from simulator.lifecycle import DONE
    from simulator.personalities import ALL_PERSONALITIES
    from simulator.sprint_cycle import BACKLOG_WARMUP_HOURS, SPRINT_LENGTH_HOURS
    from simulator.sprint_report import compute_sprint_report

    NUM_SPRINTS = 12

    seed_all()

    env = simpy.Environment()
    collector = DataCollector()

    for personality in ALL_PERSONALITIES:
        team = collector.record_team(
            team_id=personality.team_id,
            key=personality.key,
            name=personality.name,
            base_headcount=personality.headcount,
        )
        author_ids = [
            collector.record_user(
                user_id=f"{personality.key}-U-{n}",
                display_name=f"{personality.name} Dev {n}",
                team_id=team.team_id,
            ).user_id
            for n in range(1, personality.headcount + 1)
        ]
        run_team(
            env,
            personality,
            dev_resource=simpy.Resource(env, capacity=personality.headcount),
            backlog=deque(),
            collector=collector,
            author_ids=author_ids,
            team=team,
            num_sprints=NUM_SPRINTS,
        )

    env.run(until=BACKLOG_WARMUP_HOURS + NUM_SPRINTS * SPRINT_LENGTH_HOURS)

    print(f"Cross-team comparison — {NUM_SPRINTS} sprints, {len(ALL_PERSONALITIES)} teams")
    print()
    print(
        f"{'team':<10} {'devs':>5} {'hit rate':>10} {'completion':>12} "
        f"{'bug ratio':>11} {'completed':>11}"
    )
    print("-" * 62)

    for personality in ALL_PERSONALITIES:
        sprints = [s for s in collector.sprints if s.team_id == personality.team_id]
        reports = [compute_sprint_report(s, collector) for s in sprints]

        hit_rate = sum(r["hit"] for r in reports) / len(reports)
        completion = statistics.mean(
            r["completed"] / r["committed"] for r in reports if r["committed"]
        )
        bug_ratio = statistics.mean(
            r["bug_count"] / r["total_count"] for r in reports if r["total_count"]
        )
        completed_issues = sum(
            1
            for i in collector.issues
            if i.team_id == personality.team_id and i.status == DONE
        )

        print(
            f"{personality.name:<10} {personality.headcount:>5} {hit_rate:>9.0%} "
            f"{completion:>11.0%} {bug_ratio:>10.0%} {completed_issues:>11}"
        )

    print()
    print(collector.summary())
