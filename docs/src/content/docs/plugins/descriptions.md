---
title: Write useful metric descriptions
description: Give consumers enough context to choose inputs and interpret results from the catalog.
contentType: How-to
contentPlan: plugins/descriptions
sidebar: { label: "Write Useful Metric Descriptions" }
---

A caller should be able to choose a metric, supply meaningful inputs, and interpret its tables from the catalog. Write descriptions for those decisions.

## Give each field a purpose

Write each description around the decision that its reader needs to make:

| Description | Explain |
| --- | --- |
| Metric | What the calculation measures and how to interpret its overall result |
| Parameter | What the caller chooses and how that choice affects the calculation |
| Output schema | Declared `meaning` and `granularity`, including what one row represents |
| Output column | What a value means, including its unit or scale |
| Edition metadata | Source releases, observation periods, and fixed methodological choices |
| Location contract | Source meaning, field definitions, geographical edition, and revision coverage |

Begin the metric description with a useful summary. Catalog listings abbreviate descriptions, while individual metric inspection returns the full metadata.

Explain sources, formulas, thresholds, dates, resolution, or limitations when they affect interpretation. These are possible topics, not a required checklist. A source link can support an explanation; keep essential meaning in the description itself.

## Make distinctions concrete

Describe the measured quantity precisely enough to explain the result. For example, “Population” leaves the coverage unclear. “Residents in the entire census areas that intersect the selected region” tells a caller how to interpret totals near a selection boundary.

A year parameter should explain whether it selects a census observation, an imagery composite, or another temporal concept. A score column should describe its scale. Explain established missing-value meanings when they matter.

Keep each explanation in its relevant field instead of repeating the whole methodology across metric, parameter, and column descriptions. Shared structure does not make observations or statistical definitions comparable. Preserve source editions, observation periods, releases, and geographical editions as separate metadata.

Describe a metric and each operation once. The catalog derives accepted selections from typed routes; authors need no description for every metric/route pair. Keep operation descriptions as relationship metadata without inventing containment or equivalence claims. Selecting a broader source does not change the metric’s declared output granularity.

## Keep operational details in their guides

Keep routine reprojection steps, internal band names, unused attributes, and error propagation in implementation documentation. Bring those details into catalog metadata when they affect how a caller chooses inputs or interprets results.

Access requirements belong in deployment documentation. Incomplete testing and an agent’s verification status belong in the handoff. Preserve actual input requirements, indicator-defining formulas, and known interpretation limits.

Missing documentation does not establish a coverage restriction or universal validity. Resolve material ambiguity or conflicting evidence before writing claims that depend on it.

## Review the catalog on its own

Generate the manifest and run `lyra-plugin describe`. Read those descriptions without relying on a repository README.

Check that the catalog explains:

- The calculation’s purpose
- Which inputs to choose
- The meaning of each output
- Known limitations that affect use

Compare the answers with evidence from the workflow, tests, and source documentation. Remove irrelevant detail and restore missing essential context. Automated schema checks cannot establish scientific completeness.

For adapting an existing workflow with a coding agent, continue to [agent-assisted authoring](../agent-assisted-authoring/).
