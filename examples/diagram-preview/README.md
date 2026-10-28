# Diagram preview

Open [preview.md](preview.md) on GitHub or in a Mermaid-enabled Markdown preview.
This manifest-only fixture needs no plugin installation or services.

Export current renderer output from the repository root:

```sh
uv run --no-sync lyra-plugin diagram --project examples/diagram-preview --detailed --output /tmp/diagram-preview.mmd
```

Paste that file into [Mermaid Live](https://mermaid.live) to view renderer changes.
