# City analysis plugin

This standalone Python project demonstrates street lengths, maintenance decisions, explicit block allocation, and canonical selection. It contains all required data. Local execution needs no server, database, credentials, or downloads beyond Python dependencies.

## Prepare and run

From the Lyra repository root:

```sh
cd examples/city-plugin
uv sync
uv run python -m city_example 1
uv run python -m city_example 6
uv run pytest
```

The SDK source in `pyproject.toml` points to `../../packages/lyra_sdk`. When copying this project elsewhere, select the SDK from your chosen Lyra checkout with `uv add /your_lyra_checkout_here/packages/lyra_sdk` and commit the regenerated lockfile. The package has no imports from the application or repository tests.

## Follow the tutorial

Follow [Build a city analysis plugin](../../docs/src/content/docs/plugins/tutorial/index.md) to write your own adapters and tests. The first three chapters create scripts in this directory. From chapter four, you build `city_example/tutorial.py`; chapter eight exports your factory from `city_example/__init__.py` and regenerates its manifest.

The calculation helpers, schemas, provider, and data are supplied. The completed handlers in `metrics.py` and `plugin.py` remain available for comparison. Each numbered command below runs a supplied reference checkpoint with an independent plugin definition:

| Chapter | Capability | Command |
| --- | --- | --- |
| 1 | Fixed-input length calculation | `uv run python -m city_example 1` |
| 2 | Exact street selections | `uv run python -m city_example 2` |
| 3 | Direct maintenance adapter and parameters | `uv run python -m city_example 3` |
| 4 | Explicit table dependencies | `uv run python -m city_example 4` |
| 5 | Derived streets and weighted block aggregation | `uv run python -m city_example 5` |
| 6 | Canonical block-to-street selection | `uv run python -m city_example 6` |
| 7 | Native input summaries and execution observations | `uv run python -m city_example 7` |

After completing chapter eight, `uv run lyra-plugin check-manifest` verifies the manifest generated from your exported factory. Your tutorial scripts and tests exercise your authored code; the numbered reference commands continue to use the supplied handlers.

## Understand the fixture

`city_example/data/city.json` contains synthetic projected geometry in EPSG:32614, condition scores, and explicit membership weights. The three streets have lengths 100 m, 200 m, and 300 m and scores 20, 80, and 40. Higher scores mean better condition. The default threshold 50 marks the first and third streets for maintenance; equality does not trigger maintenance.

`street-2` is shared equally between two blocks. A block summary multiplies lengths by those weights; `block-empty` returns a zero row. Canonical block selection returns the union of street rows, each once. Selection itself does not aggregate.

The street contract's `fixture_1` revision covers its geometry and condition observations. The block contract's revision covers block geometry and membership. Update declarations and data together when changing either covered dataset. The fixture is illustrative and defines no real city's maintenance policy.

## Use the completed plugin

The factory `city_example:create_plugin` registers the full graph and one location provider. Public metrics are `city_street_lengths`, `city_street_maintenance`, and `city_block_maintenance`. Native location arguments are `streets` or `blocks`; the collection is `example:city` and unit types are `street` and `block`.

The distribution declares every directly imported dependency and includes its dataset and generated manifest. Regenerate and inspect the manifest after changing contracts:

```sh
uv run lyra-plugin build-manifest
uv run lyra-plugin check-manifest
uv run lyra-plugin describe
```

The provider acquires package data during execution. Manifest generation requires no data acquisition. Database clients belong in a separately configured integration project with its own schema, seed data, and dependencies.
