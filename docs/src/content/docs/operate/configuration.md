---
title: Configure credentials and networking
description: Supply settings and secrets, validate the TOML, and expose the API through a trusted proxy.
contentType: How-to
contentPlan: operate/configuration
sidebar: { label: "Configure Credentials And Networking" }
---

Lyra reads `lyra.toml` and its environment at process startup. Give the API and every worker the same configuration. Restart the deployment to apply edits.

The [configuration reference](../../reference/generated/configuration/) lists exact fields, defaults, and constraints. This guide explains how to supply them.

## Place configuration and secrets

Mount the TOML at `/lyra_data/config/lyra.toml` read-only. Put ordinary settings, such as database host and worker queues, in that file.

Supply secrets through the environment:

| Variable | Purpose |
| --- | --- |
| `LYRA_POSTGRES_PASSWORD` | PostgreSQL password |
| `LYRA_AGENT_API_KEY` | Job routes and MCP |
| `LYRA_ADMIN_API_KEY` | Administrator routes and CLI |

Use different values for the agent and administrator keys. Consumers receive the agent key; operators keep the administrator key.

Mount the Earth Engine service-account JSON read-only at `earth_engine.service_account_file`. In the example containers, this is `/lyra_data/secrets/service-account.json`.

## Validate a configuration file

Validate the source TOML before applying it, using either readable output or JSON for a script:

```sh
uv run lyra-admin config validate ./lyra.toml
uv run lyra-admin --json config validate ./lyra.toml
```

Validation checks syntax, schema, installed-distribution declarations, and internal queue relationships. It reads only the specified TOML.

A successful JSON response is `{"valid": true, "schema_version": 4}`. Invalid TOML or schema exits with code 2; a file-read failure exits with code 1. Credentials and a running server are unnecessary for this check.

Use startup diagnostics to check manifests and service access after deployment. Offline validation does not establish that those resources are available.

## Set the public URL

Set `api.public_base_url` to the URL clients can reach. Lyra uses it in authenticated result handoffs.

The value can contain a path prefix, but cannot contain credentials, a query, or a fragment. Remote deployments use HTTPS; loopback HTTP is accepted for local development.

When MCP is enabled, its public endpoint ends with the configured `mcp.mount_path` and a trailing slash, such as `/mcp/`.

## Configure a reverse proxy

Preserve `Host`, `Authorization`, `X-Forwarded-Proto`, and `X-Forwarded-For` at the proxy. Restrict forwarded-header trust with `api.forwarded_allow_ips` to the proxy’s actual IP addresses or Classless Inter-Domain Routing (CIDR) ranges.

Use the exact container network range when appropriate. Avoid wildcard trust when untrusted clients can reach the application port.

Verify that the public URL and proxy path let callers fetch authenticated result downloads as well as submit jobs.

## Apply a change

Edit the source TOML, validate it, and follow the [drain and restart procedure](../updates/). Configuration summaries describe the settings loaded by the current process.

For plugin selection, editable sources, and queue routing, continue to [Enable plugins and route work](../plugins/).
