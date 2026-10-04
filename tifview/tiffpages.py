"""Separate printing pixels from narrowly recognised C2PA metadata IFDs."""
from __future__ import annotations

from dataclasses import dataclass

import tifffile


@dataclass(frozen=True)
class TiffPageLayout:
    manifest_only_pages: int
    independent_pages: int
    subifd_reductions: int

    @property
    def pyramid_levels(self) -> int:
        return self.subifd_reductions


def _manifest_only(page: tifffile.TiffPage, last: bool) -> bool:
    """Recognise the last, metadata-only C2PAManifestStore IFD, not image pages.

    C2PA TIFF manifests use tag 52545, type UNDEFINED (C2PA 2.2, A.3.5:
    https://spec.c2pa.org/specifications/specifications/2.2/specs/C2PA_Specification.html).
    Re-encoding or editing
    pixels invalidates the signed manifest, so this one known non-image IFD
    is omitted from copies. Extra tags are deliberately not guessed away.
    """
    if not last or set(page.tags.keys()) != {52545} or page.shape != ():
        return False
    tag = page.tags[52545]
    return int(tag.dtype) == 7 and tag.count > 0


def _same_image_reduction(primary: tifffile.TiffPage, child: tifffile.TiffPage) -> bool:
    """Require an explicit reduced-image flag and unchanged sample semantics."""
    if int(child.tags.valueof(254, 0)) != 1:
        return False
    h, w = int(child.imagelength), int(child.imagewidth)
    height, width = int(primary.imagelength), int(primary.imagewidth)
    return (0 < h <= height and 0 < w <= width and (h, w) != (height, width)
            and int(child.imagedepth) == 1
            and int(child.photometric) == int(primary.photometric)
            and child.samplesperpixel == primary.samplesperpixel
            and child.bitspersample == primary.bitspersample
            and int(child.sampleformat) == int(primary.sampleformat)
            and int(child.tags.valueof(332, 1)) == int(primary.tags.valueof(332, 1))
            and tuple(child.extrasamples) == tuple(primary.extrasamples)
            and int(child.tags.valueof(274, 1)) == int(primary.tags.valueof(274, 1)))


def inspect_page_layout(tif: tifffile.TiffFile) -> TiffPageLayout:
    """Count discardable manifests, rebuildable SubIFDs and extra images.

    Only the primary image and its direct, compatible reduced SubIFDs can be
    rewritten. Other top-level pages and unexpected child IFDs stay protected
    by the writer's independent-page rejection.
    """
    if not len(tif.pages):
        return TiffPageLayout(0, 0, 0)
    primary = tif.pages[0]
    manifests = independent = reductions = 0
    for index in range(1, len(tif.pages)):
        if _manifest_only(tif.pages[index], index == len(tif.pages) - 1):
            manifests += 1
        else:
            independent += 1
    children = primary.pages
    if children is not None:
        for index in range(len(children)):
            child = children[index]
            if _manifest_only(child, index == len(children) - 1):
                manifests += 1
            elif _same_image_reduction(primary, child) and not child.subifds:
                reductions += 1
            else:
                independent += 1
    return TiffPageLayout(manifests, independent, reductions)
