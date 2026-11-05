// Build all outputs: make -C presentation
// All diagrams are native Typst. Main talk: 20 slides, 18 minutes 30 seconds.
#import "@preview/touying:0.8.0": *
#import themes.metropolis: *

#import "style.typ": *

// Supply a path relative to this file with --input mexicali=...
#let screenshots = (
  mexicali: sys.inputs.at("mexicali", default: "images/mexicali.png"),
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
    title: text(48pt, weight: "bold")[Contract-driven geospatial indicators],
    subtitle: text(27pt)[A framework for shared research pipelines],
    author: text(22pt)[Rodolfo Figueroa Soriano],
    date: none,
    institution: text(20pt)[Center for the Future of Cities - Tecnológico de Monterrey],
  ),
)
#set document(
  title: "Reusable geospatial methodologies",
  author: "Rodolfo Figueroa Soriano",
)
#set text(font: body-font, size: 25pt, fill: ink, lang: "en")
#set par(leading: 0.65em, spacing: 0.8em)
#show raw: set text(font: code-font)

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

// BLOCKS is the provider's registered location contract.
// POPULATION declares block_id (string) and working_population (integer, count).
#let metric-definition-code = (
  "@metric(",
  "    name=\"working_population\",",
  "    description=\"Working population\",",
  "    locations={\"blocks\": BLOCKS},",
  "    outputs={\"population\": POPULATION},",
  ")",
  "def working_population(blocks: Locations):",
  "    return calculate_working_population(",
  "        blocks.to_geodataframe()",
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
  #node(0pt, 73pt, [Working population], fill: mid-fill, border: 2pt + teal, size: 21pt)
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
  #if mode == "cached" {
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
  #node(0pt, 0pt, [Metropolitan \ area], width: 154pt, height: 58pt, size: 20pt)
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

#let qr-slot(destination, label, path) = align(center)[
  #text(27pt, weight: "bold", fill: teal)[#label]
  #v(12pt)
  #link(destination, image(
    path,
    width: 156pt,
    height: 156pt,
    fit: "contain",
  ))
  #v(12pt)
  #text(17pt, fill: teal)[#link(destination, destination)]
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
  #text(23pt, fill: teal)[Declare calculations. Resolve spatial inputs. Request results.
  ]
  #talk-note("0:45 · elapsed 2:05")[
    Developers use different sources and tools for good reasons. Our framework
    wraps ordinary Python calculations in a declared interface. You keep your
    libraries, data sources, and scientific choices.

    You describe the inputs, outputs, and dependencies. The framework resolves
    spatial inputs and runs the required calculations when someone requests a
    result. Those same declarations make methods discoverable and usable by
    people outside the original project.

    Let me show what this means for a familiar geospatial question.
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
  #talk-note("1:00 · elapsed 3:05")[
    Consider job accessibility for city blocks. Jobs contribute less as driving
    time increases, and we account for competition from the working population.

    We need demographics by block, employment at job sites, and a road network.
    Preparing those sources means cleaning and reprojecting data, connecting
    locations to roads, and calculating travel times. Then we apply the demand
    and decay model.

    The final result is one accessibility score per block, between zero and one.
    A higher score means better access under this model. The researcher wants
    to explore that result without manually coordinating every preparation step.
  ]
]

// 05 -------------------------------------------------------------------------
#slide(title: [What the researcher asks for])[
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
      #v(20pt)
      #text(24pt, fill: muted)[Travel-time limit]
      #v(8pt)
      #text(28pt)[30 minutes]
    ],
    align(center, text(48pt, fill: teal)[→]),
    result-table(),
  )
  #v(26pt)
  #lead(size: 29pt)[Request the result. Its dependencies run automatically.]
  #talk-note("0:50 · elapsed 3:55")[
    Choose job accessibility, select the blocks, set a thirty-minute travel-time
    limit, and receive a table. Each row identifies a block and its score.

    The executor follows the declared dependencies and supplies their results
    to the final calculation. The same request can run in your own Python process
    or on a shared server.

    That is the user-facing workflow. Now take the developer's perspective:
    how does a calculation you already have become something others can request?
  ]
]

