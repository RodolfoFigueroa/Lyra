#import "style.typ": *
#set document(title: "Contract-driven geospatial indicators", author: "Rodolfo Figueroa Soriano")
#set page(paper: "us-letter", margin: 0.65in, fill: white,
  footer: context [#set text(size: 8pt, fill: muted)
    #grid(columns: (1fr, auto), [Conference companion · 4 November 2026], counter(page).display("1 / 6"))])
#set text(font: body-font, size: 11pt, fill: ink, lang: "en")
#set par(leading: 0.48em, justify: false, spacing: 0.7em)
#set heading(numbering: none)
#show heading.where(level: 1): set text(size: 22pt, fill: teal, weight: "bold")
#show heading.where(level: 2): set text(size: 13pt, fill: teal, weight: "bold")
#show raw: set text(font: code-font, size: 9pt)
#show raw.where(block: true): it => block(width: 100%, fill: rgb("#EEF1ED"), inset: 10pt, radius: 3pt, it)
#show link: set text(fill: teal)
#let caption(body) = text(size: 9pt, fill: muted, body)
#let label(body) = text(size: 9pt, fill: orange, weight: "bold", body)
#let box(body, fill: pale) = block(width: 100%, fill: fill, inset: 6pt, radius: 3pt, align(center, body))
#let arrow = block(width: 100%, above: 2pt, below: 2pt, align(center, text(size: 14pt, fill: teal)[↓]))
#let section(n, title) = [#label[COMPANION / #n] #v(4pt) #heading(level: 1, title)]
#let repo = "https://github.com/RodolfoFigueroa/lyra/"
#let docs = "https://rodolfofigueroa.github.io/Lyra/"

#label[CONFERENCE COMPANION]
= Contract-driven geospatial indicators
#text(size: 14pt)[A framework for shared research pipelines]
#v(6pt)
*Rodolfo Figueroa Soriano* \
Center for the Future of Cities · Tecnológico de Monterrey

Our framework turns Python calculations into reusable metrics with declared inputs, outputs, and dependencies. A researcher requests an indicator for a set of locations; the framework resolves the inputs, executes the required calculations, and returns named tables. The same plugin can run in a local Python process or through a shared service.

== The pipeline after the project
A research pipeline often outlives the project that produced it. Its code may remain available while the knowledge needed to run it stays with its author: which datasets to use, what each column means, which preparation steps matter, and how parameters affect the result. Reuse then depends on reconstructing that context.

Declaring the calculation's interface makes these requirements explicit. Developers retain their libraries, data sources, and scientific choices. Contracts describe how others can request the method and interpret its outputs. These declarations also give the executor the information it needs to connect calculations.

== A running example: access to jobs
Consider an accessibility score for each city block. The method combines working population, employment at job sites, and driving travel times. Jobs contribute less as travel time increases, while demand accounts for competition from the working population. A travel-time limit controls which opportunities enter the model.

#grid(columns: (1fr, 1fr), gutter: 20pt,
  [*Request* \
  Metric: job accessibility \
  Blocks: B01, B02, B03 \
  Travel-time limit: 30 minutes],
  table(columns: (1fr, 1fr), stroke: none, inset: 5pt,
    fill: (_, y) => if y == 0 { pale },
    [*Block*], [*Score*], [B01], [0.28], [B02], [0.63], [B03], [0.81]))
#caption[Illustrative output from the talk. Each row represents one block. A higher score indicates better access under this model; the score is not a percentage of jobs reachable.]

The request hides the coordination work, but the method still determines the answer. Travel mode, population definitions, source dates, distance decay, and normalization all affect interpretation. Comparisons need a consistent methodological context.

#table(columns: (1fr, 1fr), stroke: none, inset: 7pt,
  fill: (_, y) => if y == 0 { pale },
  [*The plugin author supplies*], [*The framework handles*],
  [Scientific calculations, data access, spatial relationships, and meaningful contracts.],
  [Contract validation, declared dependency execution, spatial input resolution, and result reuse.])

