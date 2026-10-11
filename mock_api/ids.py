"""Deterministic Jira identifiers derived from ground-truth IDs.

Jira identifies most things by opaque numeric IDs (issue `id`, project
`id`, sprint `id`) and identifies users by an opaque `accountId`. The
ground truth uses readable string IDs instead ("ATL-I-1", "PHX-U-2").
Each function here maps one ground-truth ID to its Jira counterpart.

Every mapping is a pure function of the ground-truth ID alone — a hash,
never a counter or a random draw. The same entity therefore gets the
same Jira ID on every load and in every process, and an entity's ID does
not shift when other entities are added to or removed from the dataset.
The cost of hashing is a small chance of collision, which
`assert_unique` exists to catch.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable

_NUMERIC_ID_FLOOR = 10_000_000
_NUMERIC_ID_SPAN = 90_000_000
"""Numeric IDs land in [10_000_000, 100_000_000): always eight digits."""


def _digest(namespace: str, ground_truth_id: str) -> str:
    """Hash a ground-truth ID within a namespace, as a hex string.

    The namespace keeps, say, a team and a board derived from the same
    team_id from hashing to the same value.

    Args:
        namespace: What kind of Jira ID is being derived.
        ground_truth_id: The simulator's ID for the entity.

    Returns:
        The SHA-256 hex digest of the namespaced ID.
    """
    return hashlib.sha256(f"{namespace}:{ground_truth_id}".encode()).hexdigest()


def _numeric(namespace: str, ground_truth_id: str) -> int:
    return _NUMERIC_ID_FLOOR + int(_digest(namespace, ground_truth_id), 16) % _NUMERIC_ID_SPAN


def account_id(user_id: str) -> str:
    """Return the Jira `accountId` for a user.

    Twenty-four hex characters, the same length as the classic Atlassian
    account IDs in Jira's documentation examples.

    Args:
        user_id: The ground-truth user ID.

    Returns:
        The user's accountId.
    """
    return _digest("user", user_id)[:24]


def issue_id(ground_truth_issue_id: str) -> str:
    """Return the Jira issue `id`, a numeric string."""
    return str(_numeric("issue", ground_truth_issue_id))


def project_id(team_id: str) -> str:
    """Return the Jira project `id` for a team, a numeric string."""
    return str(_numeric("project", team_id))


def board_id(team_id: str) -> int:
    """Return the agile board ID for a team, an integer.

    The ground truth has no board entity. Each team runs exactly one
    Scrum board in this model, so the board is derived from the team.
    """
    return _numeric("board", team_id)


def sprint_id(ground_truth_sprint_id: str) -> int:
    """Return the agile sprint `id`, an integer as the agile API returns it."""
    return _numeric("sprint", ground_truth_sprint_id)


def changelog_id(history_key: str) -> str:
    """Return the changelog history `id`, a numeric string.

    Args:
        history_key: A transition_id for a status history, or the result
            of `membership_changelog_key` for a sprint history.
    """
    return str(_numeric("changelog", history_key))


def membership_changelog_key(ground_truth_issue_id: str, ground_truth_sprint_id: str) -> str:
    """Return the key a sprint membership's changelog history is derived from.

    A membership has no ID of its own; the (issue, sprint) pair is its
    identity. The prefix keeps the key out of the transition_id space.
    """
    return f"membership:{ground_truth_issue_id}:{ground_truth_sprint_id}"


def assert_unique(kind: str, ground_truth_ids: Iterable[str], derive: Callable[[str], object]) -> None:
    """Raise if two ground-truth IDs derive the same Jira ID.

    Args:
        kind: Label for the error message.
        ground_truth_ids: Every ground-truth ID of one entity type.
        derive: The derivation function for that type.

    Raises:
        ValueError: On the first collision found.
    """
    seen: dict[object, str] = {}
    for gt_id in ground_truth_ids:
        derived = derive(gt_id)
        if derived in seen:
            raise ValueError(
                f"{kind} ID collision: {seen[derived]!r} and {gt_id!r} both map to {derived!r}"
            )
        seen[derived] = gt_id
