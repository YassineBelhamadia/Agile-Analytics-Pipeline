"""Maps SimPy's abstract simulation clock onto real calendar time.

SimPy drives the simulation on an abstract float clock — "hours of
simulated work elapsed since the run started" — because that's the unit
SimPy processes reason in. But every downstream consumer of this
project's data (STATUS_TRANSITION.timestamp, ISSUE.created/duedate/
resolutiondate, etc.) needs a real calendar datetime, because the whole
point of the analytics pipeline is to answer realistic time-based
questions: cycle time, time-in-status, sprint burndown by date.

If sim hours were mapped onto real time naively (i.e. just adding
`timedelta(hours=sim_hours)` to a start date), two things would be
dishonest about the resulting data:

1. Work would appear to happen at 3am, since a naive mapping doesn't
   know the difference between working hours and off-hours.
2. Weekends would silently count as "stuck" time in any time-in-status
   or cycle-time calculation, making teams look far slower than they
   really are.

This module is pure infrastructure with no simulation logic of its own:
it only defines the working-calendar rules (which hours count as work,
which days count as workdays) and the conversion function that respects
them. SimPy processes call `sim_to_real` to turn their internal clock
reading into a timestamp that could plausibly appear in a real Jira
changelog.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

SEED = 22
"""Base seed for the run that produces raw_data/.

Each team's random stream is derived from this seed and the team key
(see simulator/rng.py), so this one number reproduces the whole dataset.
It is not the calibration seed set: calibration is measured across
CALIBRATION_SEEDS in simulator/calibration.py, because sprint outcomes
vary widely by seed and no single run is a reliable measure.

Selected as a typical run, not drawn at random. Of seeds 1-100, 22 is
the closest to the 60-seed calibration averages among those where the
intended ordering Phoenix < Nomad < Atlas holds strictly: it gives 42% /
50% / 75% hit rates (56% overall) against averages of 43% / 53% / 73%
(56% overall), and its completed issue counts are also the closest to
average among the tied candidates (seeds 69 and 50 give the same hit
rates). The previous seed, 11, had drifted to an unrepresentative 50% /
42% / 58% under the current calibration, with Nomad below Phoenix.
Re-check this choice whenever the calibration changes.
"""

PROJECT_START = datetime(2024, 1, 8, 9, 0)
"""Simulation hour 0 — Monday 2024-01-08 at 09:00, the start of a workday."""

WORK_HOURS_PER_DAY = 8
"""Length of a simulated workday, in hours."""

WORK_START_HOUR = 9
"""Hour at which each simulated workday begins (workday runs 09:00-17:00)."""


def is_workday(d: date) -> bool:
    """Return whether `d` is a Monday-Friday workday.

    Args:
        d: The calendar date to check.

    Returns:
        True if `d` falls on Monday through Friday, False for Sat/Sun.
    """
    return d.weekday() < 5


def _advance_workdays(start: date, num_days: int) -> date:
    """Advance from `start` by `num_days` workdays, skipping weekends.

    Each step moves forward one calendar day at a time and keeps skipping
    ahead while it lands on a Saturday or Sunday, so a block of full
    working days never lands on, or hops over, a weekend incorrectly.

    Args:
        start: The date to advance from. Assumed to be a workday itself.
        num_days: How many workdays to advance by.

    Returns:
        The resulting date, guaranteed to be a workday.
    """
    current = start
    for _ in range(num_days):
        current += timedelta(days=1)
        while not is_workday(current):
            current += timedelta(days=1)
    return current


def sim_to_real(sim_hours: float) -> datetime:
    """Convert a SimPy working-hours clock reading into a real datetime.

    `sim_hours` counts only working hours (8 per workday, no nights, no
    weekends) elapsed since PROJECT_START. This function inverts that
    accounting back into a real calendar timestamp that always falls
    within a workday's 09:00-17:00 window, so no simulated event is ever
    stamped at 3am or on a Saturday/Sunday.

    Full 8-hour blocks each advance the calendar by exactly one workday
    (skipping weekends as they're encountered); any remaining fractional
    hours then advance the clock within that day's working window.

    Args:
        sim_hours: Working hours elapsed since simulation start (hour 0
            corresponds to PROJECT_START). Must be non-negative.

    Returns:
        The corresponding real datetime, always within a workday and
        within the 09:00-17:00 working window.

    Examples:
        sim_to_real(0) -> 2024-01-08 09:00 (Monday)
        sim_to_real(8) -> 2024-01-09 09:00 (Tuesday)
        sim_to_real(10) -> 2024-01-09 11:00 (Tuesday)
    """
    full_days, remainder_hours = divmod(sim_hours, WORK_HOURS_PER_DAY)
    target_date = _advance_workdays(PROJECT_START.date(), int(full_days))
    return datetime(
        target_date.year,
        target_date.month,
        target_date.day,
        WORK_START_HOUR,
    ) + timedelta(hours=remainder_hours)


if __name__ == "__main__":
    assert sim_to_real(0) == datetime(2024, 1, 8, 9, 0)
    assert sim_to_real(8) == datetime(2024, 1, 9, 9, 0)
    assert sim_to_real(10) == datetime(2024, 1, 9, 11, 0)

    # 5 full workdays (40 hours) from Monday 2024-01-08 crosses the
    # 2024-01-13/14 weekend and lands on the following Monday, not on a
    # Saturday.
    crossed = sim_to_real(40)
    assert crossed == datetime(2024, 1, 15, 9, 0)
    assert is_workday(crossed.date())

    # No result should ever land on a weekend or outside 09:00-17:00.
    for hours in [0, 1, 7.5, 8, 10, 16, 32, 33, 40, 41.5, 100]:
        result = sim_to_real(hours)
        assert is_workday(result.date()), f"{hours} hours landed on a weekend: {result}"
        assert 9 <= result.hour < 17 or (result.hour == 17 and result.minute == 0), (
            f"{hours} hours landed outside working hours: {result}"
        )

    print("All clock sanity checks passed.")
