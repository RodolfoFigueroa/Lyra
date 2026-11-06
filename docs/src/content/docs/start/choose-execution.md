---
title: Choose how to run Lyra
description: Decide whether to run a calculation in your own Python process or submit a server job.
contentType: Conceptual
contentPlan: start/choose-execution
sidebar: { label: "Choose How To Run Lyra" }
---

Start locally if you want to try Lyra or develop a calculation: your Python call returns the result directly. Use a server when people or applications need to submit work to a shared service. Both paths run the same metric definitions, so you can develop your plugin locally before sharing it.

## Run in your Python process

Use `LocalRunner` when you are developing a plugin, working in a notebook, or calling a calculation from another Python application.

Install `lyra[runtime]` alongside your plugin and its dependencies. When the calculation finishes, your call returns a dictionary of pandas or GeoPandas tables. You choose the data sources, and the runner manages their setup and cleanup.

The [first local tutorial](../local/) uses bundled files and needs no external services. For installed plugins and custom data sources, continue to [local execution](../../use/local-execution/).

## Submit to a server

Use a server when several people or applications need a shared metric catalog and background workers. The operator installs plugins and configures their data sources. Callers need the server address and an agent key.

The server checks the request, selects the calculation and data versions to use, and queues a job. It returns a job ID while a worker loads the selected locations and runs the calculation. You use the job ID to check progress, then download the result when the job finishes. The server converts tables into a JSON response; the Python client can reconstruct them as tables.

[Run your first server job](../../quickstart/) assumes a working server. If you will operate it yourself, start with [deployment requirements](../../operate/requirements/).

## Compare the two paths

Use these differences to choose where your calculation and its resources should live:

| Question | Local execution | Server job |
| --- | --- | --- |
| Where does the calculation run? | Your Python process | An operator-managed worker |
| What do you install? | `lyra[runtime]`, your plugin, and its dependencies | `lyra[client]` for Python, or an HTTP or Model Context Protocol (MCP) client |
| How do you supply a region? | Select locations by dataset, feature type, and IDs | Select locations by dataset, feature type, and IDs |
| When do you receive the result? | When the call returns | After checking that the job has finished |
| What do you receive? | A dictionary of pandas or GeoPandas tables | JSON; optionally reconstructed tables |
| Who manages source connections? | The runner and plugin | The deployment and plugin |
| Are results retained? | Save them in your application | For the server’s retention period |

A metric can need external data in either mode. Running locally does not make an external source available automatically. Start with the bundled city example to explore Lyra before connecting your own sources.
