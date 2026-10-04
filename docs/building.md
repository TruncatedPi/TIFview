# Windows checks and distribution

The single workflow `.github/workflows/windows.yml` keeps three concerns small:
Windows tests on Python 3.12 and 3.13, a portable build after tests succeed,
and publication on matching version tags. Pull requests get the same checks.
Only the release job has repository write permission. No secrets need to be set.

The tests use generated fixtures, including Photoshop resource structures.
They cover raw samples, 8/16-bit decoding, spot versus alpha/transparency,
Qt channel pixels, visibility, wheel zoom, drag pan and keeping the viewport
when selecting another channel, raster shapes/text, undo/redo and safe TIFF-copy
export with channel metadata, ICC, layers, transparency, DPI and pyramids.
They also cover spot sequence labels, preview cache invalidation after edits,
exact 16-bit grayscale conversion and cancellation of stale background previews.
The optional private production-file tests are
skipped in GitHub; set `TIFVIEW_SAMPLE` locally to run it.

## Build locally

Use a clean 64-bit Windows Python 3.13 environment so unrelated installed
packages do not inflate the executable. The same script works on tested 3.12.

```powershell
py -3.13 -m venv build/venv
.\build\venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\build\venv\Scripts\python.exe -m tools.build_windows
```

PyInstaller creates a windowed executable and a replaceable `_internal` runtime
folder, rather than unpacking the entire runtime into a temporary directory on
every launch. The app needs no Python on the destination PC. The distribution
includes dependency license notices. It is a prototype without code signing.

The bundler uses an isolated PATH containing the selected Python and Windows
directories. This prevents similarly named DLLs from unrelated installed tools
(for example Poppler's ICU library) from being bundled in place of Qt's Windows
dependencies.

The build script starts **the actual frozen executable** outside the source
folder and opens a known LZW TIFF with named White Ink, Varnish and alpha masks.
It checks Qt pixels at known coordinates, image dimensions, 100%/zoom/fit,
immutable original samples and the source hash. It also paints ellipses, boxes,
lines and text into a spot, checks exact undo/redo and displayed pixels, and
saves/reopens a TIFF copy preserving its RLE layers and ICC profile with a
rebuilt pyramid. A failure stops packaging.
It also verifies spot sequence labels and runs the background preview/cancellation
path, comparing every displayed composite pixel with the expected render.
The fixture and report stay in `build/`, not in the shipped app.

Outputs: `dist/TIFview-<version>-windows-x64.zip`, a `.zip.sha256` file,
and `build/package-check.json`. All generated output is ignored by Git.

## Publish a version

Update `tifview/__init__.py` with the next version and commit the change.
Push a matching `v<version>` tag; for example, the initial prototype is `v0.1.0`.
The build checks that the tag matches the application version. Tests and the
executable check must pass before the workflow creates the release and uploads
the download. If a job fails, its logs and saved reports show why; no release
is created by that failed run.

For a pre-release build without publishing, use **Run workflow** on the Actions
page and download its `windows-portable` artifact. Public release assets are
the installation path for users without a GitHub account.

## Automatic setup helper

`setup.cmd` invokes `tools/install_windows.ps1` with a process-scoped execution
policy. The helper downloads the latest release, verifies its published SHA-256,
extracts to a new version folder, makes a desktop shortcut and opens the viewer.
It reuses an already installed version and leaves old version folders in place.

To test the helper in an isolated folder without a shortcut or launching:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/install_windows.ps1 -InstallRoot C:\path\test-install -NoShortcut -NoLaunch
```

The helper requires internet access during setup and a published Windows
release. It reports download/checksum errors and stops; it does not fall back
to installing a separate Python runtime.
