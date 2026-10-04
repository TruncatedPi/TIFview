# Validation record — 4 October 2026

## Supplied Photoshop file

Source: `D:\Download2\SPacificPrint\jobs\Bailey\Back 360dpi V6a (10%)spot34-wv.tif`.
The original stays in its original directory. A separate edited validation copy
is stored under ignored `validation/local/editing/`; no private TIFF is committed,
uploaded to CI or included in a release.

SHA-256 before and after loading, rendering every channel and selecting all
channels through the Qt UI:

```text
41ba62134be41a13a9b4b3748e51762d73741d5c05ac8cefb62176edaf75b05b
```

The immutable decoded array also remained unchanged after rendering.

| Item | Observation | Status |
|---|---|---|
| Primary image | 1228 × 781, unsigned 8-bit, 9 samples | Decoded and displayed |
| Image encoding | LZW, interleaved, IBM PC/little endian | Verified on this file |
| Process channels | Cyan, Magenta, Yellow, Black | Names/order match Photoshop screenshot |
| Extra 0 | Transparency, TIFF associated alpha, DisplayInfo mode 1 | Distinguished from spots |
| Extra 1–4 | w-front, v-front, w-back, v-all, DisplayInfo mode 2 | Names/order match Photoshop screenshot |
| Solidity | 2%, 2%, 2%, 5% | Read from resource 1077; channel-dialog comparison pending |
| Preview colours | HSB resource components retained; converted for overlays | Read and displayed; Photoshop-dialog comparison pending |
| Pyramid | One 614 × 391 reduction with 9 samples in a SubIFD | Full-resolution parent used; reduction not listed as channels |
| Layers | ImageSourceData present, about 6.5 MiB | Detected; layer pixels not rendered |
| ICC | Embedded CMYK profile, 557168 bytes | Composite conversion to sRGB succeeds |
| Source integrity | Same source hash before/after | Verified |

The supplied Channels-panel thumbnail patterns agree qualitatively with the
generated grayscale views: patterned w-front, small bars on v-front, rectangles
and hearts on w-back, and a mostly uniform dark v-all. This comparison is limited
by thumbnail resolution and does **not** establish per-pixel equality with
Photoshop. The Layers-panel screenshot provides context for the saved composite;
the prototype does not interpret its layer visibility or adjustment layers.

Generated local artifacts are in `validation/local/bailey/`:

- `report.json`: inventory, timings, metadata and source integrity results.
- `channels.png`: contact sheet of composite and all nine stored sample views.
- `sample-0.png` through `sample-8.png`: full-size grayscale channel displays.
- `viewer-composite.png` and `viewer-w-back.png`: actual offscreen Qt UI renders.
- `composite.png`: ICC-converted inspection preview.

These are reference artifacts, not production printing files. Recorded decode
time was approximately 0.08 seconds on this machine, excluding Python startup
and GUI startup; this is not a large-file benchmark.

The native Windows Qt platform was also checked with the supplied file: the
window was visible and exposed, selecting **w-back** displayed a 1228 × 781
pixel image, and the **100%** action showed actual pixels. Gesture checks confirmed
left-button drag panning and mouse-wheel zoom around the pointer. This verifies
the image viewport and navigation rather than only the channel inventory.
The local native screenshot is `validation/local/bailey/viewer-w-back-windows.png`.

## Automated fixtures

The suite checks byte-exact sample retention through 8/16-bit TIFF decode with
uncompressed, LZW, ZIP/Deflate (including Adobe tag 8) and PackBits, both byte orders, and both pixel
orders. It also checks Unicode names, legacy/current DisplayInfo, spot vs alpha
vs transparency, metadata-count mismatches, malformed metadata, name-only
unknowns, premultiplied RGB/CMYK, fixed 16-bit display scaling, orientation,
palettes, pyramids, ICC conversion, common formats and UI selection/zoom.

An opt-in test reads the supplied real TIFF using `TIFVIEW_SAMPLE`; it compares
the expected inventory and saved channel samples to each preview, and checks
source integrity. This confirms the implementation's raw-sample display path,
not independent Photoshop pixel values.

## Remaining Photoshop validation

1. Open the exact sample in Photoshop. Select each spot alone and view at 100%
   without a colour overlay. Compare its full mask against the corresponding
   `sample-5.png` through `sample-8.png`, including edges and intermediate tones.
2. At several shared coordinates, record Photoshop channel values/ink percentages
   and compare with the viewer's raw-value hover inspector. Confirm black=ink
   polarity. Use the same original file without resaving it between comparisons.
3. Open each spot's Channel Options to compare channel type, colour and solidity.
4. Compare the composite under equivalent colour-management settings. Document
   Photoshop's monitor/proof settings; this prototype targets sRGB only.
