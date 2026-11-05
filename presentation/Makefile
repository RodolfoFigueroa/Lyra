TYPST ?= typst
SOURCE := slides.typ
BUILD := build
SHARED := style.typ
IMAGES := $(wildcard images/*.png)

.PHONY: all slides handout notes companion
all: slides handout notes companion

slides: $(BUILD)/slides.pdf $(BUILD)/slides.pdfpc
handout: $(BUILD)/handout.pdf
notes: $(BUILD)/notes.pdf
companion: $(BUILD)/companion.pdf

$(BUILD):
	mkdir -p $(BUILD)

$(BUILD)/slides.pdf: $(SOURCE) $(SHARED) $(IMAGES) | $(BUILD)
	$(TYPST) compile $(SOURCE) $@

$(BUILD)/slides.pdfpc: $(SOURCE) $(SHARED) $(IMAGES) | $(BUILD)
	$(TYPST) eval 'query(<pdfpc-file>).first().value' --in $(SOURCE) --pretty > $@

$(BUILD)/handout.pdf: $(SOURCE) $(SHARED) $(IMAGES) | $(BUILD)
	$(TYPST) compile --input handout=true $(SOURCE) $@

$(BUILD)/notes.pdf: $(SOURCE) $(SHARED) $(IMAGES) | $(BUILD)
	$(TYPST) compile --input handout=true --input notes=true $(SOURCE) $@

$(BUILD)/companion.pdf: companion.typ $(SHARED) $(IMAGES) | $(BUILD)
	$(TYPST) compile companion.typ $@
