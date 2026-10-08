Correct absolute SVG shape and stroke lengths when fitting a larger TIFF canvas.

Download the [**TIFview 0.6.1 Windows x64 ZIP**](https://github.com/TruncatedPi/TIFview/releases/download/v0.6.1/TIFview-0.6.1-windows-x64.zip),
extract the whole archive and run **TIFview/TIFview.exe**. Rerun `setup.cmd`
from the source ZIP for automatic updating.

SVG strokes or basic shape dimensions written in mm, cm, in, pt or pc now use
correct SVG/CSS user units. Qt previously ignored these suffixes on geometry
and strokes, which could make a preview and its fitted canvas too small for
other SVG viewers. Normalizing these lengths before rendering and exporting
keeps the viewBox, path coordinates, transforms and physical page size intact.
Percentage and font-relative lengths are rejected explicitly instead of silently
misinterpreted. Numeric and px lengths continue to work.

Canvas enlargement still defaults to transparent padding and offers image
centring. Original native pixels, print resolution, ICC and compatible raster
layers are retained. Save the expanded TIFF copy before its alignment job;
SVG remains separate from the TIFF pixels, and source files remain untouched.

The supplied grayscale TIFF/SVG pair passes exact native save/reopen, alpha
padding, retained compressed layer bytes, undo/redo and alignment job checks.
The displayed outline matches its exported SVG. Private validation files stay
under ignored validation/local/. Photoshop, PrintExp and cutter checks of these
new copies remain pending.