// 06 -------------------------------------------------------------------------
#slide(title: [Making an existing calculation reusable])[
  #grid(
    columns: (38pt, 1fr),
    column-gutter: 15pt,
    row-gutter: 15pt,
    text(fill: teal, weight: "bold")[1], [Choose an existing Python calculation.],
    text(fill: teal, weight: "bold")[2], [Declare its inputs and outputs.],
    text(fill: teal, weight: "bold")[3], [Connect the calculations and data sources it depends on.],
    text(fill: teal, weight: "bold")[4], [Package everything as a reusable plugin.],
    text(fill: teal, weight: "bold")[5], [Run locally\ *OR*\ Share through a server.],
  )
  #v(28pt)
  #line(length: 100%, stroke: 1pt + rule)
  #v(18pt)
  #grid(
    columns: (1fr, 1fr),
    gutter: 40pt,
    [#text(24pt, weight: "bold")[You supply]
      #v(8pt)
      #small[The method and its data access.]],
    [#text(24pt, weight: "bold")[The framework handles]
      #v(8pt)
      #small[Validation and declared execution.]],
  )
  #talk-note("1:00 · elapsed 4:55")[
    Start with one calculation you already use in a script or notebook.
    Declare the locations and parameters it accepts, then the tables it returns,
    including types, units, and what each row represents.

    If it needs another calculation's output, declare that dependency. Register
    the function in a plugin: a Python package whose factory lists its capabilities.
    You can then execute it locally and check the returned tables. A shared server
    can expose the same plugin to colleagues later.

    You still implement the scientific method and access to its data. The framework
    validates the contracts and coordinates the execution you have described.
    Let's apply that to the population branch of our accessibility calculation.
  ]
]

// 07 -------------------------------------------------------------------------
#slide(title: [Population per block: inputs, outputs, and units])[
  #text(25pt)[One reusable calculation in the accessibility model.]
  #v(22pt)
  #grid(
    columns: (548pt, 1fr),
    gutter: 28pt,
    code(metric-definition-code, size: 20pt, height: 294pt, inset: 14pt, leading: 0.45em),
    [
      #text(24pt, weight: "bold", fill: teal)[Input: BLOCKS]
      #v(8pt)
      #text(21pt)[Block ID, geometry, and demographic attributes.]
      #v(24pt)
      #text(24pt, weight: "bold", fill: teal)[Output: POPULATION]
      #v(8pt)
      #text(21pt)[block_id: string \ working_population: integer]
      #v(14pt)
      #text(21pt)[Unit: count \ One row per block.]
    ],
  )
  #talk-note("1:30 · elapsed 6:25")[
    The input contract, BLOCKS, describes block identifiers, geometries, and
    available demographic attributes. A location provider connects that contract
    to the data source and resolves the requested identifiers.

    The output contract, POPULATION, describes a table with a string block ID and
    an integer working-population count. Its metadata specifies the unit and that
    each row represents one block. These declarations tell both the framework and
    the next developer what the calculation accepts and produces.

    The decorator connects those contracts to the function. The function passes
    the resolved blocks to the existing population calculation. We keep the method
    inside that calculation, and expose the result through a stable interface.

    A metric can return several named tables. Here the table is called population.
    Another metric can declare that table as a dependency. Ordinary choices, such
    as the travel-time limit in the final model, have their own parameter declarations.
    Next, let's look at how a developer runs these definitions.
  ]
]

