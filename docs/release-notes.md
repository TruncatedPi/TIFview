Add basic vector support and TIFF/SVG alignment.

Download the [**TIFview 0.5.0 Windows x64 ZIP**](https://github.com/TruncatedPi/TIFview/releases/download/v0.5.0/TIFview-0.5.0-windows-x64.zip),
extract the entire archive, and run **TIFview/TIFview.exe**. Rerun `setup.cmd`
from the source ZIP for automatic updating. Python and Qt SVG are included.

Supported Photoshop solid shapes render from their closed additive paths even
when the layer has no cached raster pixels. Solid fill/stroke colours, enable
flags, physical widths, opacity, caps/joins and inside/outside alignment are
interpreted. Layer visibility/order copies retain the original editable vector
records and compressed layer pixels, and leave spot masks unchanged. Colour
spaces must match the TIFF process mode. Complex fills, vector masks on raster
layers, dashed strokes and other unsupported styles still report a reason.
The exact original-composite check remains in place for visible layer baselines.

Use **Vectors > Import SVG** for one static path/shape overlay. Position and size
use millimetres calibrated by image DPI, with rotation, visibility and alignment
undo/redo. A separate `.tifview.json` saves the SVG and alignment; **Export aligned
SVG** preserves vectors on a page matching the TIFF physical size. Save image
edits with **Save TIFF copy** separately, then save the job for that saved copy.
The SVG overlay is not burned into the TIFF or converted to a Photoshop layer.

SVG text needs outlines. Unsupported images/effects, clipping, gradients,
stylesheets, clones and geometry outside the SVG page are rejected explicitly.
Physical size, SVG viewport mapping, rotated/non-square DPI, displayed/exported
alignment, native 8/16-bit shape samples, original vector records, undo/redo and
source protection have automated checks. The portable build also checks its
actual Qt SVG renderer, job/export and native shape rendering.

The supplied rectangle now displays and can be shown/reordered in test copies.
Their native merged samples, unchanged spots, original layer records and source
checksum are verified. **Photoshop/PrintExp validation of these new vector copies
is pending**, as is a representative real TIFF/SVG print-and-cut workflow.
Antialiased vector edges may differ from Photoshop; some already-visible vector
stacks can fail the exact baseline check and remain unavailable for layer edits.
