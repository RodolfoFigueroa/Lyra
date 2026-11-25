---
title: Prepare a deployment
description: Gather the services, spatial data, credentials, and plugin dependencies a server needs.
contentType: How-to
contentPlan: operate/requirements
sidebar: { label: "Prepare A Deployment" }
---

Configure the API and workers to use the same Valkey instance. Add a data service, such as PostgreSQL with PostGIS, if one of your plugins needs it.

If you only want to try a calculation, the [local tutorial](../../start/local/) needs none of this infrastructure.

## Gather the required inputs

Prepare the services, credentials, and storage that the API and workers share:

| Input | What to prepare |
| --- | --- |
| Container tools | Docker with Compose and support for named build contexts |
| Application image | Lyra and selected plugins installed from a locked dependency set |
| Valkey | Version 7.2 or later, reachable from the API and workers. Valkey stores queued jobs, progress, worker heartbeats, and temporary diagnostics. |
| PostGIS | A reachable database when a plugin-owned resource requires it |
| API credentials | A random agent key and a different random administrator key |
| Configuration | A `lyra.toml` shared by API and workers |
| Storage | A writable, persistent `/lyra_data` volume shared by the API and workers for durable job history, execution reports, and result tables |

The example Compose project starts Valkey for you. Configure any plugin data sources through [named resources](../configuration/#configure-plugin-resources). Workers connect to those sources when they run a job.

## Prepare spatial data

Prepare the dataset for each [location provider](../../plugins/location-providers/) and configure its named resource. Follow the provider’s rules for record IDs and its coordinate reference system. Callers select records by sending the public provider ID and exact IDs from that dataset.

## Prepare plugin sources

The deployment image must include every enabled plugin and all transitive graph dependencies. Plugins may need additional database views, external datasets, native libraries, or private asset permissions.

The city example ships its geometry, observations, and membership data. It needs no external data service. Configure additional sources according to each plugin’s requirements.

## Choose your deployment project

Start with `examples/lyra-deployment` to [deploy Lyra with Docker Compose](../deployment/).

For an independent deployment repository, adapt source paths and container layout together. Pin production Git sources to full commits and commit the deployment lockfile. Review [plugin installation](../plugins/) when adding another package.
