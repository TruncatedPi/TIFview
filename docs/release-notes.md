Windows prototype with channel viewing, simple raster pixel edits and TIFF-copy export.

Download the **TIFview Windows x64 ZIP**, extract the entire archive, and run
**TIFview/TIFview.exe**. Python is included. Keep the `_internal` folder alongside
the executable. `setup.cmd` in the source ZIP automates download and setup.

- Paint ellipses, boxes, lines and text into a selected process, spot or mask channel.
- Black/white/gray shade, stroke thickness, filled shapes, font and text size controls.
- Exact undo/redo, with original source files protected.
- Save an 8/16-bit TIFF copy: LZW, interleaved, IBM-PC byte order, transparency,
  original DPI/ICC/channel metadata and a rebuilt image pyramid.
- Retain original Photoshop layer bytes for spot/saved-mask edits. Process or
  transparency edits require a merged copy without layers; the save dialog explains this.
- Windows CI checks pixels, navigation, edit history, TIFF saves and layer retention.
  The frozen executable also paints all tools and verifies a layered TIFF round trip.

The supplied CMYK Photoshop TIFF passes local spot-edit/save/reopen checks,
including unchanged other channels and layer bytes. **Opening edited copies
in Photoshop and the target RIP is still unverified.** Full-size Photoshop
display equivalence, layer-aware process editing, reference sidecars and
annotated PNG/PDF exports remain pending. See the README and validation record.
