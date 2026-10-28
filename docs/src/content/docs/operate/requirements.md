---
title: Prepare a deployment
description: Gather the services, spatial data, credentials, and plugin dependencies a server needs.
contentType: How-to
contentPlan: operate/requirements
sidebar: { label: "Prepare A Deployment" }
---

A Lyra server consists of an API, Redis, and worker pools, with access to PostGIS and configured data sources. Gather those inputs before starting containers.

If you only want to try a calculation, the [local tutorial](../../start/local/) needs none of this infrastructure.

## Gather the required inputs

Prepare the services, credentials, and storage that the API and workers share:

| Input | What to prepare |
| --- | --- |
| Container tools | Docker with Compose and support for named build contexts |
| Application image | Lyra and selected plugins installed from a locked dependency set |
| Redis | A reachable instance for queues, job metadata, and retained results |
| PostGIS | A reachable database with the spatial data your selectors need |
| Earth Engine | A project and readable service-account JSON, with required asset permissions |
| API credentials | A random agent key and a different random administrator key |
| Configuration | A `lyra.toml` shared by API and workers |
| Storage | A writable, persistent `/lyra_data` volume |

The example Compose project starts Redis. Supply PostGIS and Earth Engine access separately. Normal workers probe PostGIS and initialize Earth Engine at startup, even when the first metric is a calculation that only returns a table.

## Prepare spatial data

Lyra does not bundle or populate census and metropolitan-zone data. Arrange the source imports with whoever manages the database.

Metropolitan-zone selection uses `metropoli_2020`, `census_2020_mun`, and `census_2020_ageb`. Their identifiers and relationships must support the selected zone, such as `09.01` in the server tutorial. Name lookup also uses PostgreSQL trigram similarity.

Census-code selection uses the corresponding level table: `census_2020_ent`, `census_2020_mun`, `census_2020_loc`, `census_2020_ageb`, or `census_2020_mza`. Geometry columns must carry the correct spatial reference.

Readiness checks database reachability. It does not prove that every required table or geographic code has been imported. Explicit GeoJSON avoids a spatial lookup for that request, while the deployment still requires PostGIS readiness.

## Prepare plugin sources

The deployment image must include every enabled plugin and all transitive graph dependencies. Plugins may need additional database views, external datasets, native libraries, or private asset permissions.

The population views in the example plugin are illustrative sources. They are separate from the platform’s census lookup tables.

## Choose your deployment project

The repository includes `examples/lyra-deployment`, with production and editable development configurations. It is the starting point for [Deploy Lyra](../deployment/).

For an independent deployment repository, adapt source paths and container layout together. Pin production Git sources to full commits and commit the deployment lockfile. Review [plugin installation](../plugins/) when adding another package.
