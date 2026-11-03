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
| Run locally and inspect provenance | `LocalRunner` | [Local execution](../../api/lyra/sdk/local/) |
| Declare source identity | `LocationFamily`, `LocationContract` | [Location contracts](../../api/lyra/sdk/location_contracts/) |
| Work with resolved locations | `Locations`, `LocationOutputSchema` | [Locations](../../api/lyra/sdk/locations/), [location schemas](../../api/lyra/sdk/location_schema/), and [value rules](../locations/) |
| Bind table and location dependencies | `Dependency`, `LocationDependency`, `Input` | [Dependencies](../../api/lyra/sdk/dependencies/) |
| Acquire data with configuration | `BaseIO`, `IOConfig`, `Resource`, `ResourceConfig` | [Resources](../../api/lyra/sdk/resources/) |
| Submit and observe server jobs | `LyraClient`, `AsyncLyraClient` | [Synchronous client](../../api/lyra/api/client/sync/) and [asynchronous client](../../api/lyra/api/client/async_/) |

Guides use public imports from `lyra.sdk` and `lyra.api`. Generated definitions live in their owning modules. [Parameter rules](../parameters/) and [unit vocabulary](../units/) provide additional authoring constraints.
