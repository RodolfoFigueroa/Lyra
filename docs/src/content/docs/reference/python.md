---
title: Find a Python API
description: Choose public Python entry points by development task.
contentType: Landing
contentPlan: reference/python
sidebar: { label: "Find A Python API" }
---

Use this index to find the public objects used by guides. Generated module pages provide exact signatures, types, and docstrings. The complete [module browser](../../api/lyra/) remains available for deeper inspection.

| Task | Public objects | Generated reference |
| --- | --- | --- |
| Declare and register calculations | `metric`, `PluginDefinition` | [Plugin contracts](../../api/lyra/sdk/plugin/) |
| Define parameter models | `MetricParameters` | [Parameters](../../api/lyra/sdk/parameters/) |
| Prepare resolved handler inputs and validate results | `BoundPlugin` | [Runtime invocation](../../api/lyra/runtime/invocation/) |
| Run locally and inspect provenance | `LocalRunner` | [Local execution](../../api/lyra/runtime/local/) |
| Declare source identity | `LocationFamily`, `LocationContract` | [Location contracts](../../api/lyra/contracts/locations/) |
| Work with resolved locations | `Locations`, `LocationOutputSchema` | [Locations](../../api/lyra/data/locations/), [location schemas](../../api/lyra/sdk/location_schema/), and [value rules](../locations/) |
| Bind table and location dependencies | `Dependency`, `LocationDependency`, `Input` | [Dependencies](../../api/lyra/sdk/dependencies/) |
| Acquire data | `BaseIO`, `Resource` | [Resources](../../api/lyra/sdk/resources/) |
| Declare resource configuration | `IOConfig`, `ResourceConfig`, `SecretReference` | [Resource contracts](../../api/lyra/contracts/resources/) |
| Read standalone location artifacts | `read_location_artifact`, `LocationArtifact` | [Artifact reader](../../api/lyra/data/artifact_reader/) |
| Describe a job submission | `JobCreateRequest` | [Job contracts](../../api/lyra/contracts/jobs/) |
| Submit and observe server jobs | `LyraClient`, `AsyncLyraClient` | [Synchronous client](../../api/lyra/api/client/sync/) and [asynchronous client](../../api/lyra/api/client/async_/) |

When defining a metric, check the [rules for parameters and handler signatures](../parameters/) and the [supported units for output columns](../units/).
