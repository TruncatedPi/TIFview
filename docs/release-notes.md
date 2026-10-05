View Photoshop layer pixels and save supported layer visibility/order changes.

Download the [**TIFview 0.4.0 Windows x64 ZIP**](https://github.com/TruncatedPi/TIFview/releases/download/v0.4.0/TIFview-0.4.0-windows-x64.zip), extract the entire archive, and run
**TIFview/TIFview.exe**. Python is included. Keep the `_internal` folder alongside
the executable. Rerun `setup.cmd` in the source ZIP for automatic updating.

- The new **Layers** tab lists the stack top first. Select a layer to inspect its
  cached pixels, including hidden layers; select **Layer stack** for a supported
  combined view. Standard pan, zoom, fit and actual-pixel viewing are available.
- Visibility checkboxes and **Move up/down** change the saved stack. **Save TIFF
  copy** retains those changes and regenerates native process/transparency pixels
  so the merged image agrees with the stack. The original TIFF stays untouched.
- **Undo/redo** includes layer changes and can be mixed with channel operations.
  Layer selection follows its stable source identity through reordering.
- Original compressed layer pixels, masks, names and opaque Photoshop data are
  retained. Records move with their compressed chunks; only requested visibility
  flags change. RLE/ZIP layer compression is not replaced with flattened artwork.
- Layer records load lazily, pixels decode on demand, and large previews use
  background work and bounded caches. Pixel painting remains in **Channels**.
- The first compositor supports ordinary Normal raster layers, opacity and
  simple unfeathered bitmap masks. Text/smart objects use saved raster previews.
  Visible adjustments/fill layers, effects, clipping, custom blends/Blend If and
  feathered/vector masks block relevant changes with a reason. Hidden unsupported
  layers remain in the file; groups currently block stack recomposition even
  when hidden. Individual previews explain any unsupported appearance features.
- Direct RGB/CMYK/grayscale or image-transparency painting still requires a
  merged copy because painting underlying Photoshop layer pixels is not implemented.
  Spot/saved-mask edits and supported spot layout changes can retain layers.
- Earlier spot-management, neutral blending-range, Content Credentials and
  incomplete-TIFF fixes remain available.
- PNG/JPEG imports retain print resolution, including rotated and fractional
  DPI, when exported to TIFF. PNG colour-key transparency becomes a native
  transparency plane. Common formats now enforce the same image-size limits
  as TIFF imports.

Spot creation/deletion/reordering, channel painting, undo/redo, pan and zoom remain
available. The printing preset retains native 8/16-bit channels, LZW compression,
interleaved samples, IBM-PC byte order, DPI, transparency and a rebuilt pyramid.
Supported layer records and their original compressed pixel data are retained.

Automated fixtures cover layer pixels, visibility/order, synchronized native
samples, undo/redo and layer-data retention. This new workflow needs a Photoshop
and PrintExp round trip before production use. The earlier tested channel-editing
workflow does not independently validate this layer compositor.
