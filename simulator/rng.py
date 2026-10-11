"""Per-team random streams.

Every random draw in the simulation belongs to exactly one team — its
arrivals, its sprint planning, its PTO, its issues' lifecycles — so every
draw comes from that team's own generator rather than from module-level
random state. Teams therefore cannot perturb each other: retuning one
team's parameters, or adding a fourth team, changes how many draws that
team makes without moving a single draw any other team sees.

A shared stream would couple them invisibly. With one global sequence,
an extra draw by Phoenix shifts every later draw by Atlas, so Atlas's
data changes even though nothing about Atlas did, and teams cannot be
calibrated independently.

There is no shared, non-team randomness in the simulator today. Should
some appear, give it its own stream via `stream` with a name that cannot
collide with a team key, rather than borrowing a team's generator.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import TypeVar

import numpy as np

T = TypeVar("T")


def stream(seed: int, name: str) -> np.random.Generator:
    """Return an independent generator derived from a base seed and a name.

    The name is hashed with SHA-256 rather than Python's `hash()`, which is
    salted per process and would make streams irreproducible. Seed and
    name hash are combined through numpy's SeedSequence, which is designed
    to turn related inputs into statistically independent streams.

    Args:
        seed: The run's base seed.
        name: What the stream is for — a team key, for team streams.

    Returns:
        A generator that depends only on (seed, name).
    """
    name_entropy = int.from_bytes(hashlib.sha256(name.encode()).digest()[:8], "big")
    return np.random.default_rng(np.random.SeedSequence([seed, name_entropy]))


def team_rng(seed: int, team_key: str) -> np.random.Generator:
    """Return the random stream for one team in a run seeded with `seed`."""
    return stream(seed, f"team:{team_key}")


def choice(rng: np.random.Generator, items: Sequence[T]) -> T:
    """Pick one element uniformly, returning the element itself.

    `rng.choice` would convert the sequence to a numpy array and hand back
    numpy scalars (np.str_ rather than str); indexing keeps the original
    Python objects.
    """
    return items[int(rng.integers(len(items)))]


def weighted_choice(rng: np.random.Generator, weights: Mapping[T, float]) -> T:
    """Pick one key with probability proportional to its weight.

    Args:
        rng: The stream to draw from.
        weights: Key to relative weight. Need not sum to 1.
    """
    keys = list(weights)
    values = np.array([weights[k] for k in keys], dtype=float)
    return keys[int(rng.choice(len(keys), p=values / values.sum()))]
