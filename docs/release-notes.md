Preserve Photoshop layers when neutral extra blending records are present.

Download the **TIFview Windows x64 ZIP**, extract the entire archive, and run
**TIFview/TIFview.exe**. Python is included. Keep the `_internal` folder alongside
the executable. Rerun `setup.cmd` in the source ZIP for automatic updating.

- Spot creation, duplication, deletion and reordering can now retain layers in
  files with neutral extra blending-range records. The previous count-based
  check incorrectly required a merged copy for these default records.
- Original layer data is retained verbatim, including compressed pixels,
  layer masks, settings and opaque Photoshop data.
- Custom extra blending ranges and unsupported channel dependencies still
  require an explicit merged copy. RGB/CMYK/grayscale or image-transparency
  painting also requires a merged copy because layer editing is not implemented.
- Layers are selected for retention whenever supported. The source stays untouched,
  and merged copies still retain all individual spot and mask channels.
- Content Credentials and incomplete-TIFF fixes from v0.3.1 remain available.

Spot creation/deletion/reordering, channel painting, undo/redo, pan and zoom remain
available. The printing preset retains native 8/16-bit channels, LZW compression,
interleaved samples, IBM-PC byte order, DPI, transparency and a rebuilt pyramid.
Supported original Photoshop layer blocks remain intact.

Automated tests and local sample read-back checks verify exact native pixels and
unchanged layer bytes. Check the new layered copy in Photoshop and PrintExp before
production use; those applications have not yet independently validated this fix.
