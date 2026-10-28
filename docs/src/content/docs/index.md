---
title: What is Lyra?
description: Run spatial calculations in Python or share them through a job API.
contentType: Landing
contentPlan: index
sidebar: { label: "What Is Lyra?" }
---

Lyra lets you turn a Python calculation into a reusable [metric](./concepts/model/#a-metric-is-a-public-calculation). You describe the inputs it accepts and the tables it returns. People can then discover the metric, supply their own inputs, and run it.

For example, a population metric might accept a region and a census year. It could return a table of population by area and a second table containing the total. The calculation and its data sources belong to the plugin author. Lyra handles the input contract and execution.

## Start with a calculation

You can run a metric directly in a Python script or notebook. Local execution returns Pandas or GeoPandas tables in the current process. It is a useful starting point for learning Lyra, developing a plugin, or analyzing data yourself.

[Run your first metric locally](./start/local/) uses a small calculation that needs no database, credentials, or server.

## Share metrics through a server

A Lyra server publishes a catalog of available metrics. A client chooses one, submits a job, checks its status, and downloads the result. Workers execute the calculation in the background, so a client does not need to keep a request open while it runs.

You can connect through the Python client, HTTP API, or an AI assistant using the Model Context Protocol (MCP). Server jobs and their results are retained for a configured period.

If you already have a server address and an agent key, [run your first server job](./quickstart/).

## Find your path

Choose the guide that matches what you want to accomplish:

Choose the guide that matches what you want to accomplish:

| You want to… | Start here |
| --- | --- |
| Decide between local execution and a server | [Choose how to run Lyra](./start/choose-execution/) |
| Run an existing metric from Python | [Use the Python client](./use/python-client/) |
| Expose your own calculation | [Explore the example plugin](./plugins/quickstart/) |
| Set up a shared service | [Prepare a deployment](./operate/requirements/) |
| Understand the moving parts | [How metrics and plugins fit together](./concepts/model/) |
| Look up a field, method, or command | [Reference](./reference/) |

## What you bring

Lyra supplies the execution framework and the contracts around it. Plugins supply the calculations and access to their data. A server deployment requires Redis and PostGIS; add Earth Engine when your plugins need it. [Prepare a deployment](./operate/requirements/) explains the requirements before you start containers.
