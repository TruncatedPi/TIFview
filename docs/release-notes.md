Draw shapes and text onto new Photoshop pixel layers while retaining compatible
existing layers.

Download the [**TIFview 0.8.0 Windows x64 ZIP**](https://github.com/TruncatedPi/TIFview/releases/download/v0.8.0/TIFview-0.8.0-windows-x64.zip),
extract it and run **TIFview/TIFview.exe**, or rerun `setup.cmd` to update.

- In Layers, choose Box, Ellipse, Line or Text, select a colour and draw. Name
  the new layer; hide/show, reorder and undo/redo it using the existing controls.
- New artwork appears immediately in the composite even when an individual
  layer was selected before drawing.
- RGB colours convert into the document's colour mode through its embedded ICC
  profile. Conversion errors stop the operation with a clear message.
- Native 8/16-bit RGB, CMYK and grayscale images are supported. Drawing uses
  source-pixel coordinates and does not require calibrated print DPI.
- Save TIFF copy retains compatible original layer records and compressed
  pixels alongside the drawn layers, with a matching merged image. Spot and
  saved-selection channel samples remain separate and unchanged by drawing.
- Files without Photoshop layers gain an Original image base layer. Existing
  orientation, supported-composite and layer-preservation restrictions apply.
- Channels-tab painting remains available for editing individual process,
  white-ink, varnish and saved-mask samples.

Validation: 773 local tests pass, including the existing private samples.
The packaged check adds a drawing after SVG import and canvas growth, verifies
visible pixels and unchanged spot/selection planes, saves/reopens the layered
TIFF and checks undo/redo. The local executable passes all 28 packaged checks.

New drawing-workflow TIFF copies still need Photoshop/PrintExp validation.
The source image stays untouched; private validation files remain ignored.
