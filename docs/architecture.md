# Read-only architecture

The prototype uses a local Python 3.12 / PySide6 Essentials app. The UI and
metadata interpreter are separate from the document and rendering modules:

```text
TIFF -> tifffile + imagecodecs -> immutable H × W × samples array
                  |
                  +-> primary IFD tags + Photoshop ImageResources
                       -> psdtags + bounded DisplayInfo interpretation
                       -> names, types, colour values, saved solidity

ImageDocument -> display-only orientation / 8-bit preview -> Qt image view
              -> read-only inventory + original pixel-value inspection
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
layer pixels. The primary TIFF IFD supplies the merged image and extra samples.

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
