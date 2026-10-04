Fix saving Photoshop TIFFs that contain Content Credentials metadata.

Download the **TIFview Windows x64 ZIP**, extract the entire archive, and run
**TIFview/TIFview.exe**. Python is included. Keep the `_internal` folder alongside
the executable. Rerun `setup.cmd` in the source ZIP for automatic updating.

- A metadata-only Content Credentials directory no longer causes the false
  "additional independent image pages" error when saving edited spot channels.
- Saved copies omit Content Credentials; TIFview cannot update their signature
  for the edited image. The save dialog explains this. Source files stay untouched.
- TIFFs with genuinely additional images still block export to protect unseen data.
- Empty or incomplete TIFFs now produce a clear "no image data" message.
- Demo generation publishes only a finished TIFF, so failed writes cannot leave
  a header-only demo or damage an existing fixture.

Spot creation/deletion/reordering, channel painting, undo/redo, pan and zoom remain
available. The printing preset retains native 8/16-bit channels, LZW compression,
interleaved samples, IBM-PC byte order, DPI, transparency and a rebuilt pyramid.
Supported original Photoshop layer blocks remain intact.

Automated tests and local sample read-back checks verify these fixes. New channel
layouts still need a user check in Photoshop and PrintExp before production use.
