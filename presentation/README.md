# Reusable geospatial methodologies

The main talk has 20 slides and 18 minutes 30 seconds of speaker notes,
leaving a buffer within the 20-minute presentation slot before five minutes
of questions.
Progressive reveals add pages to the presentation PDF while retaining the
logical slide numbers.

## Build

Use Typst 0.15.1 or later and Touying 0.8.0. The package is pinned in the source.
The typeface is Avenir Next, with Arial as a fallback. Code uses Menlo, with
DejaVu Sans Mono as a fallback.

From the repository root:

    make -C presentation

This writes the following files under presentation/build/:

- slides.pdf: the presentation, including progressive reveals.
- slides.pdfpc: speaker notes and overlay metadata for pdfpc.
- handout.pdf: one final state per slide.
- notes.pdf: the timed speaker notes with slide previews.
- companion.pdf: a six-page, self-contained conference companion for color printing.

To compile only the presentation:

    typst compile presentation/slides.typ presentation/build/slides.pdf

The palette and font families shared by the slides and companion live in
style.typ. All slide diagrams are native Typst drawings in slides.typ. Their helper functions
are grouped near the top of the file so their geometry, labels, and colors can
be changed together. No external image-generation scripts or assets are needed.

## Screenshots

Slide 16 displays images/mexicali.png by default. To use another screenshot,
set the mexicali entry in the screenshots mapping near the top of slides.typ
to its path relative to presentation/.

The path can also be supplied at compilation time:

    typst compile --input mexicali=assets/mexicali.png \
      presentation/slides.typ presentation/build/slides.pdf

The image preserves its full contents and aspect ratio. The Mexicali space is
approximately 1.74:1. The catalog and playground use text descriptions.

## Closing links

Slide 20 displays images/qr_repo.png in the left Code column and
images/qr_docs.png in the right Documentation column, with a clickable URL
below each image. The images also link to the repository and documentation
homepage at https://rodolfofigueroa.github.io/Lyra/. Keep the
encoded QR destinations and clickable links consistent when replacing images.

## Edit and rehearse

Slide text, diagrams, and speaker notes all live in slides.typ. Each talk-note
starts with its target duration and cumulative elapsed time. The displayed
slides keep explanatory detail in the spoken delivery.

For a one-slide-per-page PDF, pass --input handout=true. Add --input notes=true
to render the notes with slide previews. The Makefile provides both variants.

The build tracks the shared style and PNG assets, so changes to those files
rebuild the affected outputs. For images supplied through a custom path, force
a rebuild with make -B -C presentation.

## Printed conference companion

The companion is a standalone document in companion.typ. It follows the talk's
accessibility example and includes explanatory diagrams, an annotated metric,
and a runnable exercise using the bundled city plugin. Edit its prose and
print-specific diagrams independently of the slides.

To build only the companion:

    make -C presentation companion

Print build/companion.pdf in color on US Letter paper at actual size (100%),
with double-sided printing and long-edge binding. Its six portrait pages use
three sheets; no booklet imposition is needed. The slide handout remains a
separate output containing one final state per slide.

When editing, keep the six explicit page sections and check the rendered PDF
for page overflow. Update the document date in the footer when preparing a new
edition. Keep printed URLs, PDF links, and encoded QR destinations consistent.