// 08 -------------------------------------------------------------------------
#slide(title: [Local execution and a shared service])[
  #grid(
    columns: (1fr, 1fr),
    gutter: 42pt,
    [
      #text(28pt, weight: "bold")[Run locally]
      #v(12pt)
      #text(23pt)[Use your script or notebook.]
      #v(20pt)
      #code(
        "tables = runner.run(\n    \"accessibility\", request\n)\nscores = tables[\"scores\"]",
        size: 20pt,
        height: 142pt,
      )
      #v(14pt)
      #small[Receive pandas or GeoPandas tables.]
    ],
    [
      #text(28pt, weight: "bold")[Share through a server]
      #v(12pt)
      #text(23pt)[Publish the same plugin.]
      #v(20pt)
      #block(height: 142pt, inset: (top: 14pt))[
        #text(25pt, fill: teal)[HTTP · Python client · MCP]
        #v(20pt)
        #text(24pt)[Submit → job → results]
      ]
      #v(14pt)
      #small[Shared methods and configured data sources.]
    ],
  )
  #v(25pt)
  #text(22pt)[The underlying Python function also remains directly callable.]
  #talk-note("1:00 · elapsed 7:25")[
    Local execution is the easiest place to start. Load your plugin into a runner,
    request the final metric, and receive its named pandas or GeoPandas tables.
    Those results fit directly into the analysis tools you already use.

    When others need the same methodology, an operator installs the plugin and
    configures its sources on a server. Clients submit a job through HTTP, Python,
    or an agent interface, then retrieve the results when it finishes.

    The underlying function also stays directly callable when your code supplies
    its arguments. The runner is what adds dependency execution and the other
    framework services. Let's look at that execution graph.
  ]
]

// 09 -------------------------------------------------------------------------
#slide(title: [Dependencies make the methodology explicit])[
  #alternatives[
    #access-graph()
  ][
    #access-graph(mode: "execute")
  ]
  #v(10pt)
  #text(23pt)[Each intermediate result can become a reusable metric.]
  #talk-note("1:00 · elapsed 8:25")[
    The outlined population node is the calculation we just declared. Its table
    feeds the accessibility model alongside jobs and driving travel times.

    Each dependency names the table its consumer needs. The framework follows those
    relationships from the requested final metric and runs the required producers
    in order. Advance the reveal. Shared invocations are reused within the run.

    This makes the methodology explicit and gives us reusable intermediate results.
    Another model can consume the same population or travel-time tables. Developers
    connect the calculations once; researchers request the final result.

    Saving those intermediate results also changes what happens on the next request.
  ]
]

// 10 -------------------------------------------------------------------------
#slide(title: [Changing a parameter only repeats affected work])[
  #alternatives[
    #access-graph(mode: "changed")
    #v(10pt)
    #grid(
      columns: (1fr, 1fr),
      text(23pt, fill: teal)[Reuse existing results], text(23pt, fill: orange)[Recompute the affected branch],
    )
  ][
    #access-graph(mode: "cached")
    #v(10pt)
    #text(23pt, fill: teal)[Persistent caching keeps intermediate results available.]
  ]
  #talk-note("1:15 · elapsed 9:40")[
    With persistent caching, completed intermediate results remain available to
    later executions. A full cache hit can return in milliseconds.

    Now change the limit from thirty to forty-five minutes. Advance the reveal.
    The stored driving-time table covers both thresholds. Population, jobs, and
    travel times therefore remain unchanged; the limit enters the demand and decay
    calculation. That node and the final score run again, using the saved inputs.

    This is the practical benefit for a researcher comparing parameter choices:
    expensive preparation can be reused while the affected calculations run again.
    The graph also tells us where that change propagates. We can apply the same
    idea to requests that overlap geographically.
  ]
]

// 11 -------------------------------------------------------------------------
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
  #talk-note("0:50 · elapsed 10:30")[
    First we request A, B, and C. Later, someone asks for B, C, and D using the
    same parameters, data, and methodological context for the existing locations.

    Advance the reveal. The results for B and C are already available, so only D
    needs calculating. The caller receives one table with reused and newly
    calculated rows together.

    This is useful when expanding a study area or making overlapping requests
    across neighboring areas. Reuse follows both the dependency graph and the
    locations. Next, consider how we obtain the locations a calculation needs.
  ]
]

