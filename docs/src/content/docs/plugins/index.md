---
title: Build plugins
description: Choose a guided plugin tutorial or an independent authoring task.
contentType: Landing
contentPlan: plugins/index
sidebar: { label: "Choose An Authoring Guide" }
---

A plugin exposes your calculation through declared inputs, named result tables, and explicit dependencies. Choose a guided project when learning Lyra or an independent guide when changing an existing calculation.

## Build your first plugin

[Build a city analysis plugin](./tutorial/) develops a street maintenance report across eight runnable chapters. The standalone project ships its geometry, observations, membership tables, tests, and manifest. You can run it without services or credentials.

## Adapt or extend a calculation

Use the guide matching your task:

| Task | Guide |
| --- | --- |
| Declare ordinary inputs and spatial arguments | [Define metric inputs](./authoring/) |
| Describe and validate returned tables | [Declare output tables](./outputs/) |
| Reuse another metric’s table | [Connect metric dependencies](./dependencies/) |
| Derive an internal location input | [Location operations](./location-operations/) |
| Resolve IDs from your own source | [Location providers](./location-providers/) |
| Inject data access, configuration, or credentials | [Runtime services and resources](./runtime/) |
| Check behavior without services | [Plugin tests](./testing/) |
| Give an operator a verified package | [Package a plugin](./publish-and-debug/) |
| Adapt a workflow with a coding agent | [Agent-assisted authoring](./agent-assisted-authoring/) |

For shared vocabulary, read [metrics and plugins](../concepts/model/). Look up exact supported values in [reference](../reference/). Operators [install and enable packages](../operate/plugins/) independently of authoring.
