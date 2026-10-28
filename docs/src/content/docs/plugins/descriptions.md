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
| Output column | What a value means, including its unit or scale |

Begin the metric description with a useful summary. Catalog listings abbreviate descriptions, while individual metric inspection returns the full metadata.

Explain sources, formulas, thresholds, dates, resolution, or limitations when they affect interpretation. These are possible topics, not a required checklist. A source link can support an explanation; keep essential meaning in the description itself.

## Make distinctions concrete

For example, “Population” leaves several questions open. “Residents in the entire census areas that intersect the selected region” tells a caller how to interpret totals near a selection boundary.

A year parameter should explain whether it selects a census observation, an imagery composite, or another temporal concept. A score column should describe its scale. Explain established missing-value meanings when they matter.

Keep each explanation in its relevant field instead of repeating the whole methodology across metric, parameter, and column descriptions.

## Keep operational details in their guides

Keep routine reprojection steps, internal band names, unused attributes, and error propagation in implementation documentation. Bring those details into catalog metadata when they affect how a caller chooses inputs or interprets results.

Access requirements belong in deployment documentation. Incomplete testing and an agent’s verification status belong in the handoff. Preserve actual input requirements, indicator-defining formulas, and known interpretation limits.

Missing documentation does not establish a coverage restriction or universal validity. Resolve material ambiguity or conflicting evidence before writing claims that depend on it.

## Review the catalog on its own

Generate the manifest and run `lyra-plugin describe`. Read those descriptions without relying on a repository README.

Check whether a caller can answer:

- Is this the calculation I need?
- Which inputs should I choose?
- What do these outputs mean?
- Which known limitations affect my use?

Compare the answers with evidence from the workflow, tests, and source documentation. Remove irrelevant detail and restore missing essential context. Automated schema checks cannot establish scientific completeness.

For adapting an existing workflow with a coding agent, continue to [agent-assisted authoring](../agent-assisted-authoring/).
