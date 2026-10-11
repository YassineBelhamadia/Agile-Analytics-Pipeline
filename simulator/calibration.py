"""Measures the simulator's calibration across a fixed set of seeds.

A single seeded run is one draw from the simulation's distribution, and
sprint outcomes vary widely from seed to seed — Atlas's hit rate alone
can swing by tens of points. Calibration targets are therefore stated as
averages over many seeds, and they are only comparable from one tuning
pass to the next if every pass uses the *same* seeds. This module pins
that seed set and computes the per-team figures the targets are written
against.

Hit rates come from `compute_sprint_report`, so calibration is always
measured with the same exact-timestamp rules the mock API's payloads
imply (see "Where 'commit vs. completion vs. scope-added' actually comes
from" in docs/domain-model.md).

Usage:
    python -m simulator.calibration
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass

from simulator.lifecycle import DONE
from simulator.multi_team import DEFAULT_NUM_SPRINTS, run_simulation
from simulator.personalities import ALL_PERSONALITIES, TeamPersonality
from simulator.sprint_report import compute_sprint_report

CALIBRATION_SEEDS: tuple[int, ...] = tuple(range(1, 61))
"""The seeds every calibration pass is tuned on: 1 through 60.

Fixed so that tuning results are comparable over time. Changing this set
invalidates every previously reported calibration figure, so treat it as
part of the calibration targets rather than as a convenience default.
Sixty rather than twenty because a 20-seed average moved by roughly three
points from one seed set to the next — as wide as half a target band.
"""

HOLDOUT_SEEDS: tuple[int, ...] = tuple(range(61, 101))
"""Seeds 61 through 100, never used for tuning.

Reported alongside the calibration figures as a check on whether the
tuned parameters describe the simulation or merely fit the particular
seeds they were tuned on. Tuning against these would defeat the purpose.
"""


@dataclass(frozen=True)
class SeedResult:
    """One team's outcome in one seeded run.

    Attributes:
        hit_rate: Fraction of the team's sprints that were hit.
        completed_issues: Team issues in Done at the end of the run.
    """

    hit_rate: float
    completed_issues: int


@dataclass(frozen=True)
class TeamCalibration:
    """One team's figures across every calibration seed.

    Attributes:
        name: Team name.
        capacity_realization_factor: The crf the figures were measured at.
        by_seed: Seed to that seed's result.
    """

    name: str
    capacity_realization_factor: float
    by_seed: dict[int, SeedResult]

    @property
    def mean_hit_rate(self) -> float:
        return statistics.mean(r.hit_rate for r in self.by_seed.values())

    @property
    def hit_rate_range(self) -> tuple[float, float]:
        rates = [r.hit_rate for r in self.by_seed.values()]
        return min(rates), max(rates)

    @property
    def mean_completed_issues(self) -> float:
        return statistics.mean(r.completed_issues for r in self.by_seed.values())


def calibrate(
    personalities: Sequence[TeamPersonality] = ALL_PERSONALITIES,
    seeds: Sequence[int] = CALIBRATION_SEEDS,
    num_sprints: int = DEFAULT_NUM_SPRINTS,
) -> list[TeamCalibration]:
    """Run the multi-team simulation once per seed and collect per-team figures.

    All teams run together in each simulation, exactly as in a real data
    generation run. Each team draws from its own random stream (see
    simulator/rng.py), so a team's figures depend only on its own
    parameters: retuning one team leaves the others' numbers unchanged,
    and a team measured alone gets the same figures as in the full run.

    Args:
        personalities: The team profiles to measure.
        seeds: Seeds to run. Defaults to CALIBRATION_SEEDS.
        num_sprints: Sprints each team runs per simulation.

    Returns:
        One TeamCalibration per personality, in the order given.
    """
    by_team: dict[str, dict[int, SeedResult]] = {p.team_id: {} for p in personalities}
    for seed in seeds:
        collector = run_simulation(num_sprints, seed=seed, personalities=personalities)
        for personality in personalities:
            sprints = [s for s in collector.sprints if s.team_id == personality.team_id]
            hits = sum(compute_sprint_report(s, collector)["hit"] for s in sprints)
            done = sum(
                1
                for issue in collector.issues
                if issue.team_id == personality.team_id and issue.status == DONE
            )
            by_team[personality.team_id][seed] = SeedResult(hits / len(sprints), done)

    return [
        TeamCalibration(p.name, p.capacity_realization_factor, by_team[p.team_id])
        for p in personalities
    ]


def ordering_holds(results: Sequence[TeamCalibration], seed: int) -> bool:
    """Whether hit rates are strictly increasing in `results` order for one seed."""
    rates = [team.by_seed[seed].hit_rate for team in results]
    return all(a < b for a, b in zip(rates, rates[1:]))


def print_report(label: str, seeds: Sequence[int]) -> None:
    """Print the per-team calibration table for one seed set."""
    # Phoenix < Nomad < Atlas is the intended ordering of hit rates.
    by_name = {team.name: team for team in calibrate(seeds=seeds)}
    ordered = [by_name["Phoenix"], by_name["Nomad"], by_name["Atlas"]]

    print(f"{label}: seeds {seeds[0]}-{seeds[-1]}")
    print()
    print(f"{'team':<10} {'crf':>6} {'hit rate':>10} {'range':>11} {'completed':>11}")
    print("-" * 52)
    for team in ordered:
        low, high = team.hit_rate_range
        print(
            f"{team.name:<10} {team.capacity_realization_factor:>6.2f} "
            f"{team.mean_hit_rate:>9.1%} {low:>5.0%}-{high:<4.0%} "
            f"{team.mean_completed_issues:>11.1f}"
        )
    print()
    overall = statistics.mean(team.mean_hit_rate for team in ordered)
    held = sum(ordering_holds(ordered, seed) for seed in seeds)
    print(f"overall mean hit rate: {overall:.1%}")
    print(f"Phoenix < Nomad < Atlas held strictly in {held}/{len(seeds)} seeds")


if __name__ == "__main__":
    print_report("Calibration", CALIBRATION_SEEDS)
    print()
    print_report("Held out", HOLDOUT_SEEDS)
