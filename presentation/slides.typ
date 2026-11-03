// Build all outputs: make -C presentation
// All diagrams are native Typst. Main talk: 19 slides, 19 minutes 45 seconds.
#import "@preview/touying:0.8.0": *
#import themes.metropolis: *

#let ink = rgb("#183D45")
#let teal = rgb("#137C78")
#let orange = rgb("#C45B36")
#let paper = rgb("#FAF9F5")
#let muted = rgb("#5F7174")
#let pale = rgb("#E3EFEB")
#let warm = rgb("#F7E5D9")
#let rule = rgb("#C5D1CE")

// Replace with paths relative to this file, or pass --input catalog=...
#let screenshots = (
  catalog: sys.inputs.at("catalog", default: none),
  playground: sys.inputs.at("playground", default: none),
  mexicali: sys.inputs.at("mexicali", default: none),
)

// Reserve a separate column for the preview so notes never run underneath it.
#let speaker-panel(self: none, ..args) = touying-notes(
  self: self,
  header: none,
  fill: paper,
  preview-setting: preview => place(
    top + right,
    dx: -32pt,
    dy: 32pt,
    scale(x: 26%, y: 26%, reflow: true, preview),
  ),
  note-setting: note => pad(left: 32pt, right: 312pt, top: 32pt)[
    #text(19pt, fill: teal, weight: "bold")[Speaker notes]
    #v(20pt)
    #note
  ],
  ..args,
)

#show: metropolis-theme.with(
  aspect-ratio: "16-9",
  align: top,
  footer: none,
  footer-progress: false,
  footer-right: context text(
    size: 13pt,
    fill: muted,
    utils.slide-counter.display() + " / " + utils.last-slide-number,
  ),
  config-page(
    width: 960pt,
    height: 540pt,
    margin: (top: 104pt, bottom: 42pt, x: 54pt),
  ),
  config-common(
    handout: sys.inputs.at("handout", default: "false") == "true",
    show-only-notes: sys.inputs.at("notes", default: "false") == "true",
    breakable: false,
    clip: false,
    detect-overflow: sys.inputs.at("notes", default: "false") != "true",
    notes-fn: speaker-panel,
    show-strong-with-alert: false,
  ),
  config-colors(
    primary: teal,
    primary-light: pale,
    secondary: ink,
    neutral-lightest: paper,
    neutral-dark: ink,
    neutral-darkest: ink,
  ),
  config-info(
    title: text(48pt, weight: "bold")[Reusable geospatial \
      methodologies],
    subtitle: text(27pt)[A framework for shared research pipelines],
    author: text(22pt)[Rodolfo Figueroa Soriano],
    date: none,
    institution: none,
  ),
)
#set document(
  title: "Reusable geospatial methodologies",
  author: "Rodolfo Figueroa Soriano",
)
#set text(font: ("Avenir Next", "Arial"), size: 25pt, fill: ink, lang: "en")
#set par(leading: 0.65em, spacing: 0.8em)
#show raw: set text(font: ("Menlo", "DejaVu Sans Mono"))

// Shared typography and native diagram components.
#let lead(body, size: 32pt, color: ink) = text(size, fill: color, weight: "medium", body)
#let small(body, size: 20pt) = text(size, fill: muted, body)
#let code(source, size: 18pt, height: 150pt, inset: 16pt, leading: 0.65em) = block(
  width: 100%,
  height: height,
  inset: inset,
  fill: rgb("#EEF1ED"),
  radius: 3pt,
  [
    #set par(leading: leading)
    #show raw: set text(size: size)
    #raw(source, lang: "python", block: true)
  ],
)