#pagebreak()
#section("02", "Declaring a reusable calculation")
A metric wraps a calculation in a declared interface. Its handler remains Python code, so an existing function can keep using GeoPandas, SQL, a remote data source, or a custom model. The declaration describes what the handler receives and which tables it returns.

```python
@metric(
    name="working_population",
    description="Working population",
    locations={"blocks": BLOCKS},
    outputs={"population": POPULATION},
)
def working_population(blocks: Locations):
    return calculate_working_population(
        blocks.to_geodataframe()
    )
```
#caption[Declaration excerpt from the accessibility example. Imports, BLOCKS, POPULATION, and the calculation function are defined elsewhere. Page 6 provides a complete runnable example.]

== Reading the contract
*Input.* `BLOCKS` identifies the location contract: block identifiers, geometry, and demographic attributes available to the calculation. A provider resolves requested identifiers against the corresponding data source. The handler receives those resolved locations.

*Output.* `POPULATION` declares a table with `block_id` as a string and `working_population` as an integer count. Its row meaning is one record per block. Types support validation; units and descriptions explain how a consumer should use the values.

*Table name.* `population` names the output table, rather than a column. A metric can return several named tables. A dependent calculation names the producer and the table it needs, then receives that table as an argument.

*Parameters.* Ordinary choices, such as a travel-time limit, have their own declarations. Defaults and validation make the effective request explicit. They should describe choices in the method, including units and acceptable values.

== The pieces of a plugin
#table(columns: (100pt, 1fr), stroke: none, inset: (x: 7pt, y: 6pt),
  fill: (_, y) => if calc.even(y) { pale },
  [*Metric*], [A calculation that accepts declared inputs and returns named tables.],
  [*Provider*], [An adapter that resolves identifiers into locations and attributes from a declared source.],
  [*Operation*], [A function that obtains related locations, such as streets surrounding blocks.],
  [*Dependency*], [An explicit connection to a table or locations needed by another calculation.],
  [*Plugin*], [A Python package whose factory registers its capabilities and required resources.])

Start with a calculation you already trust. Describe its interface, connect its dependencies, and register it in a plugin. Test its returned tables locally before making it available to other researchers. Contract validation checks structure; the author remains responsible for scientific validity and the meaning of the data.

#pagebreak()
#section("03", "Execution and reuse")
A request for the final indicator starts a traversal of its declared dependencies. The executor obtains the required inputs and supplies producer outputs to their consumers. Shared invocations can serve several consumers within the same run. Intermediate results are useful calculations in their own right.

#grid(columns: (1fr, 1fr, 1fr), gutter: 10pt,
  box([Demographics]), box([Job locations]), box([Road network]),
  arrow, arrow, arrow,
  box([Working population]), box([Jobs by destination]), box([Driving travel times]),
  arrow, arrow, arrow)
#box([*Demand and distance decay* \
Travel-time limit: 30 or 45 minutes], fill: warm)
#arrow
#box([*Accessibility score per block*], fill: warm)
#caption[All three intermediate tables feed the demand and decay calculation. Teal preparation can be reused when its inputs remain equivalent. Orange identifies the calculations affected by the limit change in this example.]

== Changing a parameter
Suppose the first request uses a 30-minute limit and the next uses 45 minutes. The population and jobs calculations do not depend on that limit. In this example, the stored driving-time table covers both thresholds, so it also remains useful. The demand and decay calculation and the final score run again using those saved inputs.

This boundary depends on the method. If the travel-time table had been truncated at 30 minutes, it would not contain the inputs needed for the second request. Declare parameters on the calculations they actually affect so that the graph expresses the correct dependency structure.

== Expanding a study area
A first request calculates locations A, B, and C. A later request asks for B, C, and D under the same parameters, data, and methodological context for the existing locations. The framework reuses B and C and computes D, returning the reused and new results together.

