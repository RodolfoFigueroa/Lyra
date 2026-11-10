# 1. Behavioral testing policy

Keep the test suite focused on supported behavior so subsequent refactors can
change internal structure without losing meaningful coverage. Establish the
testing and authoring guidance that accompanies each feature milestone.

## Accepted scope

Remove obsolete tests whose only purpose is to assert that a retired API or
module is absent, preserve historical internal organization, or pin incidental
algorithm choices and helper call counts. Apply this selectively: when a test
also checks supported behavior, retain those assertions. Returned values, public
interfaces, mutation isolation, cache hits skipping handlers, and meaningful
resource boundaries remain valuable requirements.

For example, an exact count of fingerprint-helper calls is not inherently a
requirement. A cache hit avoiding a handler invocation is observable behavior and
remains worth testing. An internal call-count assertion is justified when it
enforces a stated resource or performance requirement rather than one particular
implementation of that behavior.

Update [AGENTS.md](../AGENTS.md) to direct future tests toward supported behavior
and observable invariants. Internal assertions remain appropriate when they enforce
an explicit correctness, resource, or performance requirement; the test must make that
requirement clear. The guidance must not prohibit all implementation-level checks
or justify removing behavioral coverage during a refactor.

Use the existing ASV benchmarks to assess execution time and memory. Ordinary
optimization regressions may surface in benchmark comparisons without immediately
failing unit tests. Preserve targeted regression tests for deliberate performance
requirements. Later milestones own representative measurements for the behavior
they change, including integrated storage and caching workloads.

Lyra is in closed alpha, with no existing users or deployments to preserve.
Breaking public-name and internal-structure changes are acceptable without
compatibility scaffolding. This permits removing obsolete assertions; it does not
permit losing supported behavior, weakening validation, or declaring failed work
successful. The milestone sequence still leaves the application functional after
each change.

Keep service access isolated with mocks, dummies, or in-process fakes. Preserve
the suite's service-access guard and existing restrictions on live databases,
Redis, HTTP services, and cloud APIs. Documentation checks may verify structure,
links, rendering, schemas, and executable examples, but must not pin editorial
wording or require particular documentation topics.

## Completion boundary

The application behaves as before, the remaining tests still protect supported
behavior, and the contributor guidance clearly distinguishes useful internal
assertions from historical constraints. Relevant tests continue to pass without
weakened validation, disabled guards, or reduced coverage of observable behavior.

This establishes the testing policy for the remaining milestones. Each later
refactor includes its own affected tests, examples, and authoring guidance; cleanup
does not wait for a final catch-all milestone. Updated authoring material describes
the supported contract directly and removes obsolete instructions and examples.

## Deferred or excluded work

No application API, execution, storage, or transport changes belong here. Further
obsolete assertions discovered while implementing a feature are handled with that
feature. This milestone does not introduce a new benchmark system, broad test
rewrites, live-service tests, package-distribution smoke checks, or tests enforcing
documentation prose.

## Reference points

Relevant tests include [public imports](../tests/test_public_imports.py) and
[location performance assertions](../tests/test_location_performance_acceptance.py).
The [benchmark guide](../benchmarks/USAGE.md) describes the existing ASV workflow.