// The examples share the city plugin's column helper and calculate_lengths.
// STREETS is the provider's registered contract for STREET_SCHEMA.
#let location-schema-code = (
  "STREET_SCHEMA = pa.DataFrameSchema({",
  "    \"id\": column(\"str\", \"Street ID\"),",
  "    \"geometry\": column(",
  "        \"geometry\", \"Street centreline\"",
  "    ),",
  "    \"lanes\": column(",
  "        \"int64\", \"Lane count\", Unit.COUNT",
  "    ),",
  "})",
).join("\n")
#let metric-schema-code = (
  "LENGTHS = pa.DataFrameSchema({",
  "    \"street_id\": column(",
  "        \"str\", \"Street ID\"",
  "    ),",
  "    \"length_m\": column(",
  "        \"float64\", \"Length\", Unit.METRE",
  "    ),",
  "}, metadata={",
  "    \"meaning\": \"Street length\",",
  "    \"granularity\": \"One row per street\",",
  "})",
).join("\n")
#let metric-definition-code = (
  "@metric(",
  "    name=\"street_length\",",
  "    description=\"Street length\",",
  "    locations={\"streets\": STREETS},",
  "    outputs={\"lengths\": LENGTHS},",
  ")",
  "def street_length(",
  "    streets: Locations,",
  "):",
  "    return calculate_lengths(",
  "        streets.to_geodataframe()",
  "    )",
).join("\n")
#let talk-note(duration, body) = speaker-note(subslide: none)[
  #set text(size: 14pt)
  #set par(leading: 0.45em, spacing: 0.65em)
  *#duration*

  #body
]
#let shot(path, label, width: 100%, height: 235pt) = {
  if path != none {
    image(path, width: width, height: height, fit: "contain")
  } else {
    block(
      width: width,
      height: height,
      fill: rgb("#EEF1ED"),
      stroke: (paint: rule, thickness: 1pt, dash: "dashed"),
      radius: 3pt,
      inset: 20pt,
      align(center + horizon)[
        #text(25pt, fill: muted)[#label]
        #v(12pt)
        #text(17pt, fill: muted)[Screenshot]
      ],
    )
  }
}
#let at(x, y, body) = place(top + left, dx: x, dy: y, body)
#let segment(x1, y1, x2, y2, color: rule, thickness: 2pt) = at(
  x1,
  y1,
  line(start: (0pt, 0pt), end: (x2 - x1, y2 - y1), stroke: thickness + color),
)
#let arrow(x1, y1, x2, y2, color: muted, thickness: 2pt) = {
  segment(x1, y1, x2, y2, color: color, thickness: thickness)
  if y1 == y2 {
    if x2 > x1 {
      at(x2 - 7pt, y2 - 4pt, polygon(
        (0pt, 0pt),
        (7pt, 4pt),
        (0pt, 8pt),
        fill: color,
        stroke: none,
      ))
    } else {
      at(x2, y2 - 4pt, polygon(
        (7pt, 0pt),
        (0pt, 4pt),
        (7pt, 8pt),
        fill: color,
        stroke: none,
      ))
    }
  } else if y2 > y1 {
    at(x2 - 4pt, y2 - 7pt, polygon(
      (0pt, 0pt),
      (8pt, 0pt),
      (4pt, 7pt),
      fill: color,
      stroke: none,
    ))
  } else {
    at(x2 - 4pt, y2, polygon(
      (0pt, 7pt),
      (8pt, 7pt),
      (4pt, 0pt),
      fill: color,
      stroke: none,
    ))
  }
}
#let node(
  x,
  y,
  body,
  width: 232pt,
  height: 52pt,
  fill: pale,
  color: ink,
  border: none,
  size: 22pt,
) = at(x, y, block(
  width: width,
  height: height,
  fill: fill,
  stroke: border,
  radius: 4pt,
  inset: 7pt,
  align(center + horizon, text(size, fill: color, weight: "medium", body)),
))

