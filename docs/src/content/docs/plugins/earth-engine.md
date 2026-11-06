---
title: Use Earth Engine in metrics
description: Configure an Earth Engine resource and acquire data inside its managed lifecycle.
contentType: How-to
contentPlan: plugins/earth-engine
sidebar: { label: "Use Earth Engine In Metrics" }
---

Manage Earth Engine access through a plugin resource. Declare `earthengine-api` as a dependency when your plugin imports `ee`. If you also use the helpers in `lyra.utils.ee`, select `lyra[earth-engine]`.

Define the project setting, declare the credential secret, and initialize the client in `setup()`:

```python {12,14-18,20-21}
import ee
from lyra.sdk import BaseIO, IOConfig
from pydantic import Field


class EarthEngineSettings(IOConfig):
    project: str = Field(min_length=1)


class EarthEngineIO(BaseIO[EarthEngineSettings]):
    config_model = EarthEngineSettings
    secret_names = ("service_account_json",)

    def setup(self) -> None:
        credentials = ee.ServiceAccountCredentials(
            key_data=self.secrets["service_account_json"]
        )
        ee.Initialize(credentials, project=self.config.project)

    def close(self) -> None:
        ee.Reset()
```

Connect the resource to your metric:

1. Add methods that complete Earth Engine requests inside `with self.operation("read"):`. Construct images, collections, and reducers in these methods.
2. Register the class with `PluginDefinition(resources={"elevation": EarthEngineIO}, ...)`.
3. Request it in a handler argument annotated with `Annotated[EarthEngineIO, Resource("elevation")]`.

Keep credentials and project settings in [named resource configuration](../../operate/configuration/#configure-plugin-resources), separate from metric parameters.

Configure this example resource through named settings and a secret reference:

```toml
[resources.elevation.config]
project = "your_project_id_here"

[resources.elevation.secrets.service_account_json]
file = "/lyra_data/secrets/elevation.json"
```

Mount the credential file read-only on workers executing these metrics, and grant the service account access to the requested assets. You can also supply the JSON secret through an environment reference. For local execution, pass the equivalent `ResourceConfig` to `LocalRunner` through `resource_config`.

Lyra initializes requested resources during execution and closes them afterward, including after setup failures. Credential or initialization failures stop the affected execution. Keep imports, factories, and manifest generation free of credential access and initialization.

The Earth Engine client shares state across a process, so this example supports one configuration per executing process. Coordinate initialization and cleanup across plugins that share that state. Creating separate resource instances does not isolate their clients.

The helper `lyra.utils.ee.reduce_ee_image_over_gdf` reprojects geometry to EPSG:4326. Its `scale` is reduction resolution in metres. Preserve the workflow’s established dataset, reducer, and resolution.