5. Supply Photoshop-generated RGB+spots, CMYK+spots without transparency, and
   16-bit+spots examples. Generate variants with None/ZIP image compression,
   per-channel storage, Macintosh byte order, and ZIP layer compression. Generated
   codec fixtures already pass, but these real Photoshop save variants remain
   unverified. BigTIFF and very large production files need their own checks.

## Pixel-edit and save validation

Local validation paints an ellipse, box, line and `TIFview TEST` text into
**w-back**. Exact undo/redo restores each previous/next native array. The exported
`validation/local/editing/Bailey-w-back-edited-test.tif` is reopened and checked:

- All nine native sample planes match the edited in-memory document exactly;
  only w-back differs from the source. The other eight planes stay unchanged.
- Channel names/types, preview colours and solidity remain unchanged. Photoshop
  resource blocks are identical except the removed cached thumbnail (1036).
- ICC profile and the 6,853,704-byte Photoshop layer block are byte-identical.
  Original RLE layer compression and unknown layer tags are retained.
- 360 dpi, associated transparency, 8-bit samples, LZW image compression,
  interleaved samples, IBM-PC byte order and classic TIFF are verified.
- A 614 × 391, nine-channel pyramid is rebuilt from the edited pixels.
- Original SHA-256 remains the value recorded above.

The same directory contains `w-back-edited.png`, `viewer-edited.png`,
`save-settings.png` and `report.json`. These are local validation artifacts;
the TIFF deliberately contains test marks in a spot channel.

Automated synthetic tests additionally cover native 16-bit edits, all eight
orientations, clipping, antialiased edges, filled/outline shapes, CMYK shade
polarity, coupled associated-alpha edits, history limits/save checkpoints,
layer-retention restrictions, odd-sized pyramids, cached-thumbnail removal,
external source changes and original/hardlink protection. A GUI test draws
with actual mouse events, uses keyboard undo/redo and saves via the worker.
The packaged executable paints all four tools and saves/reopens a layered
RGB+spot fixture without a Python installation.

