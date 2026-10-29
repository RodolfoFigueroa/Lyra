# Agent instructions

## Authoring guidance

Skills, bundled references, and plugin authoring guides must describe the current
supported contract. When removing a capability, remove its author-facing prose
and examples instead of adding historical comparisons, lists of retired features,
or migration notes. State supported behavior directly. Include historical or
migration guidance only when the user explicitly requests it; keep it separate
from the instructions agents use to author plugins. Preserve necessary current
validation rules and operational guidance.

## Verification scope

Do not perform wheel or package-distribution smoke checks unless the user
explicitly requests them. This includes building or installing wheels for
verification, creating temporary package-installation environments, inspecting
wheel contents or entry points, and testing commands from installed wheels.

Use the existing project environment and relevant project checks to verify changes.

## Documentation tests

Do not add tests that require or forbid specific content, text, or prose in
documentation, including READMEs, skills, bundled references, and generated
documentation. Do not pin documentation wording with substring assertions,
regular expressions, or whole-file snapshots. Mechanical checks for structure,
layout, rendering, links, schemas, and executable examples are permitted, provided
they do not enforce editorial wording or which topics the documentation covers.

## Test resource isolation

Tests must not create real connections to databases, Redis, HTTP services, cloud
APIs, or any other external resource, including services on localhost or Unix
sockets. Use mocks, dummies, or in-process fakes for all service clients,
connections, engines, and acquisition operations. Do not use live credentials,
start service processes or containers, or rely on an available development service.
Replace live-service tests with isolated equivalents that preserve their assertions.
Keep the test-suite service-access guard enabled; mock the relevant boundary
instead of bypassing it. In-process ASGI transports, temporary test files, and
asyncio's internal socket pairs are permitted; they do not contact services.

## Test-suite sandbox escalation

Run the full test suite with sandbox escalation (`sandbox_permissions="require_escalated"`):

```sh
uv run pytest --cov=lyra_app --cov=lyra --cov-report=term-missing --cov-report=xml
```

The restricted sandbox blocks asyncio's internal socket wakeups with
`PermissionError: [Errno 1] Operation not permitted`. Tests using thread executors
can hang as a result, including database-runtime tests and asynchronous file I/O.
This is an environment restriction, not a test failure. Focused tests that do not
need these wakeups can run in the sandbox; escalate them if the same issue occurs.
Do not change application code or weaken tests to work around this restriction.
