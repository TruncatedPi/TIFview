# TIFview

A local Windows desktop prototype for inspecting printing images and their
channels. The viewer opens source files read-only. Photoshop remains the tool
for editing production files.

## Run on this computer

Dependencies are already installed in `C:\Source\TIFview\.venv`.
Double-click `launch.cmd`, or run this in PowerShell:

```powershell
cd C:\Source\TIFview
.\.venv\Scripts\python.exe -m tifview
```

Open the supplied Photoshop sample directly:

```powershell
.\.venv\Scripts\python.exe -m tifview 'D:\Download2\SPacificPrint\jobs\Bailey\Back 360dpi V6a (10%)spot34-wv.tif'
```

This is a source prototype, not a packaged executable or installer.

## Set up on another Windows computer

Install **64-bit CPython 3.12** from [Python for Windows](https://www.python.org/downloads/windows/),
including the Python launcher. Then, from the project folder:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m tifview
```

No virtual-environment activation or PowerShell execution-policy change is
needed. Installation downloads the libraries once; the viewer runs locally
without network access, accounts, or cloud services. For errors hidden by the
double-click launcher, use the Python command above to see the console output.

## Using the viewer

- **Open** or drop one image onto the window.
- Select **Composite** for the saved primary TIFF image, converted to a screen preview.
- Select a channel row for grayscale. CMYK process channels show ink as dark;
  Photoshop spot channels show their stored mask, normally black for ink.
- Use **Coloured mask** to inspect coverage on a checkerboard. Choose a display
  colour and opacity independently of the saved Photoshop preview colour/solidity.
- For the composite, tick the extra channels you want and enable
  **Show extra-channel overlays**. Spot overlays start off. Process and
  transparency checkboxes control the composite. Selecting a single channel
  shows it even if its composite checkbox is off.
- **Invert** changes only the selected display. It is useful for checking unknown
  masks; it never changes classification or stored samples.
- Wheel to zoom; drag to pan; **F** to fit; **1** for one source pixel per physical
  screen pixel. The zoom percentage accounts for Windows display scaling.
- Hover over pixels to read original sample values, including 16-bit values.
  Coordinates refer to the displayed orientation.
- **File details** shows decoding information, channel evidence, original preview
  colour components, saved solidity/opacity, and any interpretation warnings.

## What is verified

The supplied `Back 360dpi V6a (10%)spot34-wv.tif` opens successfully as a
**1228 × 781, 8-bit CMYK image** with these stored samples:

| Index | Channel | Classification | Saved solidity |
|---:|---|---|---:|
| 0–3 | Cyan, Magenta, Yellow, Black | Process | — |
| 4 | Transparency | Associated TIFF transparency | — |
| 5 | w-front | Photoshop spot | 2% |
| 6 | v-front | Photoshop spot | 2% |
| 7 | w-back | Photoshop spot | 2% |
| 8 | v-all | Photoshop spot | 5% |

Names and order match the supplied Photoshop Channels-panel screenshot. All
four spot preview patterns agree visually with its small thumbnails. The
transparency sample is stored in the TIFF even though it is not a separate
row in that Photoshop screenshot. It is not counted as a fifth spot.

For this file, decoding is verified for LZW compression, interleaved samples,
IBM-PC byte order, saved transparency, an image-pyramid SubIFD, and the presence
of Photoshop layer data. The source SHA-256 hash stayed unchanged after all
channel previews and UI selections. The embedded CMYK ICC profile is used to
convert the composite to sRGB for display.

**Exact Photoshop pixel/display equivalence remains unverified.** The supplied
screenshots show only small thumbnails, not full-size individual channel
views. This one sample does not establish general Photoshop TIFF compatibility.
See [the validation record](docs/validation.md) for evidence and remaining checks.

Automated fixtures also exercise unsigned 8/16-bit samples, uncompressed/LZW/ZIP
(Deflate)/PackBits image data, both byte orders, interleaved and per-channel
storage, RGB/CMYK/grayscale/palette TIFFs, channel metadata, all eight TIFF
orientations, and transparency. PNG/JPEG/BMP/WebP import and the UI's channel
selection, visibility and zoom controls are tested.

## Current limits

- TIFF's first full-resolution IFD is displayed. Pyramid reductions are not
  treated as new channels. Additional independent pages are reported but not navigable.
- Photoshop layers are detected, and the saved composite is used. Layer
  visibility, adjustment layers, and layer-internal masks are not editable or
  separately rendered. RLE/ZIP **layer** compression is not the same as TIFF
  image compression; the viewer currently does not decode the layer pixels.
- Native samples remain 8/16-bit. The screen preview is 8-bit, with a fixed
  full-range mapping and no automatic contrast stretching.
- ICC conversion targets sRGB, with Pillow/LittleCMS's default perceptual
  intent. Monitor profiling, Photoshop proof settings, spot-ink mixing and
  material-specific white/varnish simulation are not implemented.
- Missing or ambiguous metadata produces **Unknown**, not an inferred spot
  type. Conflicting transparency/spot metadata is reported.
- Lab/YCbCr/multichannel-inkset TIFFs, mixed bit depths, float/signed samples,
  PSD/PSB, and arbitrary Photoshop resource variants are not implemented.
- The prototype loads the whole primary image into RAM. Its current TIFF limit
  is 40 million pixels or 512 MiB decoded samples. Very large printing files
  need a later tile/region reader and viewport rendering. Channel changes are
  synchronous after the background import; large previews can briefly pause the UI.

## Architecture and next stages

PySide6 Essentials supplies the desktop UI; tifffile and imagecodecs decode the
primary TIFF samples; psdtags reads Photoshop resources; a small interpreter
maps names and DisplayInfo types to samples. NumPy retains the original sample
planes, and Pillow supplies common-format import and ICC screen conversion.
The main tradeoff is Python/Qt installation size and full-image RAM use versus
fast development and a channel reader that can be tested independently.
See [architecture and resource rules](docs/architecture.md).

1. **Current:** read-only channel inspection; validate more Photoshop files and
   full-size masks, then improve large-file startup/RAM and package a Windows build.
2. **Planned:** text, ellipses/circles, lines, arrows and rectangles, display colour
   and thickness controls, undo/redo. Store coordinates in source-image space.
3. **Planned:** a versioned `.tifview.json` sidecar with source path, dimensions,
   checksum, annotations and view settings; reopen it without writing to the TIFF.
   Export reference views to PNG/PDF. No production TIFF editing/export is planned.

## Diagnostics and development

Print a channel inventory without opening the UI:

```powershell
.\.venv\Scripts\python.exe -m tifview --inspect 'C:\path\image.tif'
```

Run the automated fixtures:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

Include the exact supplied file in the regression checks:

```powershell
$env:TIFVIEW_SAMPLE = 'D:\Download2\SPacificPrint\jobs\Bailey\Back 360dpi V6a (10%)spot34-wv.tif'
.\.venv\Scripts\python.exe -m pytest -q
```

Export **validation artifacts** (development utility, not the planned annotation exporter):

```powershell
.\.venv\Scripts\python.exe -m tools.validate_sample 'C:\path\image.tif' --output validation/local/sample
```

This creates channel PNGs, a contact sheet, GUI screenshots and a JSON report
in the output directory; it checks the source hash before and after inspection.
Keep this directory separate from the source image.

Generate an explicitly synthetic demo if no production file is available:

```powershell
.\.venv\Scripts\python.exe -m tools.make_demo
.\.venv\Scripts\python.exe -m tifview validation/local/SYNTHETIC-demo.tif
```

The demo generator refuses to overwrite an existing file. Generated images and
local validation files are excluded by `.gitignore`.
