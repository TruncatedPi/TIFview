Add manual canvas sizing and saved SVG pixel layers.

Download the [**TIFview 0.7.0 Windows x64 ZIP**](https://github.com/TruncatedPi/TIFview/releases/download/v0.7.0/TIFview-0.7.0-windows-x64.zip),
extract it and run **TIFview/TIFview.exe**, or rerun `setup.cmd` to update.

**Canvas size** on the toolbar lets you enter width/height with a nine-position
anchor or add independent amounts to the left, top, right and bottom. Use pixels
or millimetres. Padding defaults to transparent; original pixels are not resampled.
Undo/redo and saved copies retain the chosen size. Cropping is not implemented.

**Add SVG as layer** renders the imported artwork into a named Photoshop pixel
layer at the TIFF resolution. It appears in Layers and supports visibility,
ordering and undo/redo. Save TIFF copy retains this layer and a matching composite.
Saving with a pending SVG offers to add it; omission is an explicit choice.

Colours use the image's colour mode and ICC profile. Existing process pixels
outside the artwork and every spot/saved-alpha plane are preserved. Compatible
original layer records and compressed pixels remain intact. Layer creation needs
orientation 1 and an accurately supported existing composite; unsupported stacks
or independent process/transparency painting need a merged-copy round trip first.
The SVG must fit the canvas. New layers are raster artwork, not vector smart objects.

The supplied grayscale TIFF/SVG pair now saves with its original raster layer
and a separate outline layer, plus explicit edge padding. Native saved pixels,
original compressed layer channels, hidden-outline restoration, undo/redo and
unchanged source hashes pass. New files still need Photoshop/PrintExp validation.
Private files remain under ignored validation/local/.
