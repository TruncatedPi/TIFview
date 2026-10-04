Create, delete, reorder and duplicate Photoshop spot channels.

Download the **TIFview Windows x64 ZIP**, extract the entire archive, and run
**TIFview/TIFview.exe**. Python is included. Keep the `_internal` folder alongside
the executable. `setup.cmd` in the source ZIP automates download and setup.

- Use the **Spots** menu or right-click a channel row. New spots start empty
  (white/no ink); duplication copies every native mask pixel.
- Move spots up/down to change the saved relative ink sequence. Numbered labels,
  names, pixels and Photoshop channel records stay together. Process,
  transparency and saved alpha channels are protected.
- Spot properties edit the name, saved preview colour and solidity. These are
  separate from temporary overlay controls and do not set printer ink density.
- Undo/redo covers channel operations and painting together, with bounded history.
- TIFF copies retain native 8/16-bit data, channel IDs/types/colours, ICC, DPI,
  transparency and a rebuilt pyramid using the existing LZW/interleaved/IBM-PC preset.
- Layer retention checks reject additional-channel dependencies rather than
  copying inconsistent layer data. Supported layer bytes remain untouched.
- Quick Mask, unknown channel mapping and unsupported/custom halftones block
  structural changes with an explanation.

Automated and sample TIFF read-back tests verify the new channel layout and
preserved pixels/metadata. **Creation/deletion/reordering still need a new user
round-trip check in Photoshop and PrintExp.** The previously confirmed pixel-edit
workflow remains available, along with fast channel previews, pan and zoom.
