---
title: Configure plugin data resources
description: Supply named resource settings and worker credentials independently of metric parameters.
contentType: How-to
contentPlan: operate/resources
sidebar: { label: "Configure Plugin Data Resources" }
---

Configure each plugin resource using the name and model declared by that plugin. The city example reads bundled data and requires no settings or secrets.

## Configure plugin resources

Use `[resources.resource_name.config]` for settings required by a plugin’s [input/output (I/O) resources](../../plugins/runtime/#define-an-io-resource). Match the name registered by the plugin. For example, configure the `shared_files` resource’s directory:

```toml
[resources.shared_files.config]
directory = "/data"
```

For a resource named `remote` that declares a `token` secret, reference an environment variable:

```toml
[resources.remote.secrets.token]
env = "REMOTE_ACCESS_TOKEN"
```

Set `REMOTE_ACCESS_TOKEN` on every worker that runs the resource. To use a credential file instead, replace that secret entry with:

```toml
[resources.remote.secrets.token]
file = "/lyra_data/secrets/remote-token"
```

Mount the UTF-8 credential file on each worker that needs it. Each secret entry accepts exactly one source: `env` or `file`.

During a job, Lyra validates each requested resource’s settings, resolves its credentials, and initializes it. This applies to both location providers and calculation resources. Offline configuration validation checks the TOML structure without reading credentials or checking plugin-specific settings.

## Select a location provider

Enable exactly one plugin that declares a location provider. Lyra selects its named resource automatically and rejects multiple enabled provider declarations.

Use the resource name from the plugin declaration in `[resources.resource_name.config]` and `[resources.resource_name.secrets]`. Supply its credentials to workers that resolve locations. You can inspect provider metadata without contacting its data sources.
