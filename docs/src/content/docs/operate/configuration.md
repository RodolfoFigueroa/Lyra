---
title: Configure credentials and networking
description: Supply settings and secrets, validate the TOML, and expose the API through a trusted proxy.
contentType: How-to
contentPlan: operate/configuration
sidebar: { label: "Configure Credentials And Networking" }
---

Lyra reads `lyra.toml` and its environment at process startup. Give the API and every worker the same configuration. Restart the deployment to apply edits.

Each API application keeps a startup snapshot of its configuration and credentials. Its HTTP endpoints and [Model Context Protocol (MCP) tools](../../use/mcp/) share that snapshot and its service resources.

The [configuration reference](../../reference/generated/configuration/) lists exact fields, defaults, and constraints. This guide explains how to supply them.

## Place configuration and secrets

Mount the TOML at `/lyra_data/config/lyra.toml` read-only. Put ordinary settings, such as database host and worker queues, in that file.

Supply secrets through the environment according to each process’s role:

| Variable | Required by | Purpose |
| --- | --- | --- |
| `LYRA_POSTGRES_PASSWORD` | API and workers with `[database]` | PostgreSQL password |
| `LYRA_AGENT_API_KEY` | API | Job routes and MCP |
| `LYRA_ADMIN_API_KEY` | API | Administrator routes and CLI |

The API requires both keys even when you disable MCP. Use different values for the agent and administrator keys. Give consumers the agent key and keep the administrator key for operators.

## Configure PostgreSQL access

Add `[database]` when your deployment needs database lookups or metrics that request the `database` resource. Set these fields in `lyra.toml`:

- `host`: PostgreSQL hostname
- `name`: database name
- `user`: database username

Supply `LYRA_POSTGRES_PASSWORD` to the API and every worker. Workers test the connection before loading executable plugins and stop if that check fails.

To run without PostgreSQL, omit `[database]` and all `[database.*]` pool sections. Metrics and providers that read files can run without database credentials. Metrics requesting an unconfigured database fail with `database_not_configured` and `retryable=false`.

Metrics can still use the other [runtime services](../../plugins/runtime/) without database configuration.

The `/ready` endpoint checks Redis and any configured PostgreSQL connection. Without `[database]`, it returns `database.status="not_configured"`, and readiness depends on Redis alone. Set each probe timeout separately:

- `redis.readiness_timeout_seconds`: Redis timeout, default `1.0` seconds
- `database.readiness_timeout_seconds`: PostgreSQL timeout, default `1.0` seconds

## Configure plugin resources

