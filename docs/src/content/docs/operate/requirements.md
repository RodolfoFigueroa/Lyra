---
title: Prepare a deployment
description: Gather the services, spatial data, credentials, and plugin dependencies a server needs.
contentType: How-to
contentPlan: operate/requirements
sidebar: { label: "Prepare A Deployment" }
---

Gather the services, credentials, storage, and plugins for your Lyra server before starting containers. You need an API, Redis, and worker pools. Add data services, such as PostgreSQL with PostGIS, when a plugin requires them.

If you only want to try a calculation, the [local tutorial](../../start/local/) needs none of this infrastructure.

## Gather the required inputs

Prepare the services, credentials, and storage that the API and workers share:

| Input | What to prepare |
| --- | --- |
| Container tools | Docker with Compose and support for named build contexts |
| Application image | Lyra and selected plugins installed from a locked dependency set |
| Redis | A reachable instance for queues, job metadata, and retained results |
| PostGIS | A reachable database when a plugin-owned resource requires it |
| API credentials | A random agent key and a different random administrator key |
| Configuration | A `lyra.toml` shared by API and workers |
| Storage | A writable, persistent `/lyra_data` volume |

The example Compose project starts Redis. Configure plugin data sources through [named resources](../configuration/#configure-plugin-resources); workers connect to those sources during job execution.

## Prepare spatial data

The configured location provider owns its datasets, identifier rules, unit types, and coordinate reference systems. Prepare its data sources and named resource configuration according to that provider's documentation. Requests select exact IDs within a declared collection and unit type.

Plugins own dataset loading, provisioning, and acquisition settings through named resources.

## Prepare plugin sources

The deployment image must include every enabled plugin and all transitive graph dependencies. Plugins may need additional database views, external datasets, native libraries, or private asset permissions.

The population views in the example plugin are illustrative sources. Configure their data sources according to the plugin’s requirements.

## Choose your deployment project

The repository includes `examples/lyra-deployment`, with production and editable development configurations. It is the starting point for [Deploy Lyra](../deployment/).

For an independent deployment repository, adapt source paths and container layout together. Pin production Git sources to full commits and commit the deployment lockfile. Review [plugin installation](../plugins/) when adding another package.