// Identical geometry across the dependency and caching slides.
// mode: plain | execute | cached | changed.
#let access-graph(mode: "plain") = block(width: 852pt, height: 326pt)[
  #let changed = mode == "changed"
  #let executed = mode == "execute"
  #let cached = mode == "cached" or changed
  #let source-fill = if cached { pale } else { rgb("#E9ECE7") }
  #let mid-fill = if executed or cached { pale } else { rgb("#E9ECE7") }
  #let calc-fill = if changed { warm } else { mid-fill }
  #let active = if changed { orange } else { teal }
  #for x in (116pt, 426pt, 736pt) {
    arrow(x, 44pt, x, 73pt)
  }
  #segment(116pt, 125pt, 116pt, 151pt)
  #segment(116pt, 151pt, 736pt, 151pt)
  #segment(736pt, 125pt, 736pt, 151pt)
  #arrow(426pt, 125pt, 426pt, 183pt, color: if executed { teal } else { muted })
  #arrow(426pt, 239pt, 426pt, 271pt, color: active)
  #arrow(620pt, 211pt, 542pt, 211pt, color: active)
  #node(0pt, 0pt, [Demographics], height: 44pt, fill: source-fill)
  #node(310pt, 0pt, [Job locations], height: 44pt, fill: source-fill)
  #node(620pt, 0pt, [Road network], height: 44pt, fill: source-fill)
  #node(0pt, 73pt, [Working population], fill: mid-fill, size: 21pt)
  #node(310pt, 73pt, [Jobs by destination], fill: mid-fill, size: 21pt)
  #node(620pt, 73pt, [Driving travel times], fill: mid-fill, size: 21pt)
  #node(310pt, 183pt, [Demand + decay], height: 56pt, fill: calc-fill)
  #node(
    620pt,
    187pt,
    if changed { [Travel-time limit: 45 min] } else { [Travel-time limit: 30 min] },
    height: 48pt,
    fill: if changed { warm } else { paper },
    border: 1.5pt + active,
    size: 18pt,
  )
  #node(
    310pt,
    271pt,
    [Accessibility / block],
    height: 54pt,
    fill: if changed { orange } else { ink },
    color: white,
  )
  #if cached {
    at(0pt, 205pt, [
      #text(32pt, fill: teal, weight: "medium")[Milliseconds]
      #v(8pt)
      #text(20pt, fill: muted)[on a full cache hit]
    ])
  }
]
#let result-table() = {
  set text(size: 24pt)
  table(
    columns: (145pt, 158pt),
    inset: (x: 16pt, y: 14pt),
    stroke: none,
    align: (left, right),
    fill: (x, y) => if y == 0 { ink } else if calc.odd(y) { pale } else { none },
    table.header(text(fill: white, weight: "medium")[Block], text(fill: white, weight: "medium")[Accessibility]),
    [B01], [0.28],
    [B02], [0.63],
    [B03], [0.81],
  )
}
#let block-streets() = block(width: 390pt, height: 185pt)[
  #at(107pt, 39pt, rect(width: 142pt, height: 105pt, fill: pale, stroke: none))
  #for y in (25pt, 158pt) {
    segment(34pt, y, 331pt, y, color: orange, thickness: 8pt)
  }
  #for x in (94pt, 262pt) {
    segment(x, 0pt, x, 184pt, color: orange, thickness: 8pt)
  }
  #at(127pt, 76pt, text(25pt, weight: "medium", fill: teal)[Block])
]
#let street-streets() = block(width: 390pt, height: 185pt)[
  #for x in (63pt, 320pt) {
    segment(x, 0pt, x, 184pt, color: rule, thickness: 7pt)
  }
  #for y in (40pt, 142pt) {
    segment(20pt, y, 360pt, y, color: orange, thickness: 8pt)
  }
  #segment(195pt, 0pt, 195pt, 184pt, color: teal, thickness: 10pt)
  #at(212pt, 79pt, text(23pt, fill: teal, weight: "medium")[Street])
]
#let route-graph(shortcut: false) = block(width: 852pt, height: 178pt)[
  #let old-color = if shortcut { rule } else { teal }
  #arrow(154pt, 29pt, 232pt, 29pt, color: old-color)
  #arrow(386pt, 29pt, 464pt, 29pt, color: old-color)
  #arrow(618pt, 29pt, 696pt, 29pt, color: old-color)
  #node(0pt, 0pt, [State], width: 154pt, height: 58pt)
  #node(232pt, 0pt, [Municipality], width: 154pt, height: 58pt, size: 20pt)
  #node(464pt, 0pt, [Census tract], width: 154pt, height: 58pt, size: 20pt)
  #node(696pt, 0pt, [Block], width: 154pt, height: 58pt, fill: ink, color: white)
  #if shortcut {
    segment(77pt, 58pt, 77pt, 122pt, color: teal, thickness: 3pt)
    segment(77pt, 122pt, 773pt, 122pt, color: teal, thickness: 3pt)
    arrow(773pt, 122pt, 773pt, 58pt, color: teal, thickness: 3pt)
    at(311pt, 137pt, text(23pt, fill: teal, weight: "medium")[Direct selection])
  } else {
    at(233pt, 112pt, text(24pt, fill: teal)[The framework connects the steps.])
  }
]

// 01 -------------------------------------------------------------------------
#title-slide(
  config: config-common(freeze-slide-counter: false),
  extra: [
    #talk-note("0:20 · elapsed 0:20")[
      I work in an urban research lab, where much of our work depends on
      geospatial indicators. I built a framework to help people reuse the
      methods behind those indicators without depending on the original
      project's developer.
    ]
  ],
)

// 02 -------------------------------------------------------------------------
#slide(title: [When a project ends, its pipeline goes on the shelf])[
  #lead[We build the pipeline. We deliver the project.]
  #v(10pt)
  #lead(color: orange)[Then the code starts collecting dust.]
  #v(38pt)
  #grid(
    columns: (1fr, 1fr),
    gutter: 48pt,
    [
      #text(27pt, weight: "bold")[For its author]
      #v(10pt)
      Every reuse becomes another request for help.

      #small[Maintainer, support desk, living documentation.]
    ],
    [
      #text(27pt, weight: "bold")[For other researchers]
      #v(10pt)
      Reuse depends on finding the person who wrote it.

      #small[When that person leaves, knowledge leaves too.]
    ],
  )
  #talk-note("1:00 · elapsed 1:20")[
    Most of us recognize this pattern. A project needs an indicator, so we find
    the data, work out the method, and build a pipeline. We finish the project
    and move on. The code remains in a repository or a folder, but using it
    again requires reconstructing the context.

    For the developer, that means becoming the permanent maintainer and knowledge
    base. For everyone else, it means depending on the developer's time and memory.
    Poor documentation makes that dependence worse. If the developer leaves, a
    perfectly useful method can become very difficult to recover.

    I wanted researchers outside the original project to be able to pick up
    that work and use it.
  ]
]

