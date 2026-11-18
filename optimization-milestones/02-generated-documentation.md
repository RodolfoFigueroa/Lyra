# 02 · Generate documentation from the application

The downloadable OpenAPI contract and the generated HTTP guide must describe every HTTP operation included in the application’s OpenAPI schema, including retained results and execution reports. Add a shared `register_http_routes(app)` function for the server and the schema-only FastAPI app in `docs/scripts/generate_docs.py`. The generator can call `openapi()` offline, without entering the application lifespan, loading plugins, acquiring credentials, or connecting to Redis.

## Rendering and publication

Parse delimited Markdown frontmatter with the existing `yaml.safe_load`. Preserve the required title and description, the Markdown body, and the source path in errors for malformed or incomplete metadata. A folded multiline description should yield the YAML string value.

Render the entire generation in a temporary directory under the existing writer lock. A rendering error leaves the last published generation intact. Once rendering succeeds, replace the owned `reference/generated` tree and publish the five named `PUBLIC_FILES`. Keep the ownership and path checks, including symlink handling. Replacing the tree removes stale pages while preserving unrelated content.

Remove the backup tree and inverse renames. A filesystem error during publication fails the build and may leave partial generated output. Rerunning generation repairs it before Astro builds the site. Keep `filelock` in the development group and remove it from the `runtime` extra, bootstrap diagnostics, and runtime dependency test inventories.

## Verification

Compare path and HTTP method sets from the application’s OpenAPI schema and the generated contract in the existing docs contract checks. Use temporary files to exercise folded and malformed YAML, preservation of published files on rendering failure, stale-page removal, and recovery by rerunning after a publication failure. The comparison should continue to work when a router gains or loses an endpoint.
