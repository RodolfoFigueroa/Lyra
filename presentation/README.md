# Reusable geospatial methodologies

The main talk has 19 slides and 19 minutes 45 seconds of speaker notes.
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

To compile only the presentation:

    typst compile presentation/slides.typ presentation/build/slides.pdf

All diagrams are native Typst drawings in slides.typ. Their helper functions
are grouped near the top of the file so their geometry, labels, and colors can
be changed together. No external image-generation scripts or assets are needed.

## Screenshots

Slides 15 and 16 contain spaces for the metric catalog, metric playground, and
Mexicali scenario-planning application. Add your images under presentation/
and set the screenshots mapping near the top of slides.typ to their relative
paths. For example, use "assets/mexicali.png" in place of the corresponding
sys.inputs.at(...) value.

Paths can also be supplied at compilation time:

    typst compile --input catalog=assets/catalog.png \
      --input playground=assets/playground.png \
      --input mexicali=assets/mexicali.png \
      presentation/slides.typ presentation/build/slides.pdf

Images preserve their full contents and aspect ratio. The two lab-tool spaces
are approximately 1.75:1; the Mexicali space is approximately 1.74:1.

## Edit and rehearse

Slide text, diagrams, and speaker notes all live in slides.typ. Each talk-note
starts with its target duration and cumulative elapsed time. The displayed
slides keep explanatory detail in the spoken delivery.

For a one-slide-per-page PDF, pass --input handout=true. Add --input notes=true
to render the notes with slide previews. The Makefile provides both variants.

After adding or replacing screenshots, rebuild the relevant output with
make -B -C presentation so changed image files are included.
