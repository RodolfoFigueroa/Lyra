# 03 · Compile and execute from one catalog

Each catalog build owns a fresh provider registry, the compiled graph, and the matching implementations. A local run and a queued run should resolve the same SDK declarations and apply the same preflight checks.

## Registration and binding

Construct one registry from all active plugins early enough for the installed-plugin loader to use it during binding. Use that registry for resource validation, cross-plugin provider dependencies, definition binding, node compilation, plugin manifests, and final catalog assembly. Each plugin's manifest lists only its own registrations, including those it shares. A self-contained plugin can still be inspected from its local declarations.

| Registrations in one build | Outcome |
| --- | --- |
| Same resource key and same provider class in two plugins | One registration, visible in both owning manifests |
| Same resource key and different classes | Reject the build |
| Different resource keys claiming one provider ID | Reject the build, even for the same class |

## Local and worker execution

Give `GraphExecutor` the compiled catalog and resolve handlers from `catalog.implementations`. Remove the separately supplied implementation map and the preflight graph rebuild used to compare them. Preflight still checks the plan's selected routes, revisions, pinned interventions, and provider scopes. Verify that a concrete intervention target matches its accepted declaration before acquiring the resource.

At worker startup, call `Catalog.load` with the configured plugins and build revision. Resolve each submitted root and all its dependencies from that catalog and execute the graph as one job. Remove the worker's duplicate compiler, discarded dependency walk, and per-metric implementation wrappers. Verify job identity and build revision and prepare the accepted parameters against the live catalog. While the deployment uses assigned queues, verify that the pool serves the root metric's queue and that the job arrived from that queue.

Exercise both entry points with cross-plugin dependencies, shared and conflicting registrations, and a stale accepted plan. Use a cold run to verify that the worker executes the selected root and its required dependencies as one job. An incompatible declaration or job fails before the affected handler or acquisition.