// 12 -------------------------------------------------------------------------
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
  #talk-note("1:00 · elapsed 11:30")[
    Metrics produce tables. Operations supply related locations to a calculation.
    For our accessibility workflow, that includes connecting blocks with streets.
    The left diagram shows a block selecting its surrounding streets.

    The right diagram starts with a street and selects intersecting streets.
    An operation can connect locations of the same type, and the same types can
    have several named relationships, such as incoming or parallel streets.

    The plugin implements the relationship. A calculation declares which one it
    needs, so its caller doesn't have to collect those spatial inputs manually.
    Those operations can also make the initial geographic selection much easier.
  ]
]

// 13 -------------------------------------------------------------------------
#slide(title: [Request the geography you have])[
  #grid(
    columns: (1fr, 75pt, 1fr),
    align: horizon,
    [
      #small[Select a metropolitan area]
      #v(8pt)
      #text(28pt, weight: "medium")[Mexico City]
    ],
    text(38pt, fill: teal)[→],
    [
      #small[Resolve its blocks]
      #v(8pt)
      #text(28pt)[B01, B02, B03, …]
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
  #talk-note("1:00 · elapsed 12:30")[
    A researcher usually has a city or metropolitan area in mind. They can select
    that area and let registered operations obtain the blocks needed by the metric.

    A route can connect a metropolitan area to municipalities, census tracts, and
    then blocks. That replaces the caller's manual work of finding geometries,
    performing spatial selections, and collecting identifiers.

    Advance the reveal. Register a direct metropolitan-area-to-block operation,
    and the route can use that single step. The framework chooses the route with
    the fewest registered operations.

    The selection changes how we obtain inputs. The result still has one row per
    block. Once we have that result, we also need to understand what produced it.
  ]
]

// 14 -------------------------------------------------------------------------
#slide(title: [Understanding what produced a result])[
  #grid(
    columns: (290pt, 1fr),
    gutter: 38pt,
    [
      #text(27pt, weight: "bold", fill: teal)[Declared contracts]
      #v(14pt)
      #text(23pt)[Check inputs and returned tables against their schemas.]
      #v(22pt)
      #text(23pt)[Read column meanings, units, and row granularity.]
      #v(25pt)
      #code("runner.inspect()", size: 22pt, height: 65pt)
    ],
    [
      #text(27pt, weight: "bold", fill: teal)[Execution record]
      #v(15pt)
      #set text(size: 22pt)
      #table(
        columns: (170pt, 1fr),
        inset: (x: 12pt, y: 12pt),
        stroke: none,
        fill: (x, y) => if calc.even(y) { pale } else { none },
        [Selection], [B01, B02, B03],
        [Parameters], [Travel-time limit: 45 min],
        [Data revisions], [Declared source revisions],
        [Reused], [Population, jobs, travel times],
        [Executed], [Demand, decay, final score],
      )
    ],
  )
  #talk-note("1:15 · elapsed 13:45")[
    The declarations serve two purposes here. Schemas let the framework check
    inputs and returned tables. Their descriptions, units, and row meanings help
    people interpret those tables and connect them to further analysis.

    The execution record adds context to an individual result. It records the
    accepted selection, effective parameters, conversion routes, and declared
    data revisions. Observations distinguish calculations that executed from
    results that were reused.

    Here we return to the forty-five-minute request. We can see the selected
    blocks and parameter, then separate the reused preparation from the calculations
    that ran again. That helps explain a result and investigate an unexpected change.

    Locally, runner.inspect() provides this information separately from the returned
    tables. Server results expose provenance as well. These same contracts and
    execution records support interfaces that other people can use.
  ]
]

