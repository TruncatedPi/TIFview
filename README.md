# TIFview

[![Windows checks](https://github.com/TruncatedPi/TIFview/actions/workflows/windows.yml/badge.svg)](https://github.com/TruncatedPi/TIFview/actions/workflows/windows.yml)

A local Windows desktop prototype for inspecting printing images and painting
simple shapes and text into individual process, spot and mask channels. Edits
change channel pixels in memory; **Save TIFF copy** writes a separate file.
The original image stays untouched. The user has tested edited TIFFs successfully
in Photoshop and PrintExp (Hosonsoft) for a Refinecolor 6090, and installed the
portable app on another PC. This confirms that workflow with the tested files.

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

Click **w-back** in the channel-name column to see its full mask in the main
image area. Click **100%** to view actual pixels, use the mouse wheel to zoom,
and drag the image to pan. Click **w-front**, **v-front** or **v-all** to inspect
the other spots; click **Composite** to return to the colour image.

## Set up on another Windows computer

**Windows 10/11, 64-bit (x64). Python is included in the portable download.**

1. Open [Downloads / latest release](https://github.com/TruncatedPi/TIFview/releases/latest).
2. Download **TIFview-0.2.1-windows-x64.zip** and extract the entire ZIP.
3. Open the extracted **TIFview** folder and double-click **TIFview.exe**.

Keep the `_internal` folder with the executable. No Python installation, Git,
administrator access or account is needed. The viewer runs offline after download.
To update, extract the next release into a new folder; to remove it, delete the
application folder. Source images remain untouched.

For **automatic setup and a desktop shortcut**, download the
[source ZIP](https://github.com/TruncatedPi/TIFview/archive/refs/heads/main.zip),
extract it, and double-click **setup.cmd**. It downloads the latest Windows
release, verifies its SHA-256 checksum, extracts it to
`%LOCALAPPDATA%\TIFview\<version>`, creates a TIFview desktop shortcut, and
launches the app. Run it again to install a newer release. It does not install
Python or change your system-wide PowerShell execution policy.

### Python version and source setup

**Python 3.12 is the minimum for the pinned dependencies**, specifically NumPy,
tifffile and imagecodecs. It also happens to be the version used for the initial
local prototype (3.12.14). It means **3.12**, not Python 12.
Windows CI checks **3.12 and 3.13**, and the portable release bundles **3.13**.
Other Python versions are not part of the current test matrix.

There is no need to upgrade your working Python 3.12 environment to use this
viewer. For a fresh source/development setup, use **64-bit Python 3.13** from
[Python for Windows](https://www.python.org/downloads/windows/), including the
Python launcher. Download/extract the source ZIP or clone the repository, then
run these commands in its folder:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\launch.cmd
```

Use `py -3.12` instead if keeping an existing Python 3.12 installation.
No virtual-environment activation is needed. For console diagnostics, run
`.\.venv\Scripts\python.exe -m tifview` instead of the double-click launcher.

## Using the viewer

- **Open** or drop one image onto the window.
- Select **Composite** for the saved primary TIFF image, converted to a screen preview.
- Select a channel row for grayscale. CMYK process channels show ink as dark;
  Photoshop spot channels show their stored mask, normally black for ink.
- Spot labels show their relative **1-based sequence**, for example **1. w-front**,
  **2. v-front**, **3. w-back**, **4. v-all**. Process, transparency and saved-mask
  channels do not count. This changes the label only; exported names/order stay intact.
- Use **Coloured mask** to inspect coverage on a checkerboard. Choose a display
  colour and opacity independently of the saved Photoshop preview colour/solidity.
- For the composite, tick the extra channels you want and enable
  **Show extra-channel overlays**. Spot overlays start off. Process and
  transparency checkboxes control the composite. Selecting a single channel
  shows it even if its composite checkbox is off.
- **Invert** changes the selected preview. Paint shades follow that displayed
  grayscale polarity; toggling Invert alone does not change stored pixels.
- Wheel to zoom; drag with the **Pan** tool; **F** to fit; **1** for one source pixel per physical
  screen pixel. The zoom percentage accounts for Windows display scaling.
- Hover over pixels to read current channel sample values, including 16-bit values.
  Coordinates refer to the displayed orientation.
- **File details** shows decoding information, channel evidence, original preview
  colour components, saved solidity/opacity, and any interpretation warnings.

## Paint and save channel pixels

1. Select a single channel, such as **w-back**. Composite is a viewing mode.
2. Choose **Ellipse**, **Box** or **Line**, then drag over the image. For **Text**,
   click its position and enter the text. Width and text size use original image
   pixels, so zooming does not change their saved size. Choose the font from the list.
3. Set **Shade**: **0 = black**, **255 = white** for 8-bit images, or **65535 = white**
   for 16-bit images. Intermediate values paint gray. For Photoshop spots,
   black normally adds ink and white removes it. **Filled** fills boxes/ellipses;
   otherwise only their outlines are painted. Edges are antialiased.
4. Use **Undo** / **Redo** (**Ctrl+Z**, **Ctrl+Y** or **Ctrl+Shift+Z**).
   Choose **Pan** to drag the view, or use the middle mouse button while drawing.
5. Click **Save TIFF copy…** (**Ctrl+Shift+S**), review its layer settings and
   choose a new filename. Saving reopens the temporary TIFF and verifies it
   before publishing the copy. The source filename cannot be overwritten.

Drawing selects grayscale. Choosing a coloured-mask preview returns to Pan.
Paint affects the selected plane; it is **raster pixel editing**, not a removable
annotation object. Undo history is kept in memory, up to 128 MiB of patches.
Closing/reopening the saved TIFF retains the pixels, but does not retain shapes
as separately editable objects or the undo history.

The save preset matches the supplied Photoshop options: **LZW image compression,
interleaved samples, IBM-PC byte order, image pyramid and transparency**, with
BigTIFF off. Native bit depth, channel names/types, spot preview colours/solidity,
ICC profile and print resolution are retained. Cached Photoshop thumbnails are
removed after edits so they cannot show the old pixels; pyramid pixels are rebuilt.

**Photoshop layers are retained for spot-channel and saved-alpha-mask edits**
when the source uses IBM-PC byte order. The original layer block is copied
verbatim, retaining its RLE/ZIP compression and unknown Photoshop layer data.
The app does not edit or recompose these layers. If CMYK/RGB/grayscale process
pixels or composite transparency change, the original layers would contain a
different image. The save dialog therefore requires a **merged copy without
Photoshop layers**, while keeping all process, spot and mask channel pixels.
Undo those edits to retain layers. Macintosh layer blocks also currently require
a merged copy when exporting to the IBM-PC preset.

Associated transparency needs coupled process samples: painting its alpha plane
rescales the premultiplied process values, while painting a process plane clamps
it to alpha. Other spot/mask planes stay untouched. Exact undo restores all
affected samples.

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

The sample also passes an edited-copy round trip: changes to **w-back** reopen
with exact native pixels; the other eight planes, ICC profile and Photoshop layer
bytes stay unchanged. Channel-resource blocks stay unchanged except for removed
thumbnail caches. The saved copy has 360 dpi, LZW/interleaved storage,
little-endian byte order, transparency and a rebuilt 614 × 391 pyramid.
The user reports the edited TIFF workflow works in Photoshop and PrintExp.
The installed Photoshop/PrintExp versions and an independent per-pixel display
comparison have not been recorded.

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
- Saving supports unsigned 8/16-bit RGB, CMYK and grayscale images. Palette and
  1-bit TIFFs remain viewable but cannot be edited/saved. TIFFs with additional
  independent image pages cannot be saved, to avoid discarding unseen pages.
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
  need a later tile/region reader. Grayscale channels use a direct 8-bit screen
  plane (with rounded full-range conversion for 16-bit sources). A bounded 96 MiB
  cache accelerates revisiting views and is invalidated after edits/undo/redo.
  Large colour previews run in cancellable background jobs with bounded working
  memory. Rapid view changes keep only the latest requested result.
  While fitted, large composite/coloured views use a screen-sized preview mapped
  to the original source coordinates. **100% or wheel zoom requests full resolution**;
  the preview updates when ready. Individual grayscale channels always use full
  source resolution. Pixel inspection and editing always use the native samples.
  Edits and saves wait for a background colour preview to finish or cancel, so
  it cannot read pixels while they are being changed. Channels, pan and zoom stay usable.
  The first edit also copies the native sample array; saving needs memory for
  verification and the pyramid.

## Architecture and next stages

PySide6 Essentials supplies the desktop UI; tifffile and imagecodecs decode the
primary TIFF samples; psdtags reads Photoshop resources; a small interpreter
maps names and DisplayInfo types to samples. NumPy retains the original sample
planes, and Pillow supplies common-format import and ICC screen conversion.
The main tradeoff is Python/Qt installation size and full-image RAM use versus
fast development and a channel reader that can be tested independently.
See [architecture and resource rules](docs/architecture.md).

1. **Current:** channel inspection, ellipse/box/line/text raster edits, undo/redo,
   verified TIFF-copy export and portable Windows packaging. Validate edited
   copies on more Photoshop/RIP configurations and collect more real save variants.
2. **Planned:** layer-aware process editing and tiled loading beyond the current RAM limits.
3. **Planned:** a versioned `.tifview.json` sidecar with source path, dimensions,
   checksum, reference annotations and view settings; reopen reference markup
   without burning it into channel pixels. Export reference views to PNG/PDF.

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

[GitHub Actions](https://github.com/TruncatedPi/TIFview/actions/workflows/windows.yml)
runs these checks on pull requests, pushes to `main`, version tags and manual
runs. The Windows 3.12/3.13 jobs check dependency consistency, original sample
preservation, channel names/types, TIFF storage options, actual displayed
channel pixels, overlays, wheel zoom, drag panning and view preservation when
switching channels. They also check shape/text pixels, exact 8/16-bit undo/redo,
saved channel values/metadata, layer-retention rules, pyramids and source-file
protection. Synthetic fixtures run in CI; your production TIFF is not
uploaded. Test reports are saved as workflow artifacts.

After both test jobs pass, a clean Windows job builds a self-contained executable,
opens a synthetic LZW TIFF in that executable, checks spot/alpha names and
displayed channel pixels, and creates the portable ZIP plus SHA-256 file.
It also paints all four tools, undoes/redoes them, and saves/reopens a layered
spot-channel TIFF with its ICC profile.
Version tags publish the checked ZIP to GitHub Releases. See
[build and release instructions](docs/building.md). These checks do not establish
pixel-exact equivalence with Photoshop.

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