The user subsequently confirmed the edited TIFFs work in **Photoshop and
PrintExp (Hosonsoft) for a Refinecolor 6090**, and that the portable app installed
and worked on another computer. Independent pixel measurements, exact application
versions and testing of other TIFF variants are still not recorded. For more files, open
the edited validation copy in Photoshop, compare w-back at 100%, inspect the
other channels and original layers, and check channel options/solidity and
360 dpi. Then import that same copy into the target RIP and verify spot mapping,
resolution and the edited mask. The user identified the target as **PrintExp
(Hosonsoft) for the Refinecolor 6090**; the installed PrintExp version is still
unknown. The [manufacturer's 6090 specification](https://www.refinecolor.com/refinecolor-6090-a1-uv-flatbed-printer-optional-ccd-visual-positioning-system.html)
lists PrintExp and CMYK+W+V, but does not document the TIFF/channel export details
needed to prove this copy's compatibility. Keep the existing spot names and
compare original/edited files using the same PrintExp job settings.
The internal read-back checks alone do not establish compatibility with every RIP/configuration.

## Spot management (v0.3.0)

Create/duplicate/delete/reorder/properties support native 8/16-bit channels and
mixed pixel/structural undo/redo. The 203 local checks include channel IDs,
alternate colours, halftones, Unicode names, alpha/transparency identity through
index shifts, bounded history, GUI selection/visibility and layer dependencies.
The portable executable check repeats the complete changed-layout save path.
Plain grayscale/RGB/RGBA sources can gain a first spot, and deleting the last
spot restores valid single-channel grayscale storage and pyramid dimensions.

Two private sample copies are in ignored `validation/local/spot-management/`:

- `Bailey-spot-reorder-test.tif`: spots are now `w-back`, `w-front`, `v-front`,
  `v-all`. Each native plane and its Photoshop records follow that order.
- `Lion-spot-duplicate-test.tif`: a copy of the native white mask is first,
  followed by the original. A blank mask was also created and deleted to exercise
  those operations. Structural history used about 42 MiB, without whole-image
  history snapshots.

Read-back verifies every output sample. Process/transparency/alpha pixels,
ICC and opaque layer bytes stayed unchanged; both original file hashes stayed
unchanged. The copies use LZW/interleaved/IBM-PC classic TIFF with rebuilt
pyramids. **Independent Photoshop and PrintExp tests of these new channel
layouts remain pending.** Existing pixel-edited TIFFs were previously confirmed
by the user; that confirmation does not cover newly created/reordered spots.
Open these copies in Photoshop, compare mask/name/sequence/solidity and layers,
then check PrintExp's channel mapping using the same RIP job settings.

## TIFF save and incomplete-file fixes (v0.3.1)

A newly supplied Photoshop RGB TIFF reproduced the save failure after adding
a spot. Its full-resolution image has 1318 × 2063 pixels and five samples,
two named spots, 600 dpi and one reduced SubIFD. A second top-level directory
contains only the Content Credentials tag 52545, type 7, with no image pixels.
This metadata directory was incorrectly counted as another independent image.

After the fix, a new spot saved and reopened in about 0.42 seconds. Read-back
verified all five existing native planes, the new empty mask, channel names/types,
600 dpi and a rebuilt pyramid. A Qt check also created and painted a new spot,
verified its displayed pixels, saved asynchronously and checked every output
sample. The source hash stayed unchanged. Private TIFFs, reports and the save
dialog screenshot remain in ignored `validation/local/bugfixes/`.

At v0.3.1, the count-based layer check rejected this source's extra blending
records and required an explicit merged save without layers after adding a
spot. The v0.3.2 investigation below establishes that those records are neutral
defaults. Content Credentials are omitted from rewritten copies and
the dialog explains why. Their signatures are not parsed or independently
validated. Unknown directories and genuinely additional images still block saving.

The local suite passes 227 tests, including empty TIFF errors, failed demo-write
cleanup, credential-only directories and independent-image protection. The
frozen Windows executable passes the existing GUI/edit/save smoke check plus
the same workflow on a generated fixture with a final credential-only directory.
The local header-only demo was recreated and its native channel pixels verified.
Photoshop/PrintExp validation of the new saved sample remains pending.

## Preserve neutral layer blending records (v0.3.2)

The same RGB source has three layers. Each contains five blending-range records
(composite plus four channel entries) even though the image is RGB. Every entry
is exactly `0000ffff0000ffff`: neutral source/destination black/white defaults.
The previous check rejected their count without examining their values. No layer
pixels or known blending restrictions refer to the document's spot samples.

The scanner now accepts neutral extra records while still rejecting custom extra
ranges, embedded extra-channel dependencies and malformed/unsupported data.
A separate private copy adds and paints a new spot while retaining the entire
10,532,480-byte original layer block verbatim. Every native output sample, the
existing process/spot pixels, 600 dpi and rebuilt pyramid are checked; the source
hash stays unchanged. Original layer names are Layer 0, Generative Expand and
Rectangle 1. Private TIFF/report artifacts remain in `validation/local/bugfixes/`.

Regression tests cover neutral/custom extra ranges in both layer byte orders,
later unsafe blocks, mixed spot operations and 8/16-bit saves with unchanged
layer bytes; the local suite passes 258 tests. The save-dialog integration check
also confirms layer retention remains selected after creating a spot. The
packaged smoke fixture now includes these neutral extra records
so the actual executable must retain them through its changed-channel save.
Photoshop/PrintExp comparison of this new layered copy remains pending.

## Large-image performance update

A user-supplied 112.8 MiB TIFF with 3189 × 4606 pixels (84 MiB native samples,
CMYK, associated transparency and one spot) reproduced the reported latency.
On this PC, decoding/ICC setup took about 0.23 seconds, while the previous
renderer took 1.03 seconds for a spot and 5.90 seconds for the composite.
Composite rendering temporarily allocated about 1121 MiB and process RAM
reached about 1298 MiB. More limited RAM on the shop PC could amplify that cost.

Direct grayscale selection measured about 0.105 seconds and revisiting a cached
channel about 0.026 seconds on the same PC. Irrelevant checkbox changes took
about 0.0003 seconds. Bounded render bands reduced composite temporary memory
to tens of MiB while keeping every full-resolution output pixel equal to the
previous renderer. The UI additionally uses a fitted screen-sized colour
preview and background jobs; full resolution is requested on zoom/100%.
These local measurements are not a benchmark of the shop PC.

Final GUI measurements on this PC: open to first fitted composite 0.441 seconds,
first spot selection 0.104 seconds, cached switch with repaint 0.034 seconds,
fitted composite checkbox change 0.195 seconds, and fitted white overlay 0.223
seconds. The first 100% full-resolution composite took 4.15 seconds in the
background. Peak GUI process RAM through the sequence was about 347 MiB.
The fitted preview does not replace native image data; the 100% composite and
full-resolution grayscale hashes matched the previous renderer exactly.

Source hashes/native arrays stayed unchanged. The local profiles and screenshots
are in ignored `validation/local/performance/`; private printing files are not
bundled or uploaded. Tests exhaust all 65536 16-bit grayscale values and compare
single-band/many-band colour previews with ICC, transparency and spot overlays.
GUI regressions cover cached pixels after paint/undo, latest-view cancellation,
load races, close cancellation and fitted/full-resolution coordinate mapping.

Reference annotation objects, sidecars and annotated PNG/PDF export remain
planned. Current drawing tools burn pixels into the selected channel and save
them to a separate TIFF; undo history is not persisted.
