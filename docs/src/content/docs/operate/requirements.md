---
title: Prepare a deployment
description: Gather the services, spatial data, credentials, and plugin dependencies a server needs.
contentType: How-to
contentPlan: operate/requirements
sidebar: { label: "Prepare A Deployment" }
---

Prepare an API, Redis, and worker pools for your Lyra server. Add PostgreSQL with PostGIS when your location provider or metrics need database access. Gather the services, credentials, and plugin sources below before starting containers.

If you only want to try a calculation, the [local tutorial](../../start/local/) needs none of this infrastructure.

## Gather the required inputs

Prepare the services, credentials, and storage that the API and workers share:

| Input | What to prepare |
| --- | --- |
| Container tools | Docker with Compose and support for named build contexts |
| Application image | Lyra and selected plugins installed from a locked dependency set |
| Redis | A reachable instance for queues, job metadata, and retained results |
| PostGIS | A reachable database if you use database location provider or metrics that request the `database` resource |
| Earth Engine | Optional: a project and service-account JSON with access to the assets your plugins need |
| API credentials | A random agent key and a different random administrator key |
| Configuration | A `lyra.toml` shared by API and workers |
| Storage | A writable, persistent `/lyra_data` volume |

The example Compose project starts Redis. If you need PostGIS, [configure PostgreSQL access](../configuration/#configure-postgresql-access) separately; every worker then tests the connection at startup. For Earth Engine metrics, [configure the project and credentials](../configuration/#enable-earth-engine-for-workers).

## Prepare spatial data

The configured location provider owns its datasets, identifier rules, unit types,
and coordinate reference systems. Prepare its data sources and named resource
configuration according to that provider's documentation. Requests select exact
IDs within a declared collection and unit type.

Dataset loading and provisioning are managed separately from Lyra. The generic
`[database]` configuration is available for calculation resources and API queries;
location providers own their acquisition settings through named resources.

## Prepare plugin sources

The deployment image must include every enabled plugin and all transitive graph dependencies. Plugins may need additional database views, external datasets, native libraries, or private asset permissions.

The population views in the example plugin are illustrative sources. Configure their data sources according to the plugin’s requirements.

## Choose your deployment project

The repository includes `examples/lyra-deployment`, with production and editable development configurations. It is the starting point for [Deploy Lyra](../deployment/).

For an independent deployment repository, adapt source paths and container layout together. Pin production Git sources to full commits and commit the deployment lockfile. Review [plugin installation](../plugins/) when adding another package.
