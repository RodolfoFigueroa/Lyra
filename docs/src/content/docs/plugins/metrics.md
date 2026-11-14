---
title: Register concrete metrics
description: Register related calculations under distinct names and select each metric directly.
contentType: How-to
contentPlan: plugins/metrics
sidebar: { label: "Register Concrete Metrics" }
---

Give each calculation a unique metric name so callers can select it directly. Use a Python factory to share calculation code while fixing different values for each metric. Each metric can declare its own request and output schemas.

## Define a factory for related metrics

Start with the [output schema](../outputs/) that describes the result table. This example returns one floating-point score:

```python
from collections.abc import Callable

import pandas as pd
import pandera.pandas as pa

from lyra.sdk import PluginDefinition, metric

SCORE = pa.DataFrameSchema(
    {
        "value": pa.Column(float, description="Score", metadata={"unit": None}),
    },
    strict=True,
)
```

Create a handler for each fixed multiplier. The factory gives every handler its own registered name:

```python {4}
def score_metric(name: str, factor: float) -> Callable[[], pd.DataFrame]:
    @metric(
        code_revision="1",
        name=name,
        description=f"Score with a fixed multiplier of {factor}.",
        outputs={"score": SCORE},
    )
    def calculate() -> pd.DataFrame:
        return pd.DataFrame({"value": [factor]})

    return calculate
```

## Register and select each metric

Register every generated handler in `PluginDefinition`. Callers select `score_standard` and `score_double` independently:

```python {3-4}
plugin = PluginDefinition(
    metrics=[
        score_metric("score_standard", 1.0),
        score_metric("score_double", 2.0),
    ]
)
```

Pass the registered name to [local execution](../../use/local-execution/), as shown below. For remote execution, use the same name in the request’s `metric` field:

```python
from lyra.runtime.local import LocalRunner

runner = LocalRunner.from_definitions({"example": plugin})
result = runner.run("score_double").tables
assert result["score"]["value"].tolist() == [2.0]
```

## Bind dependencies and control visibility

Bind a [dependency](../../concepts/graphs/) by its producer name and named output. For example, `Dependency("score", producer="score_standard")` selects the standard metric’s score table. Equal output names do not connect metrics automatically.

Set `private=True` on an intermediate metric to exclude it from public discovery and submissions. Lyra still executes private metrics as dependencies. To test a private metric, call `runner.run_development()` with its name and [named ID selections](../../use/local-execution/#supply-id-selections).

## Choose a cache policy

Mark one location input `primary=True` to [assign output rows to its locations](../outputs/#declare-location-ownership). This leaves caching off. Choose `cache="full"` to reuse a computation for the complete selected input set.

Choose `cache="per_location"` only if each primary location’s result is independent of the other primary locations in the batch. The [cache policy examples](../../concepts/caching/#overlapping-selections) explain which locations the handler receives when selections overlap.
