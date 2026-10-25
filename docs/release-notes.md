Fix canvas alignment, layer-save warnings and SVG/canvas history after review.

Download the [**TIFview 0.7.1 Windows x64 ZIP**](https://github.com/TruncatedPi/TIFview/releases/download/v0.7.1/TIFview-0.7.1-windows-x64.zip),
extract it and run **TIFview/TIFview.exe**, or rerun `setup.cmd` to update.

- Centred SVG canvas expansion adds symmetric margins and moves the image and
  SVG together. The dialog shows the actual centred size.
- White canvas padding after creating layers on a flat image now explains that
  a merged copy is required. Saving with layers requested fails explicitly;
  undo restores the layered document. Later growth retains the warning.
- In Vectors, undo/redo follows the order of placement and canvas edits. A new
  edit clears obsolete redo entries in the other history too.
- Saved alignment jobs become dirty when their bound image/canvas changes,
  including right/bottom growth with no position shift. Saving only the TIFF
  cannot mark an outdated job clean. Undo can restore its saved image binding.
- Lazy layer loading backfills baseline canvas history; repeated undo/redo
  retains the loaded inventory without replacing rebased layer stacks.
- Known SVG layer-creation limits disable Add with an explanation, while Save
  without SVG remains available.
- Original raster reference points are rebased from the source in one step,
  avoiding floating-point drift during repeated canvas growth after SVG import.
- SVGs whose normalized XML exceeds the 2 MiB limit are rejected at import,
  rather than creating alignment jobs that cannot reopen.

Regression coverage uses synthetic artwork only. Original layer-record byte
verification, native channel readback and explicit merged-copy checks remain
active. The new SVG pixel-layer output still needs Photoshop/PrintExp validation.
