# Diagram preview

Open the [diagram preview](preview.md) on GitHub or in a Markdown viewer that supports Mermaid. This manifest-only fixture needs no plugin installation or services.

Export current renderer output from the repository root:

```sh
uv run --no-sync lyra-plugin diagram \
  --project examples/diagram-preview --detailed \
  --output /tmp/diagram-preview.mmd
```

Paste that file into [Mermaid Live](https://mermaid.live) to view renderer changes.
