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
| Run locally and read execution reports | `LocalRunner` | [Local execution](../../api/lyra/runtime/local/) |
| Read native results and retained reports | `RunResult`, `RunMetadata`, `ResultStore` | [Results](../../api/lyra/runtime/results/) and [read-only store](../../api/lyra/runtime/result_store/) |
| Interpret execution reports | `ExecutionReport`, `ReportNode`, `OperationReport`, `DataBinding`, `ParameterBinding` | [Report contracts](../../api/lyra/contracts/reports/) |
| Handle publication and lookup failures | `ExecutionPersistenceError`, `ResultAccessError` | [Errors](../../api/lyra/contracts/errors/) |
| Declare source identity | `LocationFamily`, `LocationContract` | [Location contracts](../../api/lyra/contracts/locations/) |
| Work with resolved locations | `geopandas.GeoDataFrame` | [Native location tables](../locations/) |
| Bind table and location dependencies | `Dependency`, `LocationDependency`, `Input` | [Dependencies](../../api/lyra/sdk/dependencies/) |
| Read managed Parquet records | `ParquetLocationProvider` | [Parquet provider](../../api/lyra/sdk/parquet/) |
| Read managed PostGIS records | `PostGISLocationProvider` | [PostGIS provider](../../api/lyra/sdk/postgis/) |
| Share PostgreSQL access | `PostgresIO` | [PostgreSQL resource](../../api/lyra/sdk/postgres/) |
| Acquire data | `BaseIO`, `Resource` | [Resources](../../api/lyra/sdk/resources/) |
| Declare resource configuration | `IOConfig`, `ResourceConfig`, `SecretReference` | [Resource contracts](../../api/lyra/contracts/resources/) |
| Describe a job submission | `JobCreateRequest` | [Job contracts](../../api/lyra/contracts/jobs/) |
| Submit and observe server jobs | `LyraClient`, `AsyncLyraClient` | [Synchronous client](../../api/lyra/api/client/sync/) and [asynchronous client](../../api/lyra/api/client/async_/) |

When defining a metric, check the [rules for parameters and handler signatures](../parameters/) and the [supported units for output columns](../units/).
