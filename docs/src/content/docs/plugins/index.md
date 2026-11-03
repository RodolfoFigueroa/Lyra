---
title: Build plugins
description: Choose a guided plugin tutorial or an independent authoring task.
contentType: Landing
contentPlan: plugins/index
sidebar: { label: "Choose An Authoring Guide" }
---

A plugin makes your Python calculations available for other people to discover and run through Lyra. You describe their inputs and result tables, and connect calculations when one needs another’s output. Start with the guided city project if you’re new to Lyra, or choose an individual guide for a change to an existing plugin.

## Build your first plugin

[Build a city analysis plugin](./tutorial/) develops a street maintenance report across eight runnable chapters. You’ll measure streets, decide which need maintenance, and share that workload between blocks. The project includes the data, calculation helpers, and tests. You can run it without services or credentials.

## Adapt or extend a calculation

Use the guide matching your task:

| Task | Guide |
| --- | --- |
| Accept settings and selected locations | [Define metric inputs](./authoring/) |
| Describe and validate returned tables | [Declare output tables](./outputs/) |
| Reuse another metric’s table | [Connect metric dependencies](./dependencies/) |
| Find or construct locations for a calculation | [Location operations](./location-operations/) |
| Resolve IDs from your own source | [Location providers](./location-providers/) |
| Supply data clients, settings, or credentials | [Runtime services and resources](./runtime/) |
| Check behavior without services | [Plugin tests](./testing/) |
| Give an operator a verified package | [Package a plugin](./publish-and-debug/) |
| Adapt a workflow with a coding agent | [Agent-assisted authoring](./agent-assisted-authoring/) |

For shared vocabulary, read [metrics and plugins](../concepts/model/). Look up exact supported values in [reference](../reference/). Operators [install and enable packages](../operate/plugins/) independently of authoring.
