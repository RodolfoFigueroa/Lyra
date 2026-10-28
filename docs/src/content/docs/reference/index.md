---
title: Find a contract
description: Look up exact Python, HTTP, configuration, command-line, and MCP interfaces.
contentType: Landing
contentPlan: reference/index
sidebar: { label: "Find A Contract" }
---

Use reference pages when you know what you want to look up. For a first run, start with the [first local metric](../start/local/) or [first server job](../quickstart/) tutorial.

## Choose a reference

Select the interface or contract you need to inspect:

| Reference | What it contains |
| --- | --- |
| [Python APIs](../api/lyra/) | SDK, client, and utility modules, signatures, types, and docstrings |
| [HTTP API](./generated/http/) | Endpoints, authentication, operations, and the full OpenAPI download |
| [Configuration](./generated/configuration/) | TOML fields, environment variables, defaults, and constraints |
| [Command line](./generated/cli/) | Help for `lyra-plugin`, `lyra-admin`, and the worker launcher |
| [MCP tools](./generated/mcp/) | Tool inputs, outputs, annotations, and schema downloads |
| [Parameter and handler rules](./parameters/) | Supported models, value types, and signatures |
| [Output units](./units/) | Canonical unit names and meanings |
| [MCP payload examples](./mcp-payloads/) | Complete representative requests and responses |

In the Python tree, `lyra.sdk` is for plugin authors and local execution, `lyra.api` is for server clients, and `lyra.utils` contains calculation helpers. Configuration constraints and defaults are also preserved in the dedicated configuration reference.

## Download machine-readable contracts

Use these versioned exports when a tool needs the schemas or documentation text:

- [OpenAPI](../openapi.json) describes the HTTP API.
- [Configuration schema](../config.schema.json) describes settings.
- [MCP contracts](../mcp-tools.json) describe tools and serialization.
- [Documentation index](../llms.txt) and [complete guide text](../llms-full.txt) provide portable documentation exports.
- [Python API text](../api/lyra/llms.txt), [symbol index](../api/lyra/symbols.json), and [object inventory](../api/lyra/objects.inv) support API discovery.

Each Python module also offers a Markdown download. Machine-readable exports complement the readable guides and use the same documentation version.

## Where generated content comes from

HTTP, configuration, CLI, and MCP references are generated from their source contracts. Python API pages are generated from package signatures, types, docstrings, and Pydantic fields.

To correct generated information, update the owning contract or metadata. Contributor setup and documentation build procedures belong in [Contributing](https://github.com/RodolfoFigueroa/lyra/blob/main/CONTRIBUTING.md).
