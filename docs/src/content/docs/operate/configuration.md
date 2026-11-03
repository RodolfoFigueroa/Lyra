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

Mount the TOML at `/lyra_data/config/lyra.toml` read-only. Put ordinary settings, such as database host and worker queues, in that file.

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

Validation checks syntax, schema, installed-distribution declarations, and internal queue relationships. It reads only the specified TOML.

A successful JSON response is `{"valid": true, "schema_version": 5}`. Invalid TOML or schema exits with code 2; a file-read failure exits with code 1. Credentials and a running server are unnecessary for this check.

After deployment, check `/ready` for Redis availability. Set `redis.readiness_timeout_seconds` to change the probe’s default timeout of 1s. Workers connect to plugin data sources during job execution.

## Set the public URL

Set `api.public_base_url` to the URL clients can reach. Lyra uses it in authenticated result handoffs.

The value can contain a path prefix, but cannot contain credentials, a query, or a fragment. Remote deployments use HTTPS; loopback HTTP is accepted for local development.

When MCP is enabled, its public endpoint ends with the configured `mcp.mount_path` and a trailing slash, such as `/mcp/`.

## Configure a reverse proxy

Preserve `Host`, `Authorization`, `X-Forwarded-Proto`, and `X-Forwarded-For` at the proxy. Restrict forwarded-header trust with `api.forwarded_allow_ips` to the proxy’s actual IP addresses or Classless Inter-Domain Routing (CIDR) ranges.

Use the exact container network range when appropriate. Avoid wildcard trust when untrusted clients can reach the application port.

Verify that the public URL and proxy path let callers fetch authenticated result downloads as well as submit jobs.

## Cache metric tables

Follow [Configure computation caching](../caching/) to enable persistent reuse and manage invalidation.

## Apply a change

Edit the source TOML, validate it, and follow the [drain and restart procedure](../updates/). Configuration summaries describe the settings loaded by the current process.

For plugin selection, editable sources, and queue routing, continue to [Enable plugins and route work](../plugins/).