// 03 -------------------------------------------------------------------------
#slide(title: [Keep the methodology; declare its interface])[
  #grid(
    columns: (1fr, 1fr),
    gutter: 50pt,
    [
      #text(27pt, weight: "bold")[Different data]
      #v(12pt)
      Census data \
      Google Earth Engine \
      OpenStreetMap
    ],
    [
      #text(27pt, weight: "bold")[Different approaches]
      #v(12pt)
      Python libraries \
      SQL and spatial databases \
      Custom models
    ],
  )
  #v(35pt)
  #line(length: 100%, stroke: 1pt + rule)
  #v(18pt)
  #lead(size: 31pt)[Keep your calculations. Add a reusable contract.]
  #v(12pt)
  #text(23pt, fill: teal)[Standard Python functions with declared inputs, outputs, and dependencies.
   ]
  #talk-note("0:50 · elapsed 2:10")[
    Developers have good reasons to use different sources and different tools.
    Prescribing one framework for every methodology would slow people down and
    restrict what they can do.

    Our framework adds a small declarative layer around ordinary Python
    calculations. Developers keep their libraries and scientific choices.
    They describe what each calculation needs and produces, giving other
    people a stable way to discover and run it.

    We can organize existing work and make it extendable while preserving the
    methods that made it useful in the first place.
  ]
]

// 04 -------------------------------------------------------------------------
#slide(title: [One accessibility indicator, many moving parts])[
  #grid(
    columns: (1fr, 1fr, 1fr),
    gutter: 28pt,
    [
      #text(28pt, weight: "bold")[People]
      #v(8pt)
      #small[Working population by city block]
    ],
    [
      #text(28pt, weight: "bold")[Jobs]
      #v(8pt)
      #small[Employment at individual sites]
    ],
    [
      #text(28pt, weight: "bold")[Roads]
      #v(8pt)
      #small[OpenStreetMap network and driving times]
    ],
  )
  #v(31pt)
  #text(24pt)[Download → clean → reproject → aggregate]
  #v(17pt)
  #text(24pt)[Snap to roads → calculate travel times → define catchments]
  #v(17pt)
  #text(24pt)[Weight demand → apply distance decay → normalize]
  #v(28pt)
  #lead(size: 30pt, color: teal)[All for one accessibility score per block.]
  #talk-note("1:20 · elapsed 3:30")[
    Consider a job-accessibility model for city blocks in Mexico. Nearby jobs are
    attractive, but their contribution decreases with driving time. We also
    account for competition from the working population.

    That immediately gives us three sources: demographic observations for city
    blocks, point locations containing employment, and the road network. Each
    has a different structure and a different preparation workflow.

    We download, clean, reproject, and aggregate. We connect blocks and job sites
    to the network. We calculate travel times and reachable areas. Then we choose
    the decay, account for demand, and combine the pieces. Some of those
    calculations can be expensive.

    The researcher who wants the result does not need to understand every one of
    these steps. Their goal is a table containing a block identifier and an
    accessibility coefficient between zero and one.
  ]
]

// 05 -------------------------------------------------------------------------
#slide(title: [What the researcher asks for])[
  #v(18pt)
  #grid(
    columns: (310pt, 125pt, 1fr),
    align: horizon,
    [
      #text(24pt, fill: muted)[Metric]
      #v(8pt)
      #lead(size: 34pt)[Job accessibility]
      #v(25pt)
      #text(24pt, fill: muted)[Locations]
      #v(8pt)
      #text(30pt)[B01, B02, B03]
    ],
    align(center, text(48pt, fill: teal)[→]),
    result-table(),
  )
  #v(38pt)
  #lead(size: 29pt)[Request the result. Its dependencies run automatically.]
  #talk-note("0:50 · elapsed 4:20")[
    This is the interface we want to give someone: choose job accessibility,
    provide the blocks, and receive the table.

    From that request, the executor follows the declared dependencies and
    supplies their results to the calculation. You can run it locally or
    submit it to a shared server.

    This is the central promise of the framework. To see how it works, we only
    need a few concepts: locations, metrics, their dependencies, and operations
    that connect locations.
  ]
]

// 06 -------------------------------------------------------------------------
#slide(title: [Locations: identity, geometry, and attributes])[
  #text(25pt)[Define what each row of street data contains.]
  #v(18pt)
  #grid(
    columns: (530pt, 1fr),
    gutter: 34pt,
    align: horizon,
    code(location-schema-code, size: 18pt, height: 280pt, inset: 14pt, leading: 0.45em),
    [
      #text(25pt, weight: "bold", fill: teal)[Identity]
      #v(5pt)
      #text(22pt)[A stable street ID.]
      #v(20pt)
      #text(25pt, weight: "bold", fill: teal)[Geometry]
      #v(5pt)
      #text(22pt)[The street's centreline.]
      #v(20pt)
      #text(25pt, weight: "bold", fill: teal)[Attributes]
      #v(5pt)
      #text(22pt)[A typed lane count.]
    ],
  )
  #v(18pt)
  #text(23pt)[The same structure can describe a block, a state, or a country.]
  #talk-note("0:50 · elapsed 5:10")[
    Start with a street. Its schema declares the identity, the geometry, and
    the attributes available to a calculation.

    The ID is a string. The geometry is the street's centreline. The lane count
    is an integer with a count unit. The column helper packages a data type,
    a description, and an optional unit into each declaration.

    Our location provider associates this schema with the street collection
    and its data source. The next slide calls that registered definition STREETS.

    A block or a country has different geometry and attributes, but the same
    basic structure. The schema tells a calculation what it will receive.
  ]
]

