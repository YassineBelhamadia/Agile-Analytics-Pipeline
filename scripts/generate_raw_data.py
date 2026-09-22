"""Generates the simulator's ground truth and writes it to `raw_data/`.

This is the step that turns the simulator from something you run into
something the rest of the pipeline can consume. It runs the full
multi-team simulation and dumps the result to JSON at the repo root, so
the mock API has a fixed dataset to serve instead of re-simulating twelve
sprints on every start.

The output is generated, not source: it is reproducible from this script
alone (the run is seeded), so `raw_data/` is gitignored rather than
committed. Re-run this script whenever the simulator's behaviour or
calibration changes and the downstream dataset needs to catch up.

Usage:
    python3 scripts/generate_raw_data.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# The project isn't installed into the environment, and running a file
# inside scripts/ puts scripts/ on sys.path rather than the repo root, so
# `import simulator` would fail without this.
sys.path.insert(0, str(REPO_ROOT))

from simulator.multi_team import run_simulation  # noqa: E402
from simulator.persistence import ENTITY_SPECS, dump_to_json  # noqa: E402

OUTPUT_DIR = REPO_ROOT / "raw_data"
"""Where the dataset lands.

Anchored to the repo root rather than the working directory, so the
script writes to the same place no matter where it is invoked from.
"""


def main() -> None:
    """Run the simulation, write it to `raw_data/`, and report what landed."""
    collector = run_simulation()
    dump_to_json(collector, OUTPUT_DIR)

    print(f"Wrote ground truth to {OUTPUT_DIR}")
    print()
    print(f"{'file':<32} {'rows':>8} {'size':>12}")
    print("-" * 54)

    total_rows = 0
    total_bytes = 0
    for spec in ENTITY_SPECS:
        rows = len(getattr(collector, spec.attribute))
        size = (OUTPUT_DIR / spec.filename).stat().st_size
        total_rows += rows
        total_bytes += size
        print(f"{spec.filename:<32} {rows:>8} {size / 1024:>9.1f} KiB")

    print("-" * 54)
    print(f"{'total':<32} {total_rows:>8} {total_bytes / 1024:>9.1f} KiB")
    print()
    print("collector.summary():", collector.summary())


if __name__ == "__main__":
    main()
