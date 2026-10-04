# Validation record — 4 October 2026

## Supplied Photoshop file

Source: `D:\Download2\SPacificPrint\jobs\Bailey\Back 360dpi V6a (10%)spot34-wv.tif`.
The file stays in its original directory. It has not been copied into the project.

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

Annotations, sidecars, annotated PNG/PDF export and production TIFF editing are
not part of this prototype's verified functionality.