#grid(columns: (1fr, 1fr, 1fr), gutter: 10pt,
  box([*B* \
Reused]), box([*C* \
Reused]), box([*D* \
Computed], fill: warm))
#caption[Location-level reuse supports overlapping requests and expanding study areas. Matching identifiers alone do not establish that two calculations have equivalent inputs.]

== Keeping reuse meaningful
Persistent caching makes completed results available to subsequent executions. Reuse requires matching effective inputs and the relevant data and methodological context. Declared source revisions help distinguish datasets that have changed. Plugin authors must keep these declarations aligned with the data they represent.

Inspect execution observations to understand what ran and what was reused. The benefit is less repeated preparation when comparing scenarios; actual response time depends on the workload, source access, and available cached results.

#pagebreak()
#section("04", "Spatial selection and provenance")
A researcher may know the metropolitan area they want to study without knowing its block identifiers. Location operations connect that public selection to the spatial inputs a metric needs. The plugin defines the relationships and the algorithms that implement them.

#box([Metropolitan area → Municipalities → Census tracts → Blocks])
#v(5pt)
#box([Metropolitan area → Blocks], fill: warm)
#caption[Registered operations provide selection routes. A direct operation can shorten the route; the framework chooses a route with the fewest eligible operations. Both routes supply blocks to the same calculation.]

== Selection and calculation have different roles
Selecting Mexico City can resolve the blocks supplied to an accessibility metric. The output still has one row per block. Selection does not itself aggregate the indicator into a metropolitan score. Producing that score requires a calculation with an explicit aggregation method and output contract.

Operations can also provide locations inside a calculation. A block may need its surrounding streets, and a street may need intersecting streets. Several relationships can connect the same location types, including incoming, outgoing, or parallel streets. The dependency identifies the relationship the method needs.

These distinctions matter when joining results or comparing geographies. Read the output's identifiers and row meaning before assuming that its granularity matches the user's initial selection. Geographic boundaries, coordinate systems, source editions, and observation periods belong to the interpretation of the analysis.

== What produced this result?
Contracts explain what a method accepts and returns. Provenance adds context about an individual execution. The accepted selection and effective parameters establish the request, while observations distinguish execution from reuse.

#table(columns: (115pt, 1fr), stroke: none, inset: 7pt,
  fill: (_, y) => if calc.even(y) { pale },
  [*Selection*], [Blocks B01, B02, B03],
  [*Parameter*], [Travel-time limit: 45 minutes],
  [*Selection route*], [Accepted operations used to obtain native blocks],
  [*Source revisions*], [Declared revisions of the datasets used],
  [*Reused*], [Population, jobs, driving travel times],
  [*Executed*], [Demand, decay, final score])
#caption[Illustrative execution record for the 45-minute request. Values describe the example rather than a captured production run.]

Locally, call `runner.inspect()` after execution to examine provenance separately from the returned tables. Server results also expose provenance. Keeping this information separate lets ordinary analysis code work directly with pandas or GeoPandas outputs.

When an indicator changes unexpectedly, compare the effective parameters, source revisions, resolved geography, and observed execution. A recorded revision helps explain a result, but does not preserve the original dataset by itself. Historical reproduction also depends on continued access to the relevant data and implementation.

#pagebreak()
#section("05", "Research interfaces and applications")
The same declarations support different ways of working. A developer can run a plugin in a script or notebook and receive tables directly. A shared server makes configured methods and data sources available to colleagues through submitted jobs.

#box([*Python plugin* \
Contracts · Calculations · Data access])
#grid(columns: (1fr, 1fr), gutter: 14pt, arrow, arrow)
#grid(columns: (1fr, 1fr), gutter: 14pt,
  box([*Local runner* \
Python process → pandas / GeoPandas]),
  box([*Shared service* \
HTTP · Python client · MCP \
Submit job → retrieve results]))
#caption[The plugin supplies the methodology in either setting. Server operators install plugins and configure their resources; clients use the published interfaces.]

