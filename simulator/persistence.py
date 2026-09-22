"""Round-trips the simulator's ground truth to JSON on disk.

The simulator's output is expensive to produce and, until now, existed
only for as long as the process that generated it. This module gives it a
durable form, so the mock API can serve a fixed dataset instead of
re-running a twelve-sprint simulation on every start.

What lands on disk is the **internal ground truth shape** — the
dataclasses in teams.py, users.py, sprints.py, issues.py and capacity.py,
field for field. It is deliberately *not* Jira-shaped. Per the core
principle in docs/domain-model.md, there is one ground truth and the
Jira-shaped payloads are views projected from it on read; that projection
belongs in mock_api/ and nowhere else. Persisting Jira-shaped JSON here
would create a second copy of the truth in a foreign shape and defeat the
whole arrangement.

One file per entity type rather than one combined document: the files
stay small enough to open and read by eye, a single entity can be
inspected or diffed without wading through the rest, and the layout maps
cleanly onto the tables these entities will eventually become.

Serialization is driven by each dataclass's own type hints rather than by
per-entity conversion functions. Adding a field to an entity requires no
change here at all — the field is picked up automatically, and its
declared type decides how it is written and read back.
"""

from __future__ import annotations

import json
import types
import typing
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, TypeVar

from simulator.capacity import Capacity
from simulator.collector import DataCollector
from simulator.issues import Issue, StatusTransition
from simulator.sprints import Sprint, SprintIssueMembership
from simulator.teams import Team
from simulator.users import User

T = TypeVar("T")


@dataclass(frozen=True)
class EntitySpec:
    """Binds one DataCollector list to its dataclass and its file on disk.

    Attributes:
        attribute: Name of the list attribute on DataCollector.
        filename: File the list is written to, inside the target directory.
        cls: The dataclass each element of that list is an instance of.
    """

    attribute: str
    filename: str
    cls: type


ENTITY_SPECS: tuple[EntitySpec, ...] = (
    EntitySpec("teams", "teams.json", Team),
    EntitySpec("users", "users.json", User),
    EntitySpec("sprints", "sprints.json", Sprint),
    EntitySpec("issues", "issues.json", Issue),
    EntitySpec("status_transitions", "status_transitions.json", StatusTransition),
    EntitySpec("sprint_issue_memberships", "sprint_issue_memberships.json", SprintIssueMembership),
    EntitySpec("capacities", "capacities.json", Capacity),
)
"""Every entity the collector holds, in dependency order.

Ordered so that referenced entities precede the entities that reference
them — teams before users, issues before the transitions that point at
them. Nothing here enforces referential integrity on load, but writing
and reading in this order keeps the files legible to anyone tracing an ID
from one to the next.
"""


def _resolve_field_types(cls: type) -> dict[str, Any]:
    """Return each field's *resolved* type for a dataclass.

    `dataclasses.fields(cls)[i].type` cannot be used directly here. Every
    entity module opts into `from __future__ import annotations`, which
    makes annotations lazy: the `.type` attribute is the literal string
    `"datetime | None"`, not the type itself. `typing.get_type_hints`
    evaluates those strings against the defining module's namespace and
    hands back real objects.

    This resolves cleanly for all seven entities because each one imports
    the names its annotations mention (`date`, `datetime`) at module
    scope. If a future entity annotates a type that is only imported
    under `if TYPE_CHECKING:`, resolution here will raise NameError — the
    fix is to import that name at runtime in the entity's module, not to
    special-case it in this function.

    Args:
        cls: The dataclass to introspect.

    Returns:
        Field name to resolved type, for that dataclass's fields only.
    """
    hints = typing.get_type_hints(cls)
    return {f.name: hints[f.name] for f in fields(cls)}


def _unwrap_optional(hint: Any) -> Any:
    """Strip `| None` from a type hint, returning the underlying type.

    `str | None` in these modules is a `types.UnionType`, while a hint
    written as `typing.Optional[str]` would be a `typing.Union`. Both are
    handled, since the two spellings are interchangeable to a reader but
    not to `typing.get_origin`.

    Args:
        hint: A resolved type hint.

    Returns:
        The single non-None member of the union, or `hint` unchanged if
        it is not an optional union.
    """
    if typing.get_origin(hint) not in (types.UnionType, typing.Union):
        return hint
    non_none = [arg for arg in typing.get_args(hint) if arg is not type(None)]
    return non_none[0] if len(non_none) == 1 else hint


def _to_jsonable(value: Any, hint: Any) -> Any:
    """Convert one field value into something `json.dump` accepts.

    Dates and datetimes become ISO 8601 strings; everything else in these
    entities is already a JSON primitive and passes through untouched.

    Args:
        value: The field's value.
        hint: The field's resolved type hint, optional-ness included.

    Returns:
        A JSON-serializable representation of `value`.
    """
    if value is None:
        return None
    # Compared by identity rather than isinstance: datetime is a subclass
    # of date, so an isinstance check ordered date-first would quietly
    # serialize every timestamp as a bare day and lose the time of day.
    inner = _unwrap_optional(hint)
    if inner is datetime or inner is date:
        return value.isoformat()
    return value


def _from_jsonable(value: Any, hint: Any) -> Any:
    """Rebuild one field value from its JSON representation.

    The declared type decides which parser is used, which is what makes
    the round trip exact. Guessing from the string instead would be
    actively unsafe: `datetime.fromisoformat("2024-01-12")` happily
    returns a midnight datetime, so a `date` field would come back as the
    wrong type and compare unequal to what was written.

    Args:
        value: The raw value read from JSON.
        hint: The field's resolved type hint, optional-ness included.

    Returns:
        The value converted back to its declared type.
    """
    if value is None:
        return None
    inner = _unwrap_optional(hint)
    if inner is datetime:
        return datetime.fromisoformat(value)
    if inner is date:
        return date.fromisoformat(value)
    return value


