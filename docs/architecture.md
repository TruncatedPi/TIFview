# Channel/layer viewer and pixel-editor architecture

The prototype uses a local Python / PySide6 Essentials app (Python 3.12 minimum;
Windows CI tests 3.12/3.13). The UI and
metadata interpreter are separate from the document and rendering modules:

```text
TIFF -> tifffile + imagecodecs -> immutable H × W × samples array
                  |
                  +-> primary IFD tags + Photoshop ImageResources
                       -> psdtags + bounded DisplayInfo interpretation
                       -> names, types, colour values, saved solidity
                  +-> lazy Photoshop ImageSourceData layer records
                       -> cached native layer pixels + original compressed chunks

ImageDocument -> display-only orientation / 8-bit preview -> Qt image view
              -> read-only inventory + current pixel-value inspection
              -> copy on first edit -> selected-plane raster patches -> undo/redo
              -> layer visibility/order -> native merged recomposition -> undo/redo
                                    -> TIFF copy -> reopen/verify -> publish
```

Additional common formats use Pillow. It preserves decoded RGB/CMYK/gray/alpha
planes where available; indexed non-TIFF formats expand their palette for
ordinary image viewing. TIFF palettes retain their index sample and colormap.
There is no OpenImageIO dependency in this milestone.

This stack is easier to inspect and revise while validating Photoshop resources
than starting with a C++ application. C++/Qt/libtiff remains an option if measured
packaged startup, installer size or RAM requires it. nomacs and ImageJ were not
chosen because their ability to interpret these specific spot-channel resources
was not established. No claim is made that they cannot do it.

## Resource interpretation

The Adobe ImageResources TIFF tag is **34377**. Photoshop ImageSourceData,
including layers, is **37724**, which the viewer detects without loading those
layer pixels during initial image import. Opening the Layers tab starts a separate
lazy reader. The primary TIFF IFD supplies the merged image and extra samples.

- Resource **1045**: Unicode extra-channel names, preferred over **1006** Pascal names.
- Resource **1077**: version 1 DisplayInfo, 13-byte records: colour space,
  four 16-bit colour components, 16-bit opacity/solidity, and one-byte mode.
- Resource **1007**: legacy 14-byte records with a trailing pad byte.
- Modes **0/1** are alpha-mask display modes; mode **2** denotes a spot channel.
  Alpha masks are kept separate from composite transparency.
- TIFF ExtraSamples **1/2** denotes associated/unassociated transparency.
  It must not be treated as ink solely because its name suggests white/varnish.
- RGB, HSB, inverse CMYK, Gray and Lab resource colours have approximate RGB
  previews. Unsupported colour-book IDs retain original components and use an
  explicit fallback display colour.

Names and DisplayInfo are independently mapped only if their counts match either
all extra samples or exactly the extra samples excluding explicit transparency.
Count mismatches are withheld; arbitrary partial lists are never zipped to
samples. The all-extras layout is observed in the supplied real file, including
its implicit Transparency record. The transparency-excluded layout is tested
with generated fixtures and still needs real Photoshop examples.

Resource framing is checked before psdtags decoding because a partial resource
stream can otherwise terminate silently. Duplicate channel-name/DisplayInfo
resources are treated as ambiguous. Other duplicate/unknown resource IDs may be
retained. Resource IDs come from the raw framing so an unknown enum value cannot
lose its original numeric identifier.

Grayscale spot preview uses stored values (0 black, maximum white). Spot overlay
coverage assumes `1 - sample/maximum`, and shows that assumption in the UI.
The saved solidity is displayed as metadata; it does not silently set the
inspection overlay opacity. This keeps 2% solidity masks legible. An inspection
overlay does not reproduce Photoshop's spot-ink compositing algorithm.

TIFF CMYK samples use increasing values for increasing ink. Process grayscale
inverts these for the conventional dark-ink separation view while hover values
always report the original data. Associated native process samples are divided
by alpha before conversion, then optionally composited onto a checkerboard.
Zero-alpha pixels have no recoverable underlying colour and use zero native
samples. Multiple independent transparency samples need additional validation.

The embedded ICC is retained. RGB/CMYK/gray composite data can be converted to
sRGB with Pillow/LittleCMS; otherwise the viewer reports a fallback. Channels
themselves are never passed through the profile. Display is 8-bit even when
source samples are 16-bit. No monitor profile or soft-proof simulation is used.