== Exploring and applying shared methods
A catalog helps researchers discover metrics and inspect their contracts. A playground connects those declarations to location selection, parameter changes, and result visualization, reducing the context a newcomer needs to reconstruct.

#image("images/mexicali.png", width: 100%, height: 140pt, fit: "contain")
#caption[Scenario planning in Mexicali. Map interactions connect interventions at the scale of houses, lots, and streets to metric calculations. The application can reuse earlier work while recalculating affected parts.]

Users explore interventions as small as a tree or streetlight. The application translates interactions into requests; the plugin retains the indicator definitions.

== Agents and subsequent analysis
Through Model Context Protocol (MCP), an agent can discover metrics, inspect contracts, submit requests, and retrieve results. The registered method still performs the scientific calculation. Its descriptions and units help the agent choose inputs and interpret returned tables.

Those tables can then enter a DuckDB workspace alongside census or other data. An agent can write SQL to join datasets and investigate relationships. This subsequent analysis depends on appropriate joins, compatible observation periods, and the meaning of the indicators.

== Research directions
Research directions include gradients through differentiable calculations and agents that propose, evaluate, and refine interventions. Both require methodological context, feasible changes, and an explicit definition of improvement.

#pagebreak()
#section("06", "A first experiment")
The bundled city plugin demonstrates the same contracts and execution model with a small street-maintenance calculation. It includes synthetic data and needs no database, credentials, or server. Use Python 3.11 or later, uv, and a checkout of the repository linked below.

From the repository root, prepare the example:
```sh
cd examples/city-plugin
uv sync
```
Save the following as `first_metric.py` in that directory:
```python
"""Run the bundled street-maintenance example."""

from city_example import create_plugin
from lyra.sdk import LocalRunner

runner = LocalRunner.from_definitions({"city": create_plugin()})
tables = runner.run(
    "city_street_maintenance",
    {
        "streets": {
            "collection": "example:city",
            "unit_type": "street",
            "ids": ["street-1"],
        },
        "parameters": {"condition_threshold": 50},
    },
)
print(tables["maintenance"][["street_id", "maintenance_length_m"]])
```
Run `uv run python first_metric.py`. The result identifies `street-1` and reports *100 m* of maintenance workload. Its condition score is 20, below the threshold of 50. The returned dictionary contains the named `maintenance` table.

*Try a change.* Select `street-2`: its score is 80, so the workload is zero at threshold 50. Raise the threshold to 90 and its 200 m length becomes eligible. Equality with the threshold does not trigger maintenance. This fixture illustrates execution; it does not define a real city's maintenance policy.

== Continue with your own method
Start with one calculation and describe its inputs and returned tables. Then add its dependencies and test the plugin locally. Move to a shared server when colleagues need access to the same configured methodology.

#text(size: 9pt)[
#link(docs + "latest/start/local/")[Local walkthrough: rodolfofigueroa.github.io/Lyra/latest/start/local/] \
#link(docs + "latest/plugins/tutorial/")[Plugin tutorial: rodolfofigueroa.github.io/Lyra/latest/plugins/tutorial/] \
#link(docs + "latest/operate/deployment/")[Deployment: rodolfofigueroa.github.io/Lyra/latest/operate/deployment/]
]
#v(5pt)
#grid(columns: (1fr, 1fr), gutter: 18pt,
  [#grid(columns: (48pt, 1fr), gutter: 9pt,
    link(repo, image("images/qr_repo.png", width: 48pt)),
    [*Code* \
    #text(size: 8pt)[#link(repo)[github.com/ \
    RodolfoFigueroa/lyra]]])],
  [#grid(columns: (48pt, 1fr), gutter: 9pt,
    link(docs, image("images/qr_docs.png", width: 48pt)),
    [*Documentation* \
    #text(size: 8pt)[#link(docs)[rodolfofigueroa.github.io/Lyra/]]])])
