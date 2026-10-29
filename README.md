# Lyra

Lyra turns Python calculations, including spatial analyses, into reusable metrics.
Plugins define the calculations, their inputs and outputs, and access to data.
Lyra validates those contracts and lets you run metrics in your own Python process
or share them through a server with background jobs.

## Try it locally

You need Python 3.11 or later, uv, and a checkout of this repository. From the
repository root, prepare the environment:

```bash
uv sync
```

Then [run your first metric locally](https://rodolfofigueroa.github.io/lyra/latest/start/local/).
The tutorial defines a small calculation and returns a pandas table without a
database, credentials, or server. Local execution also works in notebooks and
Python applications; additional resources depend on the metrics you run.

To use Lyra in another Python project, follow the
[installation guide](https://rodolfofigueroa.github.io/lyra/latest/start/installation/).

## Use a shared server

A server provides a metric catalog and background jobs through the Python client,
REST API, or Model Context Protocol (MCP). With a server address and an agent key,
[submit your first server job](https://rodolfofigueroa.github.io/lyra/latest/quickstart/).

To operate your own server, start with the
[deployment requirements](https://rodolfofigueroa.github.io/lyra/latest/operate/requirements/)
and [deployment guide](https://rodolfofigueroa.github.io/lyra/latest/operate/deployment/).
Server execution requires Redis. PostGIS, Earth Engine, and other data sources
depend on your configured plugins and resources.

## Documentation

The guides below track main and may include unreleased changes. For the latest
release, open the [released documentation](https://rodolfofigueroa.github.io/lyra/).

- [Use the REST API](https://rodolfofigueroa.github.io/lyra/latest/use/rest-api/)
- [Use the Python client](https://rodolfofigueroa.github.io/lyra/latest/use/python-client/)
- [Connect through MCP](https://rodolfofigueroa.github.io/lyra/latest/use/mcp/)
- [Build a plugin](https://rodolfofigueroa.github.io/lyra/latest/plugins/quickstart/)
- [Administrative CLI](https://rodolfofigueroa.github.io/lyra/latest/operate/admin-cli/)
- [Reference](https://rodolfofigueroa.github.io/lyra/latest/reference/)
- [Contribute to Lyra](CONTRIBUTING.md)

When a server is running, its exact OpenAPI contract is also available at
`/openapi.json`, `/docs`, and `/redoc`.
