---
title: Configure credentials and networking
description: Supply settings and secrets, validate the TOML, and expose the API through a trusted proxy.
contentType: How-to
contentPlan: operate/configuration
sidebar: { label: "Configure Credentials And Networking" }
---

Configure Lyra through `lyra.toml`, supply credentials to each process, and validate the file before deployment. Give the API and every worker the same configuration, then restart them to apply edits.

Each API application keeps a startup snapshot of its configuration and credentials. Its HTTP endpoints and [Model Context Protocol (MCP) tools](../../use/mcp/) share that snapshot and its service resources.

The [configuration reference](../../reference/generated/configuration/) lists exact fields, defaults, and constraints. This guide explains how to supply them.

## Place configuration and secrets

Mount `lyra.toml` read-only at `/lyra_data/config/lyra.toml` for the API and workers. Set `jobs.queue` to choose a queue name (default `lyra`). Set `worker.concurrency` to choose how many processes the worker service starts.

Supply secrets through the environment according to each process’s role:

| Variable | Required by | Purpose |
| --- | --- | --- |
| `LYRA_AGENT_API_KEY` | API | Job routes and MCP |
| `LYRA_ADMIN_API_KEY` | API | Administrator routes and CLI |

The API requires both keys even when you disable MCP. Use different values for the agent and administrator keys. Give consumers the agent key and keep the administrator key for operators.

## Configure plugin resources

Follow [Configure plugin data resources](../resources/) for named settings, secret references, and the designated location provider. Supply required credentials to the workers that acquire that data.

## Pass credentials to Python entry points

When you start Lyra from Python, pass credentials separately from configuration:

- Pass validated `ApiCredentials` through the `credentials` argument of `create_app`.
- Pass explicit configuration through the `config` argument of `launch_worker`.

`ApiCredentials` rejects changes after construction and excludes secrets from serialized output and object representations. Configuration parsing and offline validation require no credentials.

## Validate a configuration file

Validate the source TOML before applying it, using either readable output or JSON for a script:

```sh
uv run lyra-admin config validate ./lyra.toml
uv run lyra-admin --json config validate ./lyra.toml
```

The command checks the file’s syntax and configuration fields, including the plugin entries. You can run it without credentials or a running server.

On success, `--json` prints `{"valid": true, "schema_version": 8}`. The command exits with code 2 for invalid configuration or code 1 if it can’t read the file.

## Connect the API and workers to Valkey

Set `valkey.url` to an endpoint that both the API and workers can reach.

| Connection | Example |
| --- | --- |
| Network | `redis://valkey:6379/0` |
| Encrypted network | `rediss://valkey.example.com:6380/0` |
| Unix socket | `unix:///run/valkey/valkey.sock` |

After deployment, check `valkey.status` in the `/ready` response. A value of `ok` confirms that Valkey answered the probe. Set `valkey.readiness_timeout_seconds` to change the default timeout of 1s. Workers connect to plugin data sources when they run a job.

## Set the public URL

Set `api.public_base_url` to the URL clients can reach. Lyra uses it in authenticated result handoffs.

The value can contain a path prefix, but cannot contain credentials, a query, or a fragment. Remote deployments use HTTPS; loopback HTTP is accepted for local development.

When MCP is enabled, its public endpoint ends with the configured `mcp.mount_path` and a trailing slash, such as `/mcp/`.

## Configure a reverse proxy

Preserve `Host`, `Authorization`, `X-Forwarded-Proto`, and `X-Forwarded-For` at the proxy. Restrict forwarded-header trust with `api.forwarded_allow_ips` to the proxy’s actual IP addresses or CIDR ranges.

Use the exact container network range when appropriate. Avoid wildcard trust when untrusted clients can reach the application port.

Verify that the public URL and proxy path let callers fetch authenticated result downloads as well as submit jobs.

## Cache metric tables

Follow [Configure computation caching](../caching/) to enable persistent reuse and manage invalidation.

## Apply a change

Edit the source TOML, validate it, and follow the [drain and restart procedure](../updates/). Configuration summaries describe the settings loaded by the current process.

To select a plugin or use an editable checkout, follow [Enable plugins](../plugins/).

In Python, validate deployment settings with `lyra.contracts.config.LyraConfig`. For resource settings and credential references, use `IOConfig`, `ResourceConfig`, and `SecretReference` from `lyra.contracts.resources`. These models validate settings without reading credentials or creating directories.

The API reads its credentials at startup. Workers resolve a plugin’s credentials when they first acquire its resource.