// 07 -------------------------------------------------------------------------
#slide(title: [Metrics: inputs, outputs, and units])[
  #text(25pt)[A Python calculation with a declared table result.]
  #v(18pt)
  #grid(
    columns: (436pt, 390pt),
    gutter: 26pt,
    [
      #text(23pt, weight: "bold", fill: teal)[Output schema]
      #v(12pt)
      #code(metric-schema-code, size: 17pt, height: 280pt, inset: 12pt, leading: 0.45em)
    ],
    [
      #text(23pt, weight: "bold", fill: teal)[Metric declaration]
      #v(12pt)
      #code(metric-definition-code, size: 17pt, height: 280pt, inset: 12pt, leading: 0.45em)
    ],
  )
  #talk-note("1:10 · elapsed 6:20")[
    Now define a metric that measures the length of those streets.

    On the left, the output schema declares two columns: a street identifier
    and a floating-point length measured in metres. It also describes the
    table's meaning and its granularity: one row per street.

    On the right, the decorator gives the metric a name and description.
    The locations entry connects its streets argument to the registered street
    definition from the previous slide. The outputs entry names the result
    table and connects it to the LENGTHS schema.

    The Python function delegates to an existing calculation, which measures
    the projected street geometry. The declarations tell other people what
    to supply and what they will receive.

    Every metric returns one or more tables. Ordinary choices can also be
    declared as parameters, and other metrics can supply its dependencies.
  ]
]

// 08 -------------------------------------------------------------------------
#slide(title: [Dependencies make the methodology explicit])[
  #alternatives[
    #access-graph()
  ][
    #access-graph(mode: "execute")
  ]
  #v(10pt)
  #text(23pt)[Each intermediate result can become a reusable metric.]
  #talk-note("1:00 · elapsed 7:20")[
    Now we can express the accessibility calculation as a dependency graph.
    Each box produces a result that another calculation needs.

    The population and employment branches prepare the inputs. The road branch
    produces driving times. Those feed the demand and decay calculation, which
    produces the final accessibility table.

    Advance the reveal. Requesting the final node triggers the required upstream
    calculations. Shared dependencies are reused within the execution.

    The graph makes the methodology explicit. It also gives us reusable pieces:
    another metric can consume these same population, employment, or travel-time
    tables. Developers declare the relationships once; users request results.
  ]
]

// 09 -------------------------------------------------------------------------
#slide(title: [Operations express spatial relationships])[
  #grid(
    columns: (1fr, 1fr),
    gutter: 55pt,
    [
      #text(25pt, weight: "medium")[Block → surrounding streets]
      #v(20pt)
      #block-streets()
    ],
    [
      #text(25pt, weight: "medium")[Street → intersecting streets]
      #v(20pt)
      #street-streets()
    ],
  )
  #v(19pt)
  #text(23pt)[The same location types can have several relationships:]
  #v(13pt)
  #text(25pt, fill: teal, weight: "medium")[feeds into    ·    flows out of    ·    runs parallel]
  #talk-note("1:15 · elapsed 8:35")[
    Metrics produce tables. Operations supply locations to a calculation.
    Start with the block on the left: an operation can return all the streets
    surrounding it. One location has led us to several related locations.

    The operation does not have to change the type. On the right, the selected
    street leads to the streets that intersect it. Both the input and output
    are streets.

    We can also define several relationships between the same types. For a street,
    we might need incoming streets, outgoing streets, or parallel streets. The
    operation names the relationship, and the plugin implements it.

    This lets a calculation ask for the spatial inputs it actually needs. A block
    metric can use street measurements without making its caller collect those
    streets first.
  ]
]

