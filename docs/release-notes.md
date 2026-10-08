Add an option to enlarge the TIFF canvas when importing a larger SVG.

Download the [**TIFview 0.6.0 Windows x64 ZIP**](https://github.com/TruncatedPi/TIFview/releases/download/v0.6.0/TIFview-0.6.0-windows-x64.zip),
extract the whole archive and run **TIFview/TIFview.exe**. Rerun `setup.cmd`
from the source ZIP for automatic updating.

The import dialog offers **Expand and import**, **Keep canvas**, or **Cancel**.
New areas default to **Transparent**, with optional White padding and image
centring. The full SVG page and stroke/geometry bounds are included, even when
geometry extends beyond its own viewBox. Position, scale and pixel values are
preserved; neither image nor SVG is resampled. The Vectors tab also has
**Expand canvas to SVG** for fitting after alignment changes.

Canvas undo/redo crops and recreates padding, avoiding full-image snapshots.
Spot padding has no ink; native 8/16-bit samples, channel names/order, image
transparency/association, ICC, print resolution and TIFF orientation are kept.
Save the expanded TIFF copy first, then its alignment job. Source files remain
untouched. SVG continues to stay separate from TIFF pixels.

Compatible ordinary raster layers can be retained, with translated rectangles
and reference points when centring; their compressed pixels remain exact.
Vector/dependent layers, shifted raster masks, unsupported appearances, white
padding or nonstandard layer orientation may require a merged copy. The TIFF
save dialog explains the specific limitation before saving.

Native checks on the supplied large CMYK sample confirm unchanged original
channels, transparent/no-ink padding, retained raster layer pixels and positions,
saved-copy readback, exact undo/redo and unchanged source checksum. These new
copies still need **Photoshop/PrintExp validation**. Synthetic and portable
checks cover canvas sizing, 8/16-bit data, orientation, alpha association,
layer retention, mixed history, SVG alignment and save/reopen.

Expanded TIFF copies also update standard XMP TIFF/Exif pixel dimensions in
attributes or elements, retaining other properties and namespace prefixes.
This avoids stale original dimensions in the supplied Photoshop TIFF metadata.
