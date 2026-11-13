---
title: Define data resources
description: Manage file or service access through named resources with configuration and cleanup.
contentType: How-to
contentPlan: plugins/resources
sidebar: { label: "Define Data Resources" }
---

Use a resource to share data access and its lifecycle across nodes. The example below uses a local Parquet file; service-specific acquisition belongs in the resource implementation.

## Define an I/O resource

Define an input/output (I/O) resource to reuse file or service access across nodes. This example reads a Parquet file into a DataFrame. Your plugin environment needs pandas and a Parquet reader supported by pandas.

Create the example’s input file in a directory you control before running its metric:

```python title="resource_example.py"
from pathlib import Path
import pandas as pd

source_directory = Path("example-data")
source_directory.mkdir(exist_ok=True)
pd.DataFrame({"value": [20.0, 80.0, 40.0]}).to_parquet(
    source_directory / "observations.parquet",
    index=False,
)
```

Use the absolute path of `example-data` as the resource’s configured directory. This fixture contains every column the output declaration below requires.

Declare the file settings and wrap the complete read in `operation()`:

```python title="resource_example.py" {8-9,12-13}
from lyra.sdk import BaseIO, IOConfig


class FileSettings(IOConfig):
    directory: Path


class DataFrameIO(BaseIO[FileSettings]):
    config_model = FileSettings

    def read_parquet(self, name: str) -> pd.DataFrame:
        with self.operation("read_parquet"):
            return pd.read_parquet(self.config.directory / name)
```

A resource method may return any Python value, including a DataFrame. Finish reading and decoding inside the operation block. Lyra records the duration and outcome without serializing the result.

Declare the Parquet file’s output schema before registering the metric. This example expects one `float64` column containing demonstration values without a measurement unit:

```python title="resource_example.py" {8}
import pandera.pandas as pa

observation_schema = pa.DataFrameSchema(
    {
        "value": pa.Column(
            float,
            description="Demonstration value.",
            metadata={"unit": None},
        )
    },
    strict=True,
)
```

Request `shared_files` in the metric, then register the metric and resource class. Declare the file as a direct [origin](../location-providers/#define-revision-coverage) because the metric reads it itself:

```python title="resource_example.py" {9,14,21}
from typing import Annotated

from lyra.sdk import Origin, PluginDefinition, Resource, metric


@metric(
    code_revision="1",
    name="observations",
    origins=(Origin(id="example-observations", revision="1"),),
    description="Read observations from a Parquet file.",
    outputs={"observations": observation_schema},
)
def observations(
    files: Annotated[DataFrameIO, Resource("shared_files")],
) -> pd.DataFrame:
    return files.read_parquet("observations.parquet")


plugin = PluginDefinition(
    metrics=[observations],
    resources={"shared_files": DataFrameIO},
)
```

Set the directory in `lyra.toml`. The configuration name must match the name in `Resource("shared_files")`:

```toml
[resources.shared_files.config]
directory = "/data"
```

Change the origin revision when the file’s contents change. If your metric receives data only through provider inputs or upstream outputs, omit direct origins. Operations compute from their allowed inputs without resource or origin bindings.

Use plain `BaseIO` when your resource needs no settings. Its empty `IOConfig` rejects unexpected fields.

For custom settings, declare both `BaseIO[FileSettings]` and `config_model = FileSettings`. The generic argument types `self.config`; `config_model` selects runtime validation. Subclasses inherit both unless you override them.

## Compose resources

Declare a dependency as an annotated instance attribute to use another resource’s configured instance. In this example, `ObservationAccess` uses the file reader, and `RegionalObservationAccess` inherits that dependency:

```python title="resource_example.py" {2,8}
class ObservationAccess(BaseIO):
    files: Annotated[DataFrameIO, Resource("shared_files")]

    def observations(self) -> pd.DataFrame:
        return self.files.read_parquet("observations.parquet")


class RegionalObservationAccess(ObservationAccess):
    pass
```

Register `RegionalObservationAccess` and `DataFrameIO` in `PluginDefinition.resources`. To use a different reader, override the inherited `files` annotation with that reader’s registration name.

Before constructing resources or reading credentials, Lyra checks dependency declarations for:

- Missing registrations
- Incompatible resource types
- Dependency cycles

Constructors receive only configuration and secrets. Lyra initializes dependencies and injects them into the declared attributes before calling `setup()`.

If plugins share a resource name, register the same class under that name. Within each execution scope, Lyra shares one instance per registration across direct requests and dependencies. Register the class under different names when you need separate instances.

During managed execution, providers and metrics share these resource instances. Lyra initializes and closes resources within each run; separate runs use fresh instances. Sharing resources doesn’t create a transaction across the run.

Lyra initializes only needed resources and their dependencies. During cleanup, Lyra closes each resource before its dependencies, including after partial setup failures. Don’t close injected dependencies yourself; they remain available throughout your resource’s `close()` method.

If one resource fails to close, Lyra continues closing the others. A cleanup error preserves any earlier setup or execution error. If execution otherwise succeeds, a cleanup error fails the run.

## Manage resource clients and credentials

Override lifecycle methods when your resource needs a service client:

- Use `setup()` to create and authenticate clients.
- Use `close()` to release clients, including any client created before `setup()` failed.
- Declare required secret names with `secret_names = ("token",)` and read their values from `self.secrets["token"]`.

Keep imports, registration, and live inspection free of service calls and credential reads. Ask your deployment operator to [configure resource credentials](../../operate/configuration/#configure-plugin-resources) before running the metric.

For local file reads, wrap `path.read_bytes()` in `with self.operation("read_bytes"):`. For downloads, consume the complete response inside the block and configure timeouts on the service client.

## Share a native PostgreSQL backend

Register `PostgresIO` to share one native Ibis PostgreSQL backend during an execution. This resource declares a dependency on the `city_db` registration:

```python {6,9-11}
from typing import Annotated
from lyra.sdk import BaseIO, PluginDefinition, PostgresIO, Resource


class CityAccess(BaseIO):
    database: Annotated[PostgresIO, Resource("city_db")]


plugin = PluginDefinition(resources={"city_db": PostgresIO, "city_access": CityAccess})
```

Providers use the same class annotation. Metrics use it on their resource argument.

Configure the database registration with connection settings and a password secret reference. This configuration uses the default PostgreSQL port:

```python {10}
from lyra.sdk import ResourceConfig, SecretReference

resource_config = {
    "city_db": ResourceConfig(
        config={
            "host": "db.example.com",
            "database": "city",
            "user": "lyra",
        },
        secrets={"password": SecretReference(env="CITY_DB_PASSWORD")},
    ),
}
```

A `PostgresIO` subclass can add domain query methods using `self.backend`. Metrics can request that subclass and call its methods. Each acquiring provider or metric must declare its own [origins](../location-providers/#declare-a-provider), including reads delegated through resources.

Lyra initializes the backend when acquisition needs it. The owning `PostgresIO` disconnects during cleanup, including after partial setup failures. Providers borrow that backend and must not close it.

Managed resource access runs sequentially. Synchronize any concurrent access you introduce through custom methods or `.backend`. Sharing a connection does not add automatic locking or create a transaction spanning the run.
