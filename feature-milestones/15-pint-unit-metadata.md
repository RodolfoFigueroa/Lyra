# 15. Pint-backed unit metadata

Replace the maintained unit vocabulary with Pint's default definitions for schema
metadata. This is a small, low-priority milestone following the current set;
earlier milestones remain independently implementable.

## Accepted scope

Add Pint as a runtime dependency and use one internal `UnitRegistry()` with its
default definitions. Replace the custom `Unit` enum with plain strings validated
through `parse_units()`. Keep the registry internal, with no public registry API
or client, plugin, or deployment configuration for custom catalogs.

Every column must explicitly declare a unit string or `None`. Reject empty or
whitespace-only strings and expressions Pint cannot parse as units. Preserve
`None` for inapplicable units and `"dimensionless"` for known unitless quantities;
neither omission nor an invalid unit becomes either of these values implicitly.

Normalize accepted aliases using the parsed unit's ordinary string representation.
Preserve the declared unit and scale: kilometres remain kilometres, percent remains
percent, and Celsius remains Celsius. Normalization changes spelling only; it
does not convert values or reduce units to base units.

Apply the same validation and normalization to schema declarations and shared
column metadata contracts. Public metadata contains strings or null, including
manifests, discovery, retained descriptions, and exports. Generated JSON Schema
describes a required string-or-null field; Pint supplies semantic validation.
Do not enumerate the catalog in JSON Schema or reproduce Pint's parser.

Remove unused semantic labels without replacement mappings, compatibility aliases,
or custom definitions. Update affected declarations, tests, examples, and authoring
guidance as part of this implementation. Authoring material describes the supported
contract directly. No compatibility layer or stored-data migration is required.

Units serve only as schema metadata. Column values and ordinary dtypes retain
their existing behavior, without Pint objects embedded in quantities or columns.

## API sketch

Declare units as strings in ordinary Pandera column metadata:

```python
import pandera.pandas as pa

schema = pa.DataFrameSchema(
    {
        "area": pa.Column(
            float,
            nullable=False,
            description="Measured area",
            metadata={"unit": "m**2"},
        ),
    },
)
```

Other declarations can use `"count/km**2"` for a count density,
`"dimensionless"` for a unitless quantity, or `None` when no unit applies.
Descriptions explain what is measured or counted. Serialized metadata uses
Pint's normalized spelling, such as `"meter ** 2"` for the area above.

## Completion boundary

Schema declarations and shared metadata accept Pint's default unit expressions,
normalize their spelling consistently, and preserve strings or null through
serialization. The affected examples and authoring guidance use this contract.

Use isolated tests to cover simple and compound units, aliases, invalid and blank
input, required unit fields, explicit null, and dimensionless metadata. Verify
round trips through declarations and serialized contracts, preservation of declared
unit scale, and unchanged column values and ordinary dtypes. In particular,
kilometres, percent, and Celsius must not trigger value conversion.

Follow milestone 1's behavioral testing policy and the repository's required
verification commands. Tests use no external services and do not pin documentation
wording.

## Deferred or excluded work

Custom catalogs, public registry helpers, Pint object inputs, quantity wrapping,
unit arithmetic, conversion, dimensional checking of calculations, and NumPy or
pandas integration are outside this milestone. Storage and exports carry the
ordinary metadata strings without new unit-specific interfaces.

## Reference points

The current [unit enum](../src/lyra/contracts/units.py),
[schema compilation](../src/lyra/sdk/output_schema.py),
[column metadata contracts](../src/lyra/contracts/outputs.py), and
[unit tests](../tests/test_sdk_units.py) identify the existing validation and
serialization boundaries. Pint documents
[default registry initialization](https://pint.readthedocs.io/en/stable/getting/tutorial.html#initializing-a-registry)
and [unit parsing](https://pint.readthedocs.io/en/stable/api/facets.html#pint.facets.plain.GenericPlainRegistry.parse_units).
