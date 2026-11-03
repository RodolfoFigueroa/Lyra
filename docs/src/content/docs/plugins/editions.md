---
title: Register metric editions
description: Create named metric variants with shared structural and parameter contracts.
contentType: How-to
contentPlan: plugins/editions
sidebar: { label: "Register Metric Editions" }
---

Use editions to register variants of a metric under one `family` identifier. Create decorated handlers with ordinary Python factories, giving each handler a distinct `name` and each variant a named `edition`. Register every generated handler in `PluginDefinition`.

Bind dependencies to a concrete producer and its local output name. Factories can reuse output names without connecting their graphs; share a dependency by referencing the same producer and output. Declare column meanings and units in each output schema, and use `family` to associate metrics even when their schemas match.

Variants in a family must share these contracts:

- Named native input roles and location families
- Named output schemas
- The complete ordinary parameter contract, including names, types, defaults, and validation constraints

Parameter compatibility includes requiredness, nullability, bounds, and allowed values. Compilation rejects incompatible variants. Use separate families when their public parameter contracts differ.

A location family defines one structural schema for all its source contracts. It fixes column types, nullability, validation constraints, units, and ID and geometry conventions. Sources can differ in their values, boundaries, available IDs, and field definitions. A shared schema establishes neither geographic correspondence nor statistical comparability.

Use the `edition_metadata` JSON object to describe the data and methodology for each edition. Record these details as metadata:

- Source releases
- Observation periods
- Fixed methodological constants and their meanings

For development execution, use `plugin.editions.resolve(family, edition="your_edition_here")` to obtain the concrete node name. The compiled graph exposes the same resolver across enabled plugins.

Select a named edition even when you have installed only one; Lyra does not select the latest edition automatically. The resolver rejects unknown selections, and registration rejects duplicate editions within a family.

Omit `edition` for a metric with one implicit variant that requires no edition selector. Its family identifier defaults to its node name. Edition selection binds concrete sources and geographies separately from ordinary handler parameters. Do not repeat the edition inside each ID selection.

The [edition factory example](https://github.com/RodolfoFigueroa/lyra/blob/main/examples/location-plugin/location_plugin/capabilities.py) uses `score_edition` to register public `street_rating` variants and private producers. Those variants share schemas and calculation code; `edition_metadata` explains each variant’s fixed multiplier. [Run the example](https://github.com/RodolfoFigueroa/lyra/blob/main/examples/location-plugin/USAGE.md) for multiple named inputs, native returns, and separate provenance inspection.
