---
title: What is Lyra?
description: Run spatial calculations in Python or share them through a job API.
contentType: Landing
contentPlan: index
sidebar: { label: "What Is Lyra?" }
---

Lyra lets you turn a Python calculation into a reusable [metric](./concepts/model/#a-metric-declares-a-calculation). You describe the inputs it accepts and the tables it returns. People can then discover the metric, supply their own inputs, and run it.

For example, a city maintenance report needs to identify streets in poor condition and measure how much road needs work. The city plugin lets you choose streets and a condition threshold, then returns their maintenance workload. A separate calculation divides that workload between city blocks.

You write the calculations and choose their data sources. Lyra checks the inputs and results, runs any calculations they depend on, and makes them available through a common interface.

## Start with a calculation

You can run a metric directly in a Python script or notebook. Local execution returns pandas or GeoPandas tables in the current process. It is a useful starting point for learning Lyra, developing a plugin, or analyzing data yourself.

[Run your first metric locally](./start/local/) runs a supplied city plugin that needs no database, credentials, or server.

## Share metrics through a server

A Lyra server publishes a catalog of available metrics. A client chooses one, submits a job, checks its status, and downloads the result. Workers execute the calculation in the background, so a client does not need to keep a request open while it runs.

You can connect through the Python client, HTTP API, or an AI assistant using the Model Context Protocol (MCP). The server retains jobs and results for a configured period.

If you already have a server address and an agent key, [run your first server job](./quickstart/).

## Find your path

Choose the guide that matches what you want to accomplish:

| You want to… | Start here |
| --- | --- |
| Decide between local execution and a server | [Choose how to run Lyra](./start/choose-execution/) |
| Run an existing metric from Python | [Use the Python client](./use/python-client/) |
| Expose your own calculation | [Build a city plugin](./plugins/tutorial/) |
| Set up a shared service | [Prepare a deployment](./operate/requirements/) |
| Understand metrics, plugins, and jobs | [How metrics and plugins fit together](./concepts/model/) |
| Look up a field, method, or command | [Reference](./reference/) |

## What you bring

To try the tutorials, you need Python, uv, and a Lyra checkout; the city example includes its data. Your own plugins declare the data sources, calculation dependencies, and credentials they need. A server deployment also needs Redis and access to those sources. Follow [Prepare a deployment](./operate/requirements/) before starting containers.