// 10 -------------------------------------------------------------------------
#slide(title: [Three ways to use the same calculation])[
  #grid(
    columns: (1fr, 1fr, 1fr),
    gutter: 25pt,
    [
      #text(28pt, weight: "bold")[Import]
      #v(12pt)
      #text(23pt)[Call the Python function.]
      #v(22pt)
      #code("import my_plugin\n\nmy_plugin.metric(\n    locations,\n)", size: 17pt)
      #v(17pt)
      #small[Your code supplies its arguments.]
    ],
    [
      #text(28pt, weight: "bold")[Run locally]
      #v(12pt)
      #text(23pt)[Execute the pipeline.]
      #v(22pt)
      #code("result = runner.run(\n    \"accessibility\",\n    request,\n)", size: 17pt)
      #v(17pt)
      #small[In a script, notebook, or application.]
    ],
    [
      #text(28pt, weight: "bold")[Run remotely]
      #v(12pt)
      #text(23pt)[Use a shared server.]
      #v(22pt)
      #block(height: 150pt, inset: (top: 14pt))[
        #text(24pt, fill: teal, weight: "medium")[HTTP · Python · MCP]
        #v(17pt)
        #text(22pt)[Submit → run → result]
      ]
      #v(17pt)
      #small[Shared methods and configured data sources.]
    ],
  )
  #talk-note("1:00 · elapsed 9:35")[
    The underlying function remains an ordinary Python function. You can import
    it and call it with its arguments, just as you would any other function.

    For a complete pipeline, the local runner executes the graph in your Python
    process. That works in a script, a notebook, or another application. You
    supply the final metric and its request; the runner handles the declared
    dependencies.

    A server makes the same calculations available to a wider group. The operator
    configures the plugins and their data sources, and callers submit requests
    through HTTP, a Python client, or MCP. They receive a job and retrieve its
    result when it completes.

    These are different ways to access the same methodology.
  ]
]

// 11 -------------------------------------------------------------------------
#slide(title: [Changing a parameter only repeats affected work])[
  #alternatives[
    #access-graph(mode: "cached")
    #v(10pt)
    #text(23pt, fill: teal)[Persistent caching keeps intermediate results available.]
  ][
    #access-graph(mode: "changed")
    #v(10pt)
    #grid(
      columns: (1fr, 1fr),
      text(23pt, fill: teal)[Reuse existing results], text(23pt, fill: orange)[Recompute the affected branch],
    )
  ]
  #talk-note("1:20 · elapsed 10:55")[
    Once we have run this model with persistent caching, the intermediate results
    are available to later executions.

    Now change the travel-time limit from thirty to forty-five minutes. Advance
    the reveal. In this graph, the population, jobs, and driving-time tables are
    unchanged. We reuse them. The demand and decay calculation receives a new
    parameter, so that calculation and the final score are recomputed.

    This changes the cost of iteration. Researchers can explore the choices they
    care about while reusing the expensive preparation work.

    In our internal benchmarks, a fully cached request on a synthetic graph with
    hundreds of nodes is on the order of milliseconds. The important experience
    for the researcher is that a repeated question can return quickly, and a
    small change can reuse most of the previous work.
  ]
]

// 12 -------------------------------------------------------------------------
#slide(title: [Only compute the missing locations])[
  #v(10pt)
  #text(25pt, fill: muted)[First request]
  #v(14pt)
  #grid(
    columns: (130pt, 130pt, 130pt, 1fr),
    gutter: 20pt,
    block(fill: pale, inset: 20pt, align(center)[A]),
    block(fill: pale, inset: 20pt, align(center)[B]),
    block(fill: pale, inset: 20pt, align(center)[C]),
    align(horizon, text(25pt, fill: teal)[Results stored]),
  )
  #v(34pt)
  #text(25pt, fill: muted)[Next request]
  #v(14pt)
  #alternatives[
    #grid(
      columns: (130pt, 130pt, 130pt, 1fr),
      gutter: 20pt,
      block(fill: rgb("#E9ECE7"), inset: 20pt, align(center)[B]),
      block(fill: rgb("#E9ECE7"), inset: 20pt, align(center)[C]),
      block(fill: rgb("#E9ECE7"), inset: 20pt, align(center)[D]),
      align(horizon, text(25pt)[An overlapping study area]),
    )
  ][
    #grid(
      columns: (130pt, 130pt, 130pt, 1fr),
      gutter: 20pt,
      block(fill: pale, inset: 20pt, align(center)[B]),
      block(fill: pale, inset: 20pt, align(center)[C]),
      block(fill: warm, inset: 20pt, align(center)[D]),
      align(horizon, text(25pt)[Reuse B + C. Compute D.]),
    )
  ]
  #v(30pt)
  #lead(size: 28pt)[Reuse follows the locations, too.]
  #talk-note("0:50 · elapsed 11:45")[
    Reuse also works when the requested locations overlap.

    Our first request computed A, B, and C. Later, someone asks for B, C, and D.
    Advance the reveal. We already have the results for B and C. Only D needs
    to be calculated.

    That is incremental caching at the location level. It is useful when a
    researcher expands a study area, compares nearby neighborhoods, or asks
    questions that overlap with previous analyses.

    Researchers receive one table containing both the reused results and the
    newly calculated rows.
  ]
]

