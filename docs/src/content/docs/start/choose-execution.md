---
title: Choose how to run Lyra
description: Decide whether to run a calculation in your own Python process or submit a server job.
contentType: Conceptual
contentPlan: start/choose-execution
sidebar: { label: "Choose How To Run Lyra" }
---

Lyra supports local execution and server jobs. Both use the same metric definitions, parameter models, and declared outputs. Choose based on where the calculation should run and how you want to receive its results.

## Run in your Python process

Use `LocalRunner` when you are developing a plugin, working in a notebook, or calling a calculation from another Python application.

You install the software development kit (SDK) and the plugin’s dependencies in your environment. A call runs synchronously and returns a dictionary of native DataFrames or GeoDataFrames. You own any data-source clients that the calculation needs.

The [first local tutorial](../local/) works entirely in memory. For installed plugins and custom data sources, continue to [local execution](../../use/local-execution/).

## Submit to a server

Use a server when several people or applications need a shared metric catalog and background workers. The operator installs plugins and configures their data sources. Callers need the server address and an agent key.

Submitting a job returns an identifier immediately. You check that identifier until the job finishes, then download its retained result. The server converts tables into a JSON response; the Python client can reconstruct them as tables.

[Run your first server job](../../quickstart/) assumes a working server. If you will operate it yourself, start with [deployment requirements](../../operate/requirements/).

## Compare the two paths

Use these differences to choose where your calculation and its resources should live:

Use these differences to choose where your calculation and its resources should live:

| Question | Local execution | Server job |
| --- | --- | --- |
| Where does the calculation run? | Your Python process | An operator-managed worker |
| What do you install? | SDK, plugin, and calculation dependencies | A client, or use HTTP or the Model Context Protocol (MCP) |
| How do you supply a region? | Resolved GeoJSON | A supported spatial selector |
| When do you receive the result? | When the call returns | After polling the background job |
| What do you receive? | Native named tables | JSON; optionally reconstructed tables |
| Who manages source connections? | Your application | The deployment and plugin |
| Are results retained? | Your application owns them | For the server’s retention period |

A metric can need external data in either mode. Running locally does not make an external source available automatically. Start with an in-memory metric if you want to explore the SDK before setting up sources.
