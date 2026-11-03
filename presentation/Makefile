TYPST ?= typst
SOURCE := slides.typ
BUILD := build

.PHONY: all slides handout notes
all: slides handout notes

slides: $(BUILD)/slides.pdf $(BUILD)/slides.pdfpc
handout: $(BUILD)/handout.pdf
notes: $(BUILD)/notes.pdf

$(BUILD):
	mkdir -p $(BUILD)

$(BUILD)/slides.pdf: $(SOURCE) | $(BUILD)
	$(TYPST) compile $(SOURCE) $@

$(BUILD)/slides.pdfpc: $(SOURCE) | $(BUILD)
	$(TYPST) eval 'query(<pdfpc-file>).first().value' --in $(SOURCE) --pretty > $@

$(BUILD)/handout.pdf: $(SOURCE) | $(BUILD)
	$(TYPST) compile --input handout=true $(SOURCE) $@

$(BUILD)/notes.pdf: $(SOURCE) | $(BUILD)
	$(TYPST) compile --input handout=true --input notes=true $(SOURCE) $@
