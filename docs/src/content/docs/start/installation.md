---
title: Install Lyra for Python
description: Prepare the repository environment or add the SDK and client to another Python project.
contentType: How-to
contentPlan: start/installation
sidebar: { label: "Install Lyra For Python" }
---

Use the repository environment to follow the tutorials, or add Lyra’s component packages to an existing Python project. You need Python 3.11 or later and [uv](https://docs.astral.sh/uv/getting-started/installation/) for the commands below.

## Prepare the repository environment

From the root of a Lyra checkout, synchronize its dependencies:

```sh
uv sync
```

This environment includes the software development kit (SDK), Python client, and tutorial dependencies. You can now [run your first metric locally](../local/) without a server or external data.

## Add Lyra to an existing project

The release pipeline publishes component build artifacts rather than PyPI packages. If you’re working from source, use a checkout of the product release or commit you want to run.

From your own uv-managed project, add the SDK and client together, replacing `/your_lyra_checkout_here` with the absolute checkout path:

```sh
uv add /your_lyra_checkout_here/packages/lyra_sdk \
  /your_lyra_checkout_here/packages/lyra_api
```

Adding both paths lets uv resolve the client’s SDK dependency from the same checkout. Commit your project’s dependency declarations and lockfile, and keep that checkout available while the source dependencies reference it.

For plugin development without a server client, add only the SDK:

```sh
uv add /your_lyra_checkout_here/packages/lyra_sdk
```

Your plugin also needs every library its calculation imports, such as Pandas. Add `lyra-utils` from the same checkout when you use its geometry or Earth Engine helpers.

## Choose your next task

With the packages installed, you can [run a calculation in your process](../local/) or [connect the Python client to a server](../../use/python-client/). Server deployment has additional [service and data requirements](../../operate/requirements/).
