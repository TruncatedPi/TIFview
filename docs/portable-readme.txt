TIFview 0.8.0 - printing image/channel/layer viewer, SVG alignment and pixel editor

Windows 10/11, 64-bit (x64).

1. Extract the entire ZIP to a folder you can write to.
2. Open the TIFview folder and double-click TIFview.exe.
3. Click Open, or drag a TIFF onto the viewer. Click a channel name to see pixels.

Python and the required libraries are included. No installer, administrator
privileges, Git, account or network connection is needed to run the viewer.
Keep TIFview.exe with its _internal folder; don't move the executable alone.
To update, extract a newer release into a new folder. To remove, delete this
application folder. The original image stays untouched; Save TIFF copy writes
edited channel pixels into a separate file.

Mouse wheel: zoom. Pan tool: drag image. Middle button: pan while drawing.
F: fit. 1: actual pixels. Ctrl+Z: undo. Ctrl+Y: redo. Ctrl+Shift+S: Save TIFF copy.

The running version stays visible in the title and at the bottom right.
Help > About TIFview shows the version and this copy's location.
In Explorer, TIFview.exe > Properties > Details shows File/Product version.

Select one channel, choose Ellipse/Box/Line and drag, or choose Text and click.
Shade 0 paints black (normally ink on spots), 255 paints white for 8-bit images;
16-bit white is 65535. Width/text size are in original image pixels.
Save uses LZW, interleaved samples, IBM-PC byte order, transparency and a pyramid.
Spot/saved-mask edits can retain original Photoshop layers. Process/transparency
edits require a merged copy without layers; review the save dialog.
To draw while retaining compatible existing layers, open Layers, select a
Box/Ellipse/Line/Text tool, choose Colour, draw, then name the new pixel layer.
The viewer switches to the composite so the new drawing is visible immediately.
Each drawing adds a layer with undo/redo, visibility and ordering controls.
RGB drawing colours convert through the embedded ICC profile; failed conversion
reports an error instead of saving unmanaged colour. Use Save TIFF copy with
Retain Photoshop layers enabled. New drawn-layer copies need Photoshop/PrintExp
validation; existing layer visibility/order validation does not cover them.
The Layers tab shows individual saved layer pixels. Its checkboxes and Move
up/down controls change visibility/order in a saved TIFF copy, retaining layer
pixels and updating the native merged image. These changes support undo/redo.
Ordinary Normal raster layers and simple bitmap masks are supported; unsupported
adjustments, effects, groups, blend modes or feathered/vector masks show a reason
and block changes that cannot be saved faithfully. Inspect cached pixels where
available. The user validated the layer test copies in Photoshop and PrintExp.
Solid Photoshop vector shapes can render from closed additive paths, with native
solid fill/stroke colours, physical stroke widths and inside/outside alignment.
Original vector records stay editable in saved TIFF copies. New vector-copy
Photoshop/PrintExp validation is pending; complex vectors/masks remain limited.

Vectors > Import SVG: overlay one self-contained SVG at its physical size.
Use the Vectors tab to position/size in mm, rotate, or show/hide the overlay.
Ctrl+Z/Y in that tab undo/redo alignment and canvas changes in operation order.
Text must be converted to paths;
images, effects, clipping, clones and stylesheets are unsupported.
Save alignment job keeps SVG/placement in a separate .tifview.json. Open the
associated TIFF first, then Open alignment job. Export aligned SVG keeps vectors
on a page matching the TIFF's print size.
Add SVG as layer creates a named Photoshop pixel layer at the TIFF resolution.
Hide/show and reorder it in Layers, then Save TIFF copy with layers retained.
Saving with a pending overlay offers to add it instead of silently omitting it.
Known layer-creation limits disable Add with an explanation; Save without SVG
remains available. White padding that requires flattening is shown explicitly.
Grayscale documents create gray layers; CMYK/RGB use the document colour mode
and ICC profile. The original spot masks remain unchanged. Unsupported layer
composites require saving/reopening a merged copy before adding artwork.
Canvas size on the toolbar accepts width/height with a nine-position anchor,
or separate left/top/right/bottom growth. Pixels or mm; transparent by default.
Smaller-canvas cropping is not yet implemented.
Save TIFF edits first, then save the alignment job to associate with that copy.
SVG alignment and saved pixel layers are tested with synthetic files and the
supplied TIFF/SVG pair; new Photoshop/PrintExp and print/cut validation is pending.
Content Credentials metadata does not block TIFF saving. Credentials are omitted
from saved copies because this app cannot update their signature after editing.

Use the Spots menu or right-click a channel to create, duplicate, rename/change
properties, move up/down or delete spots. A new mask is empty (white/no ink).
Changes support undo/redo and Save TIFF copy. Reordering changes the saved ink
sequence; masks/names/metadata move together. Process/transparency/alpha channels
are protected. Saved preview colour/solidity do not set printer ink density.
Check the numbered order in Photoshop and PrintExp before using a changed copy.

Spot labels show their relative order, e.g. 1. White Ink, 2. Varnish. Large fitted colour previews render in the
background at screen size; use 100% or wheel zoom for full resolution.
Grayscale channels always show full source pixels. Recent previews are cached.

Pixel-edited TIFFs have been tested by the user in Photoshop and PrintExp for the
Refinecolor 6090; the portable app also works on another PC.
The new spot count/order/property workflow has internal read-back checks and
still needs a Photoshop/PrintExp round-trip check. Unsupported channel-linked
metadata is rejected; some layered structures require a merged copy.
Reference annotation sidecars and PNG/PDF exports are planned. See README.md and
docs/validation.md for verified behavior and limitations.

Source, updates and issue reports: https://github.com/TruncatedPi/TIFview

SVG larger than the image: choose Expand and import, Keep canvas or Cancel.
Transparent new areas are the default; White is optional. Keep image position
or centre it. No original pixels are resized. Spot padding contains no ink.
Expand canvas to SVG repeats fitting after alignment changes. Canvas growth has
exact undo/redo and saves into a separate TIFF copy; save that TIFF first, then
the alignment job. Compatible raster layers can be retained; vector/dependent
layers, shifted masks or white padding can require a merged copy. The save
dialog explains this. New enlarged copies need Photoshop/PrintExp validation.
