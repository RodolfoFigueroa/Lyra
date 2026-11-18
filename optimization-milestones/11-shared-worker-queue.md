# 11 · Run one worker queue per deployment

A submitted root metric and all its dependencies execute as one RQ job. With one Lyra deployment per Redis instance, send every job to one queue and let every worker consume it. This allows a free worker to serve the next waiting graph regardless of the root metric. A short job may wait when long jobs occupy all workers. Reconsider separate pools if measured wait times or dedicated hardware requirements justify reserving capacity.

## Deployment and worker contract

Provide a default queue name and one deployment-level override. Use a single worker configuration with configurable concurrency. Remove per-metric queue overrides, allowlists, named pools, and pool selection at startup. Remove queue assignments from catalog descriptions and the operational fingerprint.

The submitter, worker launcher, worker, and admin interfaces all resolve the same configured queue. Remove worker checks against a root metric's assigned queue and its named pool. Workers still check the submitted job identity, build revision, accepted parameters and plan, pinned interventions, and provider scopes. A dependency executes on the worker that accepted the graph.

Have the admin API and CLI report queue depth, worker heartbeats, and jobs by status for the configured queue without a queue selector. Use one worker service in the example deployment. Update configuration examples, startup commands, operator documentation, and fixtures to the same settings. Discard jobs queued under the previous routing configuration when switching the deployment.

## Verification

Use queue and worker fakes to submit graphs with different root metrics. Verify both use the default queue, then repeat with a deployment override and confirm that submission, consumption, and admin reporting agree. Cover configured worker concurrency and rejection of a job with an incompatible build or accepted plan. These checks can run without starting Redis or worker processes.