## Primary sources

- [Adobe Photoshop File Formats Specification](https://www.adobe.com/devnet-apps/photoshop/fileformatashtml/)
  — ImageResources, channel names, TIFF tags and colour structures.
- [tifffile documentation and source](https://github.com/cgohlke/tifffile)
  — raw TIFF decoding, planar configurations and extra samples.
- [psdtags source](https://github.com/cgohlke/psdtags)
  — Photoshop TIFF resource and layer structures. DisplayInfo is currently an
  opaque byte block in this dependency, so the application interprets it.
- [psd-tools image-resource implementation](https://github.com/psd-tools/psd-tools/blob/main/src/psd_tools/psd/image_resources.py)
  and [mode constants](https://github.com/psd-tools/psd-tools/blob/main/src/psd_tools/constants.py)
  — reference for current DisplayInfo record structure and mode meanings.
- [Molecular Matters PSD SDK](https://github.com/MolecularMatters/psd_sdk/blob/master/src/Psd/PsdParseImageResourcesSection.cpp)
  — independent DisplayInfo interpretation reference.

Dependency versions are pinned to the tested Windows environment. These source
references guide the implementation; they do not substitute for representative
Photoshop-generated TIFF testing.

## Pixel edits and TIFF copies

Qt rasterizes ellipses, boxes, lines and text into an 8-bit antialiasing coverage
mask in displayed source-pixel coordinates. `EditSession` blends this mask into
the selected native 8/16-bit plane with 64-bit integer arithmetic. TIFF
orientation is a view, so edits map back into the stored orientation without
rotating or resampling the whole image. Uncovered samples stay exact. Original
samples remain immutable; the first real edit creates a separate writable copy.

Undo/redo records before/after pixel patches, bounded to 128 MiB. State IDs track
the save checkpoint even when the user undoes, saves or creates a new branch.
Associated-alpha edits rescale premultiplied process samples; process edits
are bounded by alpha. These coupled changes are included in the same undo patch.

The writer uses unsigned 8/16-bit native samples with LZW, horizontal prediction,
interleaved storage, little-endian byte order and classic TIFF. It retains the
source orientation, ICC, print resolution, transparency types, channel resource
blocks, XMP/IPTC and attribution tags. Photoshop thumbnail caches (1033/1036)
are dropped after pixel changes; other resource blocks are copied verbatim for
pixel-only edits. Spot layout/properties changes rewrite the linked records below.
Pyramid reductions average 2x2 native samples and are rebuilt with corresponding
reduced DPI. Additional independent pages are rejected rather than discarded.

TIFF Content Credentials can occupy a final metadata-only IFD containing just
tag 52545 (type 7), as described by the
[C2PA TIFF specification](https://spec.c2pa.org/specifications/specifications/2.2/specs/C2PA_Specification.html#_embedding_manifests_into_tiff_based_assets).
`tiffpages.py` distinguishes this known metadata directory from an additional
image. It also checks primary SubIFDs before rebuilding their reduced images;
unknown image directories still block export. Content Credentials are omitted
from rewritten copies because TIFview cannot update their signatures. The save
dialog explains this, and the original TIFF remains untouched. Empty TIFFs with
no image directories fail with an actionable error instead of an IndexError.

Photoshop ImageSourceData is kept as an opaque byte block for spot/saved-mask
edits to little-endian files when visibility/order does not change. This retains
unknown layer tags and existing RLE or ZIP data without decode/reserialize losses.
Supported layer visibility/order changes rewrite record order and visibility
flags while retaining compressed channel chunks and opaque data; their merged
process/transparency samples are regenerated in native bit depth. Direct painting
of process or image-transparency channels would make the original layer composite
stale, so the UI disables layer retention and explicitly offers a merged copy
with every channel. Big-endian
layer blocks currently require the same merged-copy option because the export
preset uses IBM-PC byte order. Painting the pixels inside a Photoshop layer is
future work.

The original path and hardlink aliases cannot be export destinations. The source
file's stat signature must still match the opened document. A temporary TIFF is
written beside the chosen destination, reopened and checked for native pixels,
bit depth, orientation, channel names/types/display info, resources, ICC, layers,
resolution, encoding and pyramid pixels. Only a successful check publishes the
copy. Editing is disabled during this background save; failures retain in-memory
edits and leave an existing destination intact. These checks establish internal
round-trip integrity, not independent Photoshop or RIP compatibility. The user
subsequently confirmed the v0.2.0 workflow works in Photoshop and PrintExp for
the Refinecolor 6090; v0.2.1 leaves the TIFF writer/editing path intact.

## Spot-channel layout changes

`spots.py` returns immutable documents for new/duplicate/delete/move/properties.
Reorder permutes only spot slots; deleting a spot shifts later sample positions
while logical identities keep transparency and alpha masks attached to their
original data. Native unsigned 8/16-bit samples, stored orientation and base
process planes are retained. New masks contain maximum sample values (no ink).
Stable edit-session identities retain channel selection, visibility and display
colour overrides. Added/duplicated spots have new source identities.

Structural history stores metadata snapshots, permutations and one added/deleted
plane; it shares the 128 MiB limit with raster patches. It stores no full-image
snapshots. Undo/redo restores original resource bytes, channel properties, pixels
and save checkpoints exactly, including mixed structural and pixel operations.

Resource rewriting preserves unrelated raw blocks and their names/padding.
1006/1045 names and 1007/1077 display records are rebuilt consistently. Unicode
lengths count UTF-16 code units. ExtraSamples is remapped with each plane.
1053 IDs are raw big-endian uint32 arrays in both supplied TIFFs and the
[psd-tools implementation](https://github.com/psd-tools/psd-tools/blob/main/src/psd_tools/psd/image_resources.py).
Retained IDs move with their channels; new/duplicated spots get distinct nonzero
IDs, and 1044's shared document seed advances monotonically. 1067 alternate spot
colours are matched by ID, duplicated/filtered as necessary; changing a spot's
preview colour drops its stale alternate. Photoshop thumbnails are removed.

1043 in both supplied files has a v6 uint16 version/count header and 18-byte
halftone records (`>IHihIBB`). Supported records move/copy with their spots;
new masks use the observed Photoshop default. This observed layout differs from
the abbreviated Adobe table. Unsupported/custom shapes, trailing data and count
mismatches are rejected. Quick Mask references and ambiguous channel mappings
also block structural changes. Generic opaque print descriptors remain verbatim.

The writer maps process/transparency planes by original identity before deciding
whether layers remain consistent. On spot count/order changes, `layercheck.py`
scans bounded 37724 headers without decoding pixels or opaque tagged payloads.
It rejects embedded extra-channel pixels, Alph blocks, additional-channel
restrictions/custom blending ranges and malformed/unsupported framing. Excess
blending-range records are allowed only when both source and destination have
the exact neutral black/white defaults (`0000ffff0000ffff`). These records do not
restrict blending; no layer payload is rewritten or normalized. Supported
layer bytes are copied verbatim. Both supplied blocks pass this check.
These internal checks do not establish a new Photoshop/PrintExp round trip.

## Layer inspection, visibility and order

`layers.py` reads bounded ImageSourceData headers and channel spans without
interpreting opaque descriptor payloads. `Layer.index` is a stable source-record
identity; public order lists are top first, while TIFF layer records are stored
bottom first. The Layers tab preserves selected identities across changes and
undo/redo. Individual previews show cached layer pixels on the original canvas
even when their visibility checkbox is off. Painting remains a Channels operation.

Layer records are loaded lazily. Channel pixels decode only when a preview or
recomposition needs them, with a bounded cache and decoded-memory checks.
Large previews run in background jobs. Supported RAW, RLE and ZIP layer samples
are converted to native TIFF polarity for RGB/CMYK/grayscale rendering; screen
ICC conversion still happens only after native compositing.

The conservative compositor handles ordinary Normal layers, opacity and simple
unfeathered bitmap masks. Text and smart-object layers can use their saved raster
pixels while their editable records remain opaque. Adjustment/fill layers,
effects, clipping, groups, custom blend modes/Blend If ranges, feathered/vector
masks and other unsupported dependencies carry explicit reasons. Unsupported
visible layers block relevant changes and saves instead of being approximated.
Hidden unsupported records can remain untouched; groups block stack recomposition
even when hidden. An individual preview can show available cached pixels with
its limitations, but does not synthesize unsupported effects or adjustments.

A supported visibility/order operation produces native process samples and an
unassociated alpha plane in stored orientation. The edit session maps these back
to the existing primary-image layout, including association with TIFF transparency
where required. Spot and saved-alpha planes keep their native values. Layer state
and resulting sample changes participate in the same undo/redo history and save
checkpoint as channel operations. A rejected operation leaves accepted state intact.

Before the first edit to a supported baseline, its recomposed native process and
existing transparency samples must exactly reproduce the authoritative original
primary image. A mismatch blocks editing with a reason rather than publishing an
approximate replacement. The result is cached against the original sample array.
An explicit hide of unsupported baseline appearance features is still allowed
when the resulting visible stack is supported; this deliberately changes those
features instead of approximating their previous appearance. Groups remain blocked.

Layer rewriting moves each original record with its original compressed channel
chunks, toggles only the requested hidden flag, and preserves the remainder of
ImageSourceData. It does not rasterize text into the stored layer, decode/resave
compressed layer pixels, or normalize opaque Photoshop tags. Export verifies the
rewritten block and the synchronized native primary pixels in the new TIFF copy.
The original remains untouched. This feature still needs independent Photoshop
and PrintExp verification; internal read-back does not establish matching Photoshop
compositing or production compatibility.

## Large-image previews

Grayscale channels copy only one uint8 plane; uint16 uses a rounded full-range
65536-entry lookup table. The Qt view accepts grayscale directly, avoiding
float/RGB expansion. Colour previews process 65536-pixel row bands with original
checkerboard coordinates, in-place arithmetic and one final RGB output. This
bounds temporary memory while preserving full-resolution preview pixels.

The UI caches at most 96 MiB of preview arrays. Keys contain only settings that
affect displayed pixels; single-channel grayscale ignores composite visibility,
overlay colours and opacity. A document/edit generation prevents reuse across
files or edits. All entries are invalidated after painting, undo and redo.

Large fitted colour views sample the native document at a stride sized to 1.5
times the viewport's physical pixel density. The graphics item maps that preview
to the unchanged source scene dimensions. Grayscale always uses full resolution;
100% or wheel zoom requests a full-resolution colour preview. Source inspection,
shape coordinates, editing and export always use the full native document.

Colour jobs above two million source pixels run in one background thread.
Changing the requested view interrupts work between bands and replaces the
pending request with the newest one. A job result is installed only for its
matching document generation/settings. Load-time old-file controls cannot start
previews. Edit/save operations are disabled until a running preview finishes or
cancels, preventing reads during in-place edits; channel selection/pan/zoom remain
available. Closing cancels a preview safely before the normal unsaved-edit check.


## Basic vector support (v0.5.0)

`psvectors.py` selectively reads bounded vector-mask paths and solid content/
stroke descriptors using Adobe's file-format specification. Unknown descriptors
stay opaque. The layer parser never deserializes all Photoshop metadata, and
rewriting still moves original record/channel chunks with only visibility-bit
changes. Supported shapes use QPainterPath and bounded coverage bands, retaining
native 8/16-bit process colours; they are not imported through an RGB flatten.
Closed additive solid shapes are admitted only when their colour space matches
the TIFF. Complex masks, fills and styles stay unsupported. Original visible
baselines still require exact native agreement; antialiasing mismatches block
editing rather than silently replacing appearance.

`svg.py` uses the Qt SVG module already provided by PySide6 Essentials. It admits
self-contained static geometry with explicit physical page dimensions, bounded
XML/geometry, validated paths/transforms and understood inline styles. Qt ignores
SVG clipping/nested viewports, so clipping and out-of-page geometry are rejected
on import. Export expresses the viewport mapping as ordinary affine groups,
without raster images or clipping dependencies. The CSS/SVG 96 px/in rule maps
unitless/px pages; image DPI separately maps millimetres into displayed samples.

The QGraphicsItem remains a vector at each zoom. The Vectors panel has separate
bounded placement history; the active tab selects SVG versus image undo/redo.
The versioned JSON job embeds SVG plus placement and binds it to an image hash,
dimensions and calibrated DPI. It stores no image edits. An atomic separate SVG
export uses the image's physical page size. Original TIFF/SVG inputs are protected.
TIFF output retains the existing channel/layer workflow and does not contain the
imported SVG. Real vector-copy/cutter validation remains a separate milestone.

References: [Adobe Photoshop formats](https://www.adobe.com/devnet-apps/photoshop/fileformatashtml/),
[Qt SVG rendering](https://doc.qt.io/qt-6.11/qsvgrenderer.html), and
[SVG viewport units](https://www.w3.org/TR/SVG2/coords.html#Units).