// 13 -------------------------------------------------------------------------
#slide(title: [Request the geography you have])[
  #grid(
    columns: (1fr, 75pt, 1fr),
    align: horizon,
    [
      #small[Select blocks]
      #v(8pt)
      #text(28pt)[B01, B02, B03, …]
    ],
    text(38pt, fill: teal)[→],
    [
      #small[Select a metropolitan area]
      #v(8pt)
      #text(28pt, weight: "medium")[Mexico City]
    ],
  )
  #v(29pt)
  #alternatives[
    #route-graph()
  ][
    #route-graph(shortcut: true)
  ]
  #v(15pt)
  #lead(size: 28pt)[The result still contains one row per block.]
  #talk-note("1:30 · elapsed 13:15")[
    Researchers usually think in terms of a city or a metropolitan area. They
    should be able to select that area and receive the block results they need.

    Otherwise, they would query a database, find the block geometries, do the
    spatial selection, extract the identifiers, and return to the pipeline.
    Registering those operations lets our system perform the selection while
    preparing the request.

    The same idea extends across multiple steps. A state can reach its blocks
    through municipalities and census tracts. The available operations provide
    the path, so the caller can keep asking for the geography they have.

    Advance the reveal. Add a direct state-to-block operation, and the shortest
    route now uses that single operation. Shortest here means the fewest
    operations. Developers can explicitly bind operations in a calculation;
    route preferences choose among tied shortest routes.

    Selecting a larger region changes how we obtain the inputs. Our accessibility
    metric still produces a row for each block.
  ]
]

// 14 -------------------------------------------------------------------------
#slide(title: [The same declarations are useful to agents])[
  #lead(size: 31pt)[“Calculate accessibility for all blocks in Mexico City.”]
  #v(36pt)
  #grid(
    columns: (1fr, 40pt, 1fr, 40pt, 1fr),
    align: horizon,
    [
      #text(29pt, weight: "bold")[Discover]
      #v(10pt)
      #small[Browse and search \
        the metric catalog.]
    ],
    text(31pt, fill: teal)[→],
    [
      #text(29pt, weight: "bold")[Inspect]
      #v(10pt)
      #small[Read inputs, outputs, \
        and relationships.]
    ],
    text(31pt, fill: teal)[→],
    [
      #text(29pt, weight: "bold")[Execute]
      #v(10pt)
      #small[Request the metric \
        and retrieve its results.]
    ],
  )
  #v(42pt)
  #text(26pt, fill: teal)[MCP exposes the existing catalog and execution API.]
  #talk-note("1:00 · elapsed 14:15")[
    I built the system for human users. The same declarations made it
    straightforward for agents to discover and run the methods.

    The catalog tells an agent which metrics exist. The declarations tell it
    what a metric expects, what it produces, and how it relates to other
    calculations. The execution interface lets it submit a request.

    We added an MCP interface over the existing API. The agent can browse or
    search, inspect the selected metric, and execute it. It gets the same
    dependency execution, spatial selection, and caching capabilities as other
    clients.

    The useful part was that the work needed to make a methodology reusable by
    people also supplied the structure needed for agent access.
  ]
]

// 15 -------------------------------------------------------------------------
#slide(title: [A shared catalog and a metric playground])[
  #grid(
    columns: (1fr, 1fr),
    gutter: 30pt,
    [
      #shot(screenshots.catalog, [Metric catalog])
      #v(15pt)
      #text(26pt, weight: "medium")[Find the method you need.]
    ],
    [
      #shot(screenshots.playground, [Metric playground])
      #v(15pt)
      #text(26pt, weight: "medium")[Run, adjust, and visualize it.]
    ],
  )
  #v(22pt)
  #text(24pt)[Shared methods, real data, less onboarding.]
  #talk-note("0:45 · elapsed 15:00")[
    In the lab, the first result is a searchable catalog of the metrics we have
    developed. Someone can discover a method without knowing who originally
    wrote it.

    The next step is a metric playground. Researchers choose a metric, run it
    against real data, change parameters, and visualize the result.

    These tools use the same declarations and execution capabilities we have
    just discussed. They turn a collection of methods into something the rest
    of the lab can explore and use with less onboarding.
  ]
]

// 16 -------------------------------------------------------------------------
#slide(title: [Scenario planning in Mexicali])[
  #grid(
    columns: (580pt, 1fr),
    gutter: 35pt,
    align: horizon,
    shot(screenshots.mexicali, [Mexicali scenario planning · 3D map], height: 334pt),
    [
      #text(28pt, weight: "bold")[Houses, lots, \
        and streets.]
      #v(25pt)
      #text(25pt)[Interventions as small as a tree or a streetlight.]
      #v(30pt)
      #text(27pt, fill: teal, weight: "medium")[Edit the map. \
        See results.]
    ],
  )
  #talk-note("1:45 · elapsed 16:45")[
    This is a scenario-planning product we were hired to develop for a real
    estate developer in Mexicali.

    The spatial resolution is very fine: individual houses, lots, and streets.
    An intervention can be as small as a single tree or a streetlight.
    Users interact with a three-dimensional map, change the underlying data,
    and see how the metrics respond.

    Walk through one intervention using the screenshot: point to the affected
    location, describe what the user changes, and indicate where the resulting
    metrics are displayed. Keep the story centered on that person's decision.

    The performance requirements are very different from an overnight research
    pipeline. Users are exploring a scenario while looking at the map, so the
    interaction needs to remain responsive.

    Caching let us retain the work already done and recalculate the parts
    affected by an intervention. Together with fast execution, that kept the
    application responsive.

    This is where the earlier abstractions become tangible: a reusable
    methodology can support a product people interact with in real time.
  ]
]

