Faster large-file channel viewing and spot-sequence labels.

Download the **TIFview Windows x64 ZIP**, extract the entire archive, and run
**TIFview/TIFview.exe**. Python is included. Keep the `_internal` folder alongside
the executable. `setup.cmd` in the source ZIP automates download and setup.

- Direct grayscale display removes full-image float/RGB expansion when selecting channels.
- A bounded preview cache accelerates revisiting channels/composite views; irrelevant
  checkbox/display changes no longer regenerate grayscale.
- Large colour previews run in cancellable background jobs with much lower temporary
  memory. Fitted views use a screen-sized preview; 100%/wheel zoom requests full resolution.
- Spot labels have a 1-based ordinal among spots, e.g. `1. White Ink`, `2. Varnish`.
  TIFF names, order and native pixels are unchanged.
- Regression checks cover 16-bit grayscale values, exact banded colour previews,
  stale-result cancellation, cache invalidation after editing and file-load races.

The user reports that v0.2.0 edited files work in Photoshop and PrintExp for the
Refinecolor 6090, and installation succeeds on another PC. This update retains
that TIFF writer and pixel-editing path. Shapes/text, undo/redo and saving copies
with retained spot metadata/layers remain available. Full native arrays are still
loaded into RAM; very large files beyond the current limits need tiled loading.
