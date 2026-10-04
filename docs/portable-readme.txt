TIFview - printing image/channel viewer and pixel editor

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

Select one channel, choose Ellipse/Box/Line and drag, or choose Text and click.
Shade 0 paints black (normally ink on spots), 255 paints white for 8-bit images;
16-bit white is 65535. Width/text size are in original image pixels.
Save uses LZW, interleaved samples, IBM-PC byte order, transparency and a pyramid.
Spot/saved-mask edits can retain original Photoshop layers. Process/transparency
edits require a merged copy without layers; review the save dialog.

Spot labels show their relative order, e.g. 1. White Ink, 2. Varnish. Saved names
and channel order are unchanged. Large fitted colour previews render in the
background at screen size; use 100% or wheel zoom for full resolution.
Grayscale channels always show full source pixels. Recent previews are cached.

Edited TIFFs have been tested by the user in Photoshop and PrintExp for the
Refinecolor 6090; the portable app also works on another PC. Reference
annotation sidecars and PNG/PDF exports are planned. See README.md and
docs/validation.md for verified behavior and limitations.

Source, updates and issue reports: https://github.com/TruncatedPi/TIFview
