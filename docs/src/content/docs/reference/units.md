---
title: Output unit vocabulary
description: Look up canonical column units and the meaning of an explicitly inapplicable unit.
contentType: Reference
contentPlan: reference/units
sidebar: { label: "Output Unit Vocabulary" }
---

Every output column supplies a unit explicitly. Use a member of `Unit` from `lyra.sdk`, its exact canonical string, or `None` when no unit applies. JSON manifests use canonical strings or null.

## Supported units

Use these exact canonical strings in manifests, or the corresponding `Unit` members in Python:

| Enum member | Value | Meaning |
| --- | --- | --- |
| `MILLIMETRE` | `mm` | Millimetres of length. |
| `METRE` | `m` | Metres of length. |
| `KILOMETRE` | `km` | Kilometres of length. |
| `SQUARE_METRE` | `m2` | Square metres of area. |
| `SQUARE_KILOMETRE` | `km2` | Square kilometres of area. |
| `HECTARE` | `ha` | Hectares of area. |
| `DEGREE_CELSIUS` | `degC` | Temperature in degrees Celsius. |
| `KELVIN` | `K` | Temperature in kelvins. |
| `SECOND` | `s` | Duration in seconds. |
| `DAY` | `day` | Duration in days. |
| `YEAR` | `year` | Duration in years; document the workflow’s year convention. |
| `CALENDAR_YEAR` | `calendar_year` | Calendar-year value, not an elapsed duration. |
| `COUNT_PER_SQUARE_KILOMETRE` | `count/km2` | Count per square kilometre; describe what is counted. |
| `COUNT` | `count` | Number of entities; describe what is counted. |
| `RATIO` | `ratio` | Quotient; describe its numerator and denominator. |
| `PERCENT` | `percent` | Value expressed per hundred (50 means 50%). |
| `SCORE` | `score` | Workflow-defined scale; describe its meaning and interpretation. |
| `DIMENSIONLESS` | `dimensionless` | Known unitless quantity without a more specific applicable unit. |

## Choose a meaningful value

Use `None` for labels, identifiers, and booleans when their values have no unit. A numeric identifier can also have no applicable unit. `None` means not applicable; it does not mean that the scientific unit is unknown.

Units explain the meaning of values without converting them, imposing numeric ranges, or restricting column types. Describe a score’s scale explicitly, and validate any required range in your calculation rather than relying on its unit.

Unknown strings, aliases, incorrect casing, and omitted units are rejected. Establish the unit from the workflow before choosing a value. If the vocabulary cannot express a required unit, discuss an SDK addition rather than inventing an identifier or silently changing the calculation.

For a worked output declaration, see [output tables](../../plugins/outputs/). Progress reporting uses separate human-readable unit labels.
