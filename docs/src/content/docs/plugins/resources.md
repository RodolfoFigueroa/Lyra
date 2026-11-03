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

```python
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

```python {15-16}
from pathlib import Path

import pandas as pd
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

```python {8}
import pandera.pandas as pa

observation_schema = pa.DataFrameSchema(
    {
        "value": pa.Column(
            float,
            description="Demonstration value.",
            metadata={"unit": None},
        )
    }
)
```

Request `shared_files` in the metric, then register the metric and resource class:

```python {12,19}
from typing import Annotated

from lyra.sdk import PluginDefinition, Resource, metric


@metric(
    name="observations",
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

Resource names apply across the deployment. Plugins that share a name must register the same class; they share one instance within each job. Use different names to configure separate instances of the same class.

Lyra initializes only the resources a job requests. When the job ends, Lyra closes them in reverse initialization order, including after a failure.

## Manage resource clients and credentials

Override lifecycle methods when your resource needs a service client:

- Use `setup()` to create and authenticate clients.
- Use `close()` to release clients, including any client created before `setup()` failed.
- Declare required secret names with `secret_names = ("token",)` and read their values from `self.secrets["token"]`.

Keep imports, registration, and manifest generation free of service calls and credential reads. Ask your deployment operator to [configure resource credentials](../../operate/configuration/#configure-plugin-resources) before running the metric.

For local file reads, wrap `path.read_bytes()` in `with self.operation("read_bytes"):`. For downloads, consume the complete response inside the block and configure timeouts on the service client.
