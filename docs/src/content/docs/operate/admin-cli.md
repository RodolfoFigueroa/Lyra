---
title: Use the administrative CLI
description: Inspect a running server and validate configuration with lyra-admin.
contentType: How-to
contentPlan: operate/admin-cli
sidebar: { label: "Use The Administrative CLI" }
---

`lyra-admin` gives operators one-shot health, job, worker, queue, and catalog observations. Each command prints a result and exits.

The command-line interface (CLI) ships in `lyra-api`. Follow [Install Lyra for Python](../../start/installation/) to prepare this repository’s environment or add the client to another project.

## Connect to the server

Put global connection options before the command:

```sh
uv run lyra-admin --host localhost:5219 --no-secure health
uv run lyra-admin --host lyra.example.com --secure status
```

Remote inspection requires `LYRA_ADMIN_API_KEY` in the environment. `--admin-api-key your_admin_key_here` overrides it. Health checks are public; offline configuration validation needs no credential.

`--host` accepts `hostname[:port][/base_path]` without a URL scheme. The defaults are `localhost:5219`, HTTP, and a request timeout of 30s. Use `--secure` for HTTPS and `--timeout 60` to allow a longer request.

The CLI connects to an existing server. Use the [deployment guide](../deployment/) to start the service.

## Choose an inspection

Choose a command for the part of the deployment you need to inspect:

| Command | What it tells you |
| --- | --- |
| `health` | Redis and PostgreSQL readiness |
| `health --live` | API process liveness |
| `status` | Administrative service summary |
| `config-summary` | Loaded configuration with secrets omitted |
| `jobs list --queue your_queue_name_here --status running` | One queue/status page |
| `jobs get your_job_id_here` | Job state, queue, worker identity, and failure diagnostics |
| `workers list` / `workers get your_worker_name_here` | Worker observations and heartbeat freshness |
| `queues list` | Queue depth and worker/routing coverage |
| `plugins list` | Configured distributions and installed versions |
| `catalog show` | Catalog fingerprint, metrics, plugins, and routing |
| `routing list` | Effective defaults and overrides |

For example:

```sh
uv run lyra-admin --json jobs list \
  --queue interactive --status running --limit 20
```

Job lists require both queue and status. Use `--offset 0` and `--limit 20` (1–200) for pagination. See [monitoring](../runbook/) for ordering, retention, and observation limits.

## Validate configuration offline

Check a source configuration file without connecting to a running server:

```sh
uv run lyra-admin config validate ./lyra.toml
```

This reads the specified TOML and validates its schema and internal relationships. It does not require a server or secrets. [Configuration](../configuration/#validate-a-configuration-file) explains what the check establishes.

## Use JSON in scripts

`--json` writes one complete response object to standard output, including nulls and observation metadata. Errors go to standard error:

```json
{
  "error": {
    "kind": "usage",
    "message": "Set LYRA_ADMIN_API_KEY or supply --admin-api-key."
  }
}
```

An unhealthy readiness check can write a response to standard output and an error to standard error. Check the exit code before interpreting success. Help remains plain text.

| Exit code | Meaning |
| --- | --- |
| 0 | Success or help |
| 1 | Request, response, health, or operation failure |
| 2 | Invalid arguments/configuration or missing credentials |
| 130 | Interrupted |

Error kinds are `usage`, `request`, `operation`, and `interrupted`. Use JSON field names in scripts rather than parsing display tables. The [CLI reference](../../reference/generated/cli/) contains full command help.

## Use an administrator client

Operator Python applications use a separate client type:

```python
import os

from lyra.api import LyraAdminClient

admin = LyraAdminClient(
    "lyra.example.com",
    admin_api_key=os.environ["LYRA_ADMIN_API_KEY"],
)
status = admin.status()
workers = admin.workers.list()
```

`AsyncLyraAdminClient` provides the asynchronous equivalent. Both expose `health`, `jobs`, `plugins`, `catalog`, `workers`, `queues`, and `routing`. Consumer job applications use `LyraClient` and the agent key instead.