def entity_to_dict(entity: Any) -> dict[str, Any]:
    """Convert one dataclass instance into a JSON-ready dict.

    Field order follows the dataclass definition, so the written file
    reads in the same order as the source.

    Args:
        entity: An instance of one of the entity dataclasses.

    Returns:
        A dict of field name to JSON-serializable value.
    """
    hints = _resolve_field_types(type(entity))
    return {f.name: _to_jsonable(getattr(entity, f.name), hints[f.name]) for f in fields(entity)}


def dict_to_entity(cls: type[T], payload: dict[str, Any]) -> T:
    """Rebuild one dataclass instance from a JSON dict.

    Args:
        cls: The dataclass to construct.
        payload: The dict read back from JSON.

    Returns:
        An instance of `cls` with every field converted to its declared
        type.
    """
    hints = _resolve_field_types(cls)
    return cls(**{name: _from_jsonable(payload[name], hint) for name, hint in hints.items()})


def dump_to_json(collector: DataCollector, output_dir: Path | str) -> None:
    """Write every entity list in `collector` to its own JSON file.

    Creates `output_dir` if it does not exist. All seven files are always
    written, including empty ones, so a dump directory is always a
    complete snapshot rather than a partial one — a missing file then
    unambiguously means something went wrong, not that an entity happened
    to have no rows.

    Args:
        collector: The ground truth to persist.
        output_dir: Directory to write the files into.
    """
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)

    for spec in ENTITY_SPECS:
        payload = [entity_to_dict(entity) for entity in getattr(collector, spec.attribute)]
        path = target / spec.filename
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")


def load_from_json(input_dir: Path | str) -> DataCollector:
    """Rebuild a DataCollector from a directory written by `dump_to_json`.

    The returned collector is equal, entity for entity, to the one that
    was dumped. It is also asked to resume its transition-ID counter past
    the highest ID present, so a reloaded collector can keep recording
    without colliding with IDs already on disk — without that, the first
    transition recorded after a load would reuse "TR-1". How the counter
    is derived is DataCollector's business, not this module's.

    Args:
        input_dir: Directory containing the seven entity files.

    Returns:
        A DataCollector holding the persisted ground truth.

    Raises:
        FileNotFoundError: If any of the seven entity files is missing.
    """
    source = Path(input_dir)
    collector = DataCollector()

    for spec in ENTITY_SPECS:
        with (source / spec.filename).open(encoding="utf-8") as handle:
            payload = json.load(handle)
        entities = [dict_to_entity(spec.cls, row) for row in payload]
        getattr(collector, spec.attribute).extend(entities)

    collector.resume_transition_counter()
    return collector


if __name__ == "__main__":
    import tempfile
    from collections import deque

    import simpy

    from simulator.clock import seed_all
    from simulator.multi_team import run_team
    from simulator.personalities import ALL_PERSONALITIES
    from simulator.sprint_cycle import BACKLOG_WARMUP_HOURS, SPRINT_LENGTH_HOURS

    NUM_SPRINTS = 12

    # Round-trip against real simulated output, not a hand-built fixture:
    # an empty or synthetic collector would never exercise None-valued
    # optional fields, microsecond timestamps, or date-vs-datetime.
    seed_all()
    env = simpy.Environment()
    original = DataCollector()
    for personality in ALL_PERSONALITIES:
        team = original.record_team(
            team_id=personality.team_id,
            key=personality.key,
            name=personality.name,
            base_headcount=personality.headcount,
        )
        author_ids = [
            original.record_user(
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
            collector=original,
            author_ids=author_ids,
            team=team,
            num_sprints=NUM_SPRINTS,
        )
    env.run(until=BACKLOG_WARMUP_HOURS + NUM_SPRINTS * SPRINT_LENGTH_HOURS)

    with tempfile.TemporaryDirectory() as tmpdir:
        dump_to_json(original, tmpdir)
        reloaded = load_from_json(tmpdir)

        print(f"Wrote {len(ENTITY_SPECS)} files to a temporary directory:")
        for spec in ENTITY_SPECS:
            size = (Path(tmpdir) / spec.filename).stat().st_size
            print(f"  {spec.filename:<32} {size / 1024:>8.1f} KiB")

        print()
        print("Sample row from issues.json (note the ISO timestamps and nulls):")
        with (Path(tmpdir) / "issues.json").open(encoding="utf-8") as handle:
            sample = next(
                row for row in json.load(handle) if row["resolutiondate"] is not None
            )
        print(json.dumps(sample, indent=2))

    print()
    print(f"{'entity':<30} {'original':>10} {'reloaded':>10} {'equal':>7}")
    print("-" * 60)
    all_equal = True
    for spec in ENTITY_SPECS:
        before = getattr(original, spec.attribute)
        after = getattr(reloaded, spec.attribute)
        equal = before == after
        all_equal &= equal
        print(f"{spec.attribute:<30} {len(before):>10} {len(after):>10} {str(equal):>7}")

    print()
    print("original.summary() :", original.summary())
    print("reloaded.summary() :", reloaded.summary())
    print()
    assert original.summary() == reloaded.summary(), "summary counts diverged"
    assert all_equal, "entity lists are not equal after round-trip"
    print("Round-trip exact: every entity equal, summaries match.")
