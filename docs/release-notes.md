Reliable TIFF saving, real-file layer/canvas/SVG workflow fixes, and a
Photoshop-style dark studio workspace.

Download the [**TIFview 0.9.0 Windows x64 ZIP**](https://github.com/TruncatedPi/TIFview/releases/download/v0.9.0/TIFview-0.9.0-windows-x64.zip),
extract it and run **TIFview/TIFview.exe**, or rerun `setup.cmd` to update.

### Saving reliability & real-file workflow fixes
- **Save always available while previews render:** Clicking **Save TIFF copy…**
  (`Ctrl+S` or `Ctrl+Shift+S`) during a background preview interrupts the preview
  and queues the save automatically instead of staying disabled.
- **Clear recovery when files change on disk:** Saving prompts whether to
  continue if only the source file timestamp changed after opening; explicit
  overwrite saves to an existing copy path keep the original source protected.
- **Faster optional save mode:** Uncheck *Verify saved pixels after writing* in
  the save dialog to skip full pixel read-back verification on large jobs.
- **Larger image and layer budgets:** Image, spot, and layer limits increased
  from 40 MP / 512 MiB to **150 million pixels / 1024 MiB** decoded samples.
- **Unblock incompatible layer stacks:** Added **Flatten to single layer** in
  Layers (and **Flatten old layers to add SVG** / **Flatten old layers & add SVG**
  when saving/adding SVGs on incompatible stacks or expanded vector canvases).
- **Canvas growth & soft-alpha fixes:** Unlayered flat TIFFs can grow their canvas
  before or after adding SVG/drawn layers while preserving generated layers;
  fixed read-only array crashes and CMYK soft-alpha composite matching.
- **Broader SVG import support:** Accepts Inkscape/Illustrator metadata,
  `zoomAndPan`, and safe internal `<style>` class rules.

### Photoshop-style workspace & ergonomics
- **Dark studio theme & dock layout:** Image canvas on the left/centre with
  docked **Channels**, **Layers**, and **Vectors** tabs on the right, top menu
  bar (`File`, `Edit`, `Image`, `Layer`, `Spots`, `Vectors`, `View`, `Help`), and
  a synced left vertical tool strip (`Move`, `Box`, `Ellipse`, `Line`, `Text`).
- **Quick navigation & canvas SVG positioning:** Hold **Spacebar** to pan while
  a drawing tool is active; use the **Move** (`Pan`) tool to drag an imported SVG
  directly on the canvas; use **Center on canvas** and **Fit inside canvas**.
- **Layer thumbnails & stack controls:** Layer rows display checkerboard
  miniature thumbnails, support drag-and-drop reordering, a right-click context
  menu, an **Isolate selected layer** toggle, and a **+ SVG…** shortcut button.

Validation: **782 local pytest checks pass** (`3 skipped` for unset optional
`TIFVIEW_SAMPLE` override), covering Pass 1 saving/layer/canvas/SVG fixes and
Pass 2 workspace/UI ergonomics, plus **29 checks** on the frozen local Windows
executable. This is a trial release: newly flattened, expanded, SVGs-as-layers,
and drawn-layer TIFF copies still need verification in Photoshop and PrintExp.
The original source image stays untouched.

