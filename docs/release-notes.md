Find the version of the running app and each installed Windows copy.

Download the [**TIFview 0.4.1 Windows x64 ZIP**](https://github.com/TruncatedPi/TIFview/releases/download/v0.4.1/TIFview-0.4.1-windows-x64.zip),
extract the entire archive, and run **TIFview/TIFview.exe**. Python is included.
Rerun `setup.cmd` from the source ZIP for automatic updating.

- The window title begins with **TIFview 0.4.1**, including after opening a file.
  A permanent version label at the bottom right remains visible during work.
- **Help → About TIFview** shows the version and the running copy's location.
  The location can be copied to identify an older shortcut or installation.
- README line 1 identifies the release. The build rejects a mismatched header.
- **TIFview.exe → Properties → Details** in Explorer now shows File version and
  Product version. Both strings and numeric version fields are generated from
  the app version and checked against the finished executable before release.
- Automatically created desktop shortcuts include the version in their description.
- The user validated both layer visibility/order test copies in **Photoshop and
  PrintExp** on the shop PC for the Refinecolor 6090. This records the tested
  sample workflow; unsupported Photoshop layer features remain restricted.

Channel/layer viewing, pixel edits, spot management, supported layer stack edits,
undo/redo and TIFF-copy saving retain their existing behavior. Original images
stay untouched; no private validation files are bundled or uploaded.

Windows checks cover Python 3.12/3.13, actual portable GUI pixels, layer/channel
edits, verified TIFF saves, displayed versions and embedded Windows EXE metadata.
