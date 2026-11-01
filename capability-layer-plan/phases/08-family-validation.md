# Phase 08 — Family validation

Read the [shared workflow](index.md). Continue after
[phase 07](07-edition-registration.md). Apply ledger decisions **13, 14, 15, 24**.

Enforce the structural contract shared by all explicitly associated variants in
a family.

## Work

- Validate shared location-family structure and metric-family named native input
  roles, location families, and named output schemas.
- Compare the complete ordinary parameter contract: names, types, requiredness,
  nullability, defaults, bounds, allowed values, and other declared constraints.
  Compare the supported generated schemas without introducing a general-purpose
  schema-equivalence engine; incidental ordering or local reference names must
  not create false differences.
- Permit concrete dependency bindings, values, periods, definitions, and fixed
  methodology to vary within the shared structural contract. Preserve their
  descriptive edition metadata.
- Retain an authoritative shared contract for later family inspection. Reject
  incompatible variants instead of weakening their parameter validation or
  automatically splitting or merging families.

## Acceptance and validation

- Test mismatches in columns, types, nullability, units, structural constraints,
  input roles, output names, and ordinary parameter defaults and constraints.
- Test valid edition-specific data/dependency differences and unrelated families
  that happen to have equal schemas.
- Confirm errors identify the family, variants, and incompatible contract area.
- Run the index's Python checks and affected schema and compilation tests.

## Boundary

Do not implement visibility policy or redesign public discovery in this phase.
The next phase introduces private metrics. Edition-dependent public parameter
schemas remain deferred.