Use `[resources.resource_name.config]` for settings required by a plugin’s [input/output (I/O) resources](../../plugins/runtime/#define-an-io-resource). Match the name registered by the plugin. For example, configure the `shared_files` resource’s directory:

```toml
[resources.shared_files.config]
directory = "/data"
```

For a resource named `remote` that declares a `token` secret, reference an environment variable:

```toml
[resources.remote.secrets.token]
env = "REMOTE_ACCESS_TOKEN"
```

Set `REMOTE_ACCESS_TOKEN` on every worker that runs the resource. To use a credential file instead, replace that secret entry with:

```toml
[resources.remote.secrets.token]
file = "/lyra_data/secrets/remote-token"
```

Mount the UTF-8 credential file on each worker that needs it. Each secret entry accepts exactly one source: `env` or `file`.

Lyra validates resource settings and reads credentials when it acquires the resource. Provider acquisition occurs during submission; calculation resources initialize during graph execution. Offline configuration validation checks the TOML structure without reading credentials or checking plugin-specific settings.

## Select a location provider

Enable exactly one plugin that declares a location provider. Lyra selects its named resource automatically and rejects multiple enabled provider declarations.

Configure that resource under `[resources.resource_name.config]` and `[resources.resource_name.secrets]`. Replace `resource_name` with the name declared by the plugin, and supply its credentials to the API process. Plugin inspection exposes provider metadata without contacting services.

## Enable Earth Engine for workers

Omit `[earth_engine]` to disable Earth Engine for every worker using the document. To enable it, add the section with your project ID:

```toml
[earth_engine]
project = "your_project_id_here"
```

An empty section is invalid. The credential path defaults to `/lyra_data/secrets/service-account.json`. Set `earth_engine.service_account_file` to an absolute path if you use another location.

Place your service-account JSON at `./secrets/service-account.json` and keep it outside version control. Add this read-only mount to each worker service’s `volumes` list, preserving its existing mounts:

```yaml
- ./secrets/service-account.json:/lyra_data/secrets/service-account.json:ro
```

With `[earth_engine]` configured, every worker reads the file and initializes Earth Engine before importing executable plugins. Workers stop during startup if they cannot read valid credentials. The API validates the section’s structure without reading the file or initializing Earth Engine.

## Pass credentials to Python entry points

When you start Lyra from Python, pass credentials separately from configuration:

- Pass validated `ApiCredentials` through the `credentials` argument of `create_app`.
- Pass explicit configuration and validated `WorkerCredentials` through the `config` and `credentials` arguments of `launch_worker`.

Credential models reject changes after construction and omit their values from serialization and representations. Configuration parsing and offline validation require no credentials.

## Validate a configuration file

Validate the source TOML before applying it, using either readable output or JSON for a script:

```sh
uv run lyra-admin config validate ./lyra.toml
uv run lyra-admin --json config validate ./lyra.toml
```

Validation checks syntax, schema, installed-distribution declarations, and internal queue relationships. It reads only the specified TOML.

A successful JSON response is `{"valid": true, "schema_version": 5}`. Invalid TOML or schema exits with code 2; a file-read failure exits with code 1. Credentials and a running server are unnecessary for this check.

At startup, the API validates plugin catalogs before creating service clients. After deployment, check `/ready` for Redis and any configured PostgreSQL connection. Offline configuration validation does not check service availability.

## Set the public URL

Set `api.public_base_url` to the URL clients can reach. Lyra uses it in authenticated result handoffs.

The value can contain a path prefix, but cannot contain credentials, a query, or a fragment. Remote deployments use HTTPS; loopback HTTP is accepted for local development.

When MCP is enabled, its public endpoint ends with the configured `mcp.mount_path` and a trailing slash, such as `/mcp/`.

## Configure a reverse proxy

Preserve `Host`, `Authorization`, `X-Forwarded-Proto`, and `X-Forwarded-For` at the proxy. Restrict forwarded-header trust with `api.forwarded_allow_ips` to the proxy’s actual IP addresses or Classless Inter-Domain Routing (CIDR) ranges.

Use the exact container network range when appropriate. Avoid wildcard trust when untrusted clients can reach the application port.

Verify that the public URL and proxy path let callers fetch authenticated result downloads as well as submit jobs.

## Cache metric tables

Set `cache_dir` to reuse validated metric tables and operation locations across jobs. Add this top-level setting before any TOML section:

```toml
cache_dir = "/lyra_data/cache/metrics"
```

Use an absolute path that participating workers share on one host. Keep it separate from job scratch directories. Omitting `cache_dir` disables persistent caching.

Workers sharing a directory must use equivalent code, plugin contracts, and resource settings. Keep source data unchanged while reusing cached results, and manage invalidation and cleanup through your operational workflow. Stop all processes using a directory before removing it.

Cache storage errors produce warnings and may cause recalculation. See [reuse across executions](../../concepts/graphs/#reuse-tables-across-executions) for dependency behavior and concurrency limits.

## Apply a change

Edit the source TOML, validate it, and follow the [drain and restart procedure](../updates/). Configuration summaries describe the settings loaded by the current process.

For plugin selection, editable sources, and queue routing, continue to [Enable plugins and route work](../plugins/).
