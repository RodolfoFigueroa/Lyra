"""Run a tutorial checkpoint from the example project's environment."""

import argparse
import json
import sys
from dataclasses import asdict

from lyra.sdk import LocalRunner
from lyra.sdk.results import NativeOutputs

from city_example.checkpoints import checkpoint


def run_chapter(chapter: int) -> NativeOutputs:
    """Execute the chapter's demonstrated request.

    Returns:
        Native validated tables for the checkpoint.
    """
    runner = LocalRunner.from_definitions({"city": checkpoint(chapter)})
    if chapter == 1:
        return runner.run("city_street_lengths", {})
    if chapter == 5:
        return runner.run(
            "city_block_maintenance",
            {"blocks": selection("block", "block-1", "block-2", "block-empty")},
        )
    unit, ids = (
        ("street", ["street-3", "street-1"])
        if chapter < 6
        else ("block", ["block-1", "block-2"])
    )
    metric_name = "city_street_lengths" if chapter == 2 else "city_street_maintenance"
    tables = runner.run(metric_name, {"streets": selection(unit, *ids)})
    if chapter == 7:
        provenance = runner.inspect()
        if provenance is not None:
            observations = {
                "native_inputs": [
                    asdict(value) for value in provenance.execution.native_inputs
                ],
                "observations": [
                    asdict(value) for value in provenance.execution.observations
                ],
                "succeeded": provenance.execution.succeeded,
            }
            sys.stderr.write(json.dumps(observations, indent=2) + "\n")
    return tables


def selection(unit: str, *ids: str) -> dict[str, object]:
    """Return an ID selection for the example's collection."""
    return {"collection": "example:city", "unit_type": unit, "ids": list(ids)}


def main() -> None:
    """Print named table records for one runnable tutorial chapter."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("chapter", type=int, nargs="?", default=6, choices=range(1, 8))
    tables = run_chapter(parser.parse_args().chapter)
    sys.stdout.write(
        json.dumps(
            {name: table.to_dict("records") for name, table in tables.items()}, indent=2
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
