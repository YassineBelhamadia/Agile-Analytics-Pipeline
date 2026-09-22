"""Per-team tuning profiles — one object per team's whole character.

Every mechanic in the simulator is now parameterized rather than driven by
module constants, which means a team is fully described by the numbers fed
into those parameters. This module is where those numbers live.

Keeping them in one dataclass per team, rather than scattered across the
call sites that consume them, is the point. A team's character — fast and
sloppy, slow and dependable, chaotic — is a statement about how a dozen
numbers relate to each other, and that statement is only legible if the
numbers sit side by side. It also means adding a fourth team is a matter
of writing one more object, not of threading another set of arguments
through several call sites.

The three profiles below are deliberate narrative choices, picked so that
cross-team analytics have something real to find: the interesting question
downstream is not "which team is fastest" but "why does the fast team miss
sprints the slow team hits", and that only has an answer if the teams
differ along several axes at once.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TeamPersonality:
    """The complete tuning profile for one simulated team.

    Every field maps to a parameter of `arrivals_process`,
    `sprint_cycle_process` or `issue_lifecycle`. Frozen because a team's
    character is fixed for the length of a run — nothing in the
    simulation should be mutating these mid-flight.

    Attributes:
        team_id: Primary key for the Team record this profile creates.
        key: Short project-style key, prefixing the team's issue keys.
        name: Human-readable team name.
        headcount: Developers on the team, and the capacity of its own
            resource pool. Teams do not share developers.
        hours_per_point: Working hours one story point nominally
            represents for this team. Lower means the team estimates
            more aggressively, not that its people are faster — the
            distinction matters, because an aggressive estimate is what
            produces a missed sprint.
        qa_bounce_probability: Chance QA rejects a ticket on each pass
            and sends it back for rework.
        arrival_rate_per_hour: Mean new issues filed per working hour.
        issue_type_weights: The team's normal Story/Task/Bug/Epic mix.
        average_pto_days_per_sprint: The team's *planning assumption*
            about absence, used only to size commitments — not the
            absence that actually occurs, which is drawn from the three
            PTO fields below. A team whose assumption sits below its real
            distribution will be repeatedly surprised.
        optimism_factor_range: Bounds on the per-sprint over-commitment
            multiplier. A wide range means inconsistent planning; a high
            one means habitual over-promising.
        capacity_realization_factor: Fraction of nominal point-capacity
            the team commits to. Despite the name this is a *planning
            aggressiveness* dial, not an execution-quality one: it scales
            the commitment target, so a higher value means the team signs
            up for more and strains harder, and a lower value means it
            commits conservatively and clears what it took on.
        high_pto_probability: Chance any given sprint lands in the team's
            heavy-absence regime rather than its normal one.
        low_pto_range: Day bounds on absence in a normal sprint.
        high_pto_range: Day bounds on absence in a heavy-absence sprint.
    """

    team_id: str
    key: str
    name: str
    headcount: int
    hours_per_point: float
    qa_bounce_probability: float
    arrival_rate_per_hour: float
    issue_type_weights: dict[str, float] = field(default_factory=dict)
    average_pto_days_per_sprint: float = 1.5
    optimism_factor_range: tuple[float, float] = (1.05, 1.15)
    capacity_realization_factor: float = 0.35
    high_pto_probability: float = 0.2
    low_pto_range: tuple[float, float] = (0.0, 2.0)
    high_pto_range: tuple[float, float] = (4.0, 6.0)


PHOENIX = TeamPersonality(
    team_id="T-PHOENIX",
    key="PHX",
    name="Phoenix",
    headcount=5,
    hours_per_point=3.5,
    qa_bounce_probability=0.22,
    arrival_rate_per_hour=0.24,
    issue_type_weights={"Story": 0.42, "Task": 0.23, "Bug": 0.28, "Epic": 0.07},
    average_pto_days_per_sprint=1.2,
    optimism_factor_range=(1.10, 1.20),
    capacity_realization_factor=0.42,
    high_pto_probability=0.15,
    low_pto_range=(0.0, 2.0),
    high_pto_range=(3.0, 5.0),
)
"""Fast but messy — the biggest team, shipping hardest, paying for it in rework.

