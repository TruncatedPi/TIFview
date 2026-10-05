Fix large layer visibility changes failing with "Layer change exceeds the 128 MiB
undo limit" after a long wait. Layer undo now stores lossless compressed native
pixel differences in bounded row bands, rather than two full pixel rectangles.
The 128 MiB history budget stays in place, and undo/redo remains exact.

Download the [**TIFview 0.4.3 Windows x64 ZIP**](https://github.com/TruncatedPi/TIFview/releases/download/v0.4.3/TIFview-0.4.3-windows-x64.zip),
extract the entire archive, and run **TIFview/TIFview.exe**. Python is included.
Rerun `setup.cmd` from the source ZIP for automatic updating.

Compositing avoids repeated array-index copies, associates transparency using
bounded integer bands, and copies native colours directly for a single visible
layer. The initial exact baseline check is retained; later edits reuse it.

On the supplied 7200 x 2160, 8-bit CMYK sample, the formerly failing toggle took
about 24 seconds before this fix. It now succeeds in about 10.5 seconds here;
a later toggle takes about 4.1 seconds. Undo/redo takes 0.2-0.4 seconds, with
roughly 0.7-0.9 MiB per undo record. Timing depends on the computer and file.

Both visibility test copies retain the original compressed Photoshop layer
pixels and white spot samples. Native saved pixels and exact undo/redo are
verified, and the source file stays unchanged. Private copies and reports stay
in ignored validation/local; Photoshop/PrintExp checks of these new copies
remain for the user. Synthetic regressions cover compressed history, mixed
pixel edits, 8/16-bit values, orientation, masks, opacity and sampled previews.