// 15 -------------------------------------------------------------------------
#slide(title: [A shared catalog and a metric playground])[
  #v(18pt)
  #grid(
    columns: (1fr, 1fr),
    gutter: 55pt,
    [
      #text(30pt, weight: "bold", fill: teal)[Catalog]
      #v(20pt)
      Find a method. \ Inspect its inputs and outputs. \ Understand what it measures.
    ],
    [
      #text(30pt, weight: "bold", fill: teal)[Playground]
      #v(20pt)
      Choose locations. \ Adjust parameters. \ Run and visualize results.
    ],
  )
  #v(48pt)
  #lead(size: 29pt)[Shared methods with less onboarding.]
  #talk-note("0:40 · elapsed 14:25")[
    A catalog lets someone find a method and understand its inputs and outputs
    without first finding the person who wrote it.

    A playground gives them a place to choose locations, adjust parameters, and
    visualize the result. Both interfaces use the declarations and execution
    capabilities we have just discussed.

    That makes the lab's methods easier to explore. The same building blocks can
    also support a more specialized application.
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
  #talk-note("0:55 · elapsed 15:20")[
    This is a scenario-planning platform in Mexicali. Users explore a three-dimensional
    map at the scale of houses, lots, and streets. An intervention can be as small
    as a tree or streetlight.

    The platform connects map interactions to metric calculations. Caching lets it
    reuse earlier work while recalculating affected parts, supporting a responsive
    interaction as users explore scenarios.

    The point is that the same reusable methods can sit behind both a research
    interface and a specialized application. Another way to access those methods
    is through an agent.
  ]
]

// 17 -------------------------------------------------------------------------
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
  #text(24pt, fill: teal)[Model Context Protocol (MCP): catalog access and execution.]
  #talk-note("1:00 · elapsed 16:20")[
    The same declarations are useful to agents. The catalog tells an agent what
    metrics exist, and the contracts describe inputs, outputs, and parameters.

    Through the Model Context Protocol, or MCP, an agent can discover a method,
    inspect its contract, submit a request, and retrieve the result. It uses the
    same spatial selection, dependencies, and caching as other clients.

    For example, the agent can request accessibility for all blocks in Mexico City
    through the published interface. The scientific calculation stays inside the
    registered method. The declarations make that method usable through another
    interface, and its tables remain available for further analysis.
  ]
]

// 18 -------------------------------------------------------------------------
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
  #talk-note("0:40 · elapsed 17:00")[
    Once we have an accessibility table, we can investigate how it relates to
    census characteristics. Forward the metric results to DuckDB and make the other
    datasets available there as well.

    An agent can write SQL to join those tables, compare places, or prepare a
    statistical analysis. The framework provides the calculated indicators;
    DuckDB provides a workspace for investigating them.

    That leads to a broader question: could we use these calculations to help
    choose interventions?
  ]
]

// 19 -------------------------------------------------------------------------
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
  #talk-note("1:00 · elapsed 18:00")[
    We can ask what happens when we change an input. The question I find especially
    interesting is the reverse: to improve accessibility, what should we change?

    One direction is differentiable calculations, using gradients through the graph
    to explore changes. Another is an agent that proposes an intervention, runs
    the relevant metrics, and refines its proposal from the results.

    That raises questions about what the agent needs to know: what the metrics
    mean, which changes are feasible, and how we judge an improvement.

    The dependency graph gives us a starting structure. I would be interested in
    your thoughts on what methodological context would make that structure useful
    for choosing interventions.
  ]
]

// 20 -------------------------------------------------------------------------
#slide(title: [Methods that others can keep using])[
  #lead(size: 34pt)[A methodology should outlive its original project.]
  #v(23pt)
  #grid(
    columns: (1fr, 1fr),
    gutter: 70pt,
    qr-slot("https://github.com/RodolfoFigueroa/lyra/", [Code], "images/qr_repo.png"),
    qr-slot("https://rodolfofigueroa.github.io/Lyra/", [Documentation], "images/qr_docs.png"),
  )
  #v(23pt)
  #align(center, text(25pt, fill: teal)[What would you want to make reusable?])
  #talk-note("0:30 · elapsed 18:30")[
    Return to the pipeline on the shelf. The goal is to make its methodology useful
    to people beyond the original project, while keeping its scientific choices
    with the developer.

    The code and documentation are linked here. A starting point is one calculation
    you already use. What would you want to make reusable, and what would help
    others trust and build on it? Thank you.
  ]
]
