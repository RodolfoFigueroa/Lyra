---
title: Choose units for output columns
description: Choose a unit for each output column and look up how Lyra writes it in JSON.
contentType: Reference
contentPlan: reference/units
sidebar: { label: "Output Column Units" }
---

Give every output column a `unit` entry in its Pandera metadata. Use a unit string for a measurement or `None` when no unit applies.

## Unit expressions in Python and JSON

Use a unit expression from [Pint’s default registry](https://pint.readthedocs.io/en/stable/getting/tutorial.html#initializing-a-registry). For example, declare a distance with `metadata={"unit": "km"}`. Lyra writes `"kilometer"` in JSON and keeps the column’s values and data type. A distance of 2.5 still means 2.5 kilometres.

| Python unit | JSON unit | Example measurement |
| --- | --- | --- |
| `"km"` | `"kilometer"` | Street length in kilometres |
| `"m**2"` | `"meter ** 2"` | Plot area in square metres |
| `"count/km**2"` | `"count / kilometer ** 2"` | Dwellings per square kilometre |
| `"%"` | `"percent"` | A share of 50 percent |
| `"degC"` | `"degree_Celsius"` | A temperature of 18 degrees Celsius |

For counts and densities, name what you count in the column description. Lyra requires the `unit` key and rejects blank or invalid unit expressions. If you don’t know the measurement’s unit, confirm it before declaring the column.

## When to use `None` or `"dimensionless"`

Use `None` when the column identifies or labels something. For example, the street ID `1042` identifies a street and has no unit. Lyra writes `None` as `null` in JSON.

Use `"dimensionless"` for a quantity whose units cancel or that uses a unitless scale. An allocation weight of `0.25` means one quarter of a street’s length. For a unitless score, describe what higher and lower values mean. Enforce any required range with [Pandera column checks](../../plugins/outputs/#declare-a-model).