Phoenix estimates aggressively (3.5 hours a point, well under the others)
and commits well above what its capacity justifies (10-20% over). It has
the most people and the heaviest intake, so on raw throughput it should
look like the strongest team in the organisation.

The cost shows up everywhere else. Its QA bounce rate is the highest at
22%, so a meaningful share of its work is done twice; its bug share is
elevated at 28%, the signature of shipping fast against a codebase that
pushes back; and it carries the *highest* capacity realization factor of
the three, which on this dial means it signs up for the largest share of
its nominal capacity and leaves itself no slack for the rework it
reliably generates. Absence is unremarkable — a 15% chance of a heavy
sprint, and even then only 3-5 days.

The interesting question this team poses to the analytics is whether its
velocity advantage survives contact with its defect rate. It should
deliver the most and still miss the most.
"""

ATLAS = TeamPersonality(
    team_id="T-ATLAS",
    key="ATL",
    name="Atlas",
    headcount=3,
    hours_per_point=6.0,
    qa_bounce_probability=0.08,
    arrival_rate_per_hour=0.10,
    issue_type_weights={"Story": 0.49, "Task": 0.28, "Bug": 0.15, "Epic": 0.08},
    average_pto_days_per_sprint=1.0,
    optimism_factor_range=(1.00, 1.08),
    capacity_realization_factor=0.28,
    high_pto_probability=0.10,
    low_pto_range=(0.0, 1.5),
    high_pto_range=(3.0, 4.0),
)
"""Slow but reliable — the smallest team, and the one that means what it says.

Atlas estimates conservatively (6.0 hours a point) and commits to
roughly what it can actually do, with an optimism range that barely
leaves 1.0. It is the smallest team with the lightest intake, so in
absolute terms it will always deliver the fewest points.

What it buys with that caution is predictability. Its QA bounce rate is
the lowest at 8%, its bug share is the lowest at 15%, and it takes the
*lowest* capacity realization factor of the three — on this dial that
means it deliberately commits to a small share of its nominal capacity
and then clears it. Its absence is both low and rare: a 10% chance of a
heavy sprint capped at four days, and a normal range that tops out at
1.5 days. Crucially its planning assumption of 1.0 days sits right on
top of what actually happens, so it is almost never surprised.

It should hit its commitments far more often than Phoenix while
delivering less — the central tension the cross-team view exists to make
visible.
"""

NOMAD = TeamPersonality(
    team_id="T-NOMAD",
    key="NMD",
    name="Nomad",
    headcount=4,
    hours_per_point=5.0,
    qa_bounce_probability=0.15,
    arrival_rate_per_hour=0.15,
    issue_type_weights={"Story": 0.45, "Task": 0.25, "Bug": 0.22, "Epic": 0.08},
    average_pto_days_per_sprint=3.0,
    optimism_factor_range=(1.08, 1.25),
    capacity_realization_factor=0.35,
    high_pto_probability=0.40,
    low_pto_range=(0.5, 3.0),
    high_pto_range=(5.0, 9.0),
)
"""Chaotic and absence-prone — average on paper, erratic in practice.

On every steady-state dial Nomad is the middle team, and deliberately so:
four developers, 5.0 hours a point, a 15% bounce rate and the baseline
issue mix. Anyone reading its averages would call it unremarkable.

Its character is entirely in the variance, and absence is where it lives.
Two sprints in five land in its heavy regime — four times as often as
Atlas — and a heavy sprint costs it five to nine days, nearly a whole
sprint of one person. Even its normal sprints start at half a day rather
than zero. It plans around 3.0 days, double what the others assume, and
still tends to under-estimate what actually happens.

Its optimism range compounds this: the widest and highest of the three at
1.08-1.25, so it does not over-commit consistently so much as
unpredictably, promising far too much one sprint and about the right
amount the next. The point is not that Nomad is the worst team — on
averages it is squarely the middle one. The point is that its average is
the midpoint of two very different kinds of sprint, and the analytics
should be able to tell that apart from genuine consistency.
"""

ALL_PERSONALITIES = (PHOENIX, ATLAS, NOMAD)
"""Every team in the standard multi-team run, in display order."""
