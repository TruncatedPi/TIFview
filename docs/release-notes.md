Read-only Windows prototype for inspecting printing images and their channels.

Download the **TIFview Windows x64 ZIP**, extract the entire archive, and run
**TIFview/TIFview.exe**. Python is included; no installation or administrator
access is required. Keep the `_internal` folder alongside the executable.

- Composite and individual process, spot and alpha channel pixels.
- Mouse-wheel zoom, drag to pan, fit and actual-pixel view.
- Windows tests for 8/16-bit TIFF decoding, Photoshop channel metadata,
  immutable samples and Qt display. The packaged executable also opens a
  synthetic LZW spot-channel fixture and verifies its displayed pixels.

Photoshop interpretation remains experimental. The supplied real TIFF's names,
types and small channel previews were checked, but exact full-size Photoshop
display equivalence is unverified. Annotations and layer-pixel viewing are not
implemented. See the README and validation record for current limits.