// 17 -------------------------------------------------------------------------
#slide(title: [Using metric results for further analysis])[
  #v(17pt)
  #grid(
    columns: (1fr, 50pt, 1fr, 50pt, 1fr),
    align: horizon,
    [
      #text(29pt, weight: "bold")[Metric results]
      #v(13pt)
      #small[Computed indicators]
    ],
    text(35pt, fill: teal)[→],
    [
      #text(29pt, weight: "bold")[DuckDB]
      #v(13pt)
      #small[Tables + other data]
    ],
    text(35pt, fill: teal)[→],
    [
      #text(29pt, weight: "bold")[Analysis]
      #v(13pt)
      #small[Agent-written queries]
    ],
  )
  #v(45pt)
  #lead(size: 30pt)[How does accessibility relate to census characteristics?]
  #v(23pt)
  #text(24pt)[Join datasets. Compare places. Investigate relationships.]
  #talk-note("0:45 · elapsed 17:30")[
    In a separate project, we have started letting agents analyze metric results
    alongside other data.

    We can forward a result to a DuckDB table and connect other data sources to
    the same database. The agent can then write SQL to join the indicator with
    census data, compare groups of places, or prepare a statistical analysis.

    Our system handles metric calculation. DuckDB gives the agent a workspace
    for investigating the results alongside other data. That moves the
    interaction beyond simply requesting one table.
  ]
]

// 18 -------------------------------------------------------------------------
#slide(title: [Working backward from a desired outcome])[
  #grid(
    columns: (1fr, 1fr),
    gutter: 48pt,
    [
      #text(23pt, fill: muted)[Evaluate a change]
      #v(13pt)
      #lead(size: 29pt)[“If we change X, \
        what happens to Y?”]
    ],
    [
      #text(23pt, fill: muted)[Find an intervention]
      #v(13pt)
      #lead(size: 29pt, color: teal)[“To improve Y, \
        what should we change?”]
    ],
  )
  #v(37pt)
  #line(length: 100%, stroke: 1pt + rule)
  #v(21pt)
  #grid(
    columns: (1fr, 1fr),
    gutter: 48pt,
    [
      #text(27pt, weight: "bold")[Differentiable calculations]
      #v(12pt)
      #text(23pt)[Use gradients to work backward through the graph.]
    ],
    [
      #text(27pt, weight: "bold")[Agent-guided search]
      #v(12pt)
      #text(23pt)[Use methodological context to propose, evaluate, and refine.]
    ],
  )
  #talk-note("1:45 · elapsed 19:15")[
    The larger research question is how to work backward from a desired outcome.

    We can already ask what happens when we change an input. An agent can run
    the metric with different parameters and observe the output, even with
    relatively little knowledge of the implementation.

    The reverse question is harder: if we want a particular improvement in
    accessibility, which interventions should we choose, and by how much should
    we change them?

    One approach is to make the calculations differentiable so gradients can
    propagate through the graph. That would provide a direct numerical tool for
    exploring changes, but implementing it across diverse geospatial methods is
    a substantial challenge.

    Another approach gives an agent enough methodological context to reason
    about candidate interventions. It needs to know what the calculations mean,
    how upstream inputs influence the result, and which changes are feasible.
    It can propose an intervention, run the relevant metrics to evaluate it,
    and use the results to refine the next candidate.

    The graph tells us what depends on what. The research question is what
    additional knowledge helps turn that structure into useful decisions.
  ]
]

// 19 -------------------------------------------------------------------------
#slide(title: [Methods that others can keep using])[
  #v(34pt)
  #lead(size: 43pt)[A methodology should outlive \
    its original project.]
  #v(32pt)
  #text(29pt)[Make methods easy to find, run, and build on.]
  #v(46pt)
  #text(26pt, fill: teal)[What would you want to make reusable?]
  #talk-note("0:30 · elapsed 19:45")[
    Return to the pipeline on the shelf. The goal is to make its methodology
    available to people who were never part of the original project.

    Our framework gives us a shared way to describe and execute that work.
    The calculations and scientific choices remain with their developers.

    Thank you. I would be interested to hear where this fits your own pipelines,
    and what context you think an agent would need to help choose interventions.
  ]
]
