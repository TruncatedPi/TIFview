"""Verified TIFF copies, retaining native samples and Photoshop channel tags."""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import os
from pathlib import Path
import tempfile

import numpy as np
import tifffile

from . import __version__
from .model import ImageDocument
from .photoshop import without_thumbnails
from .reader import file_signature, load_image
from .tiffpages import inspect_page_layout


@dataclass(frozen=True)
class SaveOptions:
    keep_layers: bool = True
    pyramid: bool = True


def channel_sources(original: ImageDocument, edited: ImageDocument) -> list[int | None]:
    sources = edited.metadata.get("channel_source_indices")
    if sources is None:
        if len(edited.channels) != len(original.channels):
            raise ValueError("Use spot channel management to change the channel layout")
        sources = list(range(len(original.channels)))
    if len(sources) != len(edited.channels) or any(
            source is not None and (not isinstance(source, int) or not 0 <= source < len(original.channels))
            for source in sources):
        raise ValueError("Invalid channel source identities")
    return list(sources)


def _validate_channel_layout(original: ImageDocument, edited: ImageDocument):
    if (edited.samples.shape[:2] != original.samples.shape[:2] or edited.bits != original.bits or
            edited.samples.dtype != original.samples.dtype or edited.orientation != original.orientation or
            edited.color_mode != original.color_mode or edited.base_count != original.base_count):
        raise ValueError("Image dimensions, bit depth, orientation and colour mode must remain unchanged")
    if len(edited.channels) != edited.samples.shape[-1] or any(c.index != i for i, c in enumerate(edited.channels)):
        raise ValueError("Channel metadata does not match the sample planes")
    extras = edited.metadata.get("extra_samples")
    if any(c.associated and c.kind != "Transparency" for c in edited.channels):
        raise ValueError("Only image transparency can use associated TIFF ExtraSamples")
    expected_extras = [1 if c.associated else 2 if c.kind == "Transparency" else 0
                       for c in edited.channels[edited.base_count:]]
    if extras is not None and (list(extras) != expected_extras or
                              any(not isinstance(value, int) or isinstance(value, bool) for value in extras)):
        raise ValueError("TIFF ExtraSamples must match channel transparency and association")
    sources = channel_sources(original, edited)
    for before in original.channels:
        if before.kind == "Spot":
            continue
        matches = [i for i, source in enumerate(sources) if source == before.index]
        if len(matches) != 1:
            raise ValueError("Process, transparency and saved alpha channels cannot be created, deleted or duplicated")
        after = edited.channels[matches[0]]
        if (after.name, after.kind, after.display, after.associated) != (
                before.name, before.kind, before.display, before.associated):
            raise ValueError("Only spot channel properties can be changed")
        if before.index < original.base_count and matches[0] != before.index:
            raise ValueError("Process channels must retain their original order")
    for channel, source in zip(edited.channels, sources):
        generated_alpha = (channel.kind == "Transparency" and not channel.associated and channel.display is None
                           and channel.name == "Transparency" and edited.metadata.get("layer_generated_transparency")
                           and edited.metadata.get("layer_composite_applied") and edited.layer_stack is not None
                           and edited.layer_state is not None and
                           not any(c.kind == "Transparency" for c in original.channels) and
                           sum(c.kind == "Transparency" for c in edited.channels) == 1)
        if source is None and channel.kind != "Spot" and not generated_alpha:
            raise ValueError("Only spot channels can be added")
        if source is not None and original.channels[source].kind == "Spot" and channel.kind != "Spot":
            raise ValueError("Spot channel types must remain intact")
    retained = [source for source in sources if source is not None]
    if len(set(retained)) != len(retained):
        raise ValueError("Duplicated spots require their own channel identity")
    locked = [c.index for c in original.channels if c.kind != "Spot"]
    if [source for source in sources if source in locked] != locked:
        raise ValueError("Process, transparency and saved alpha channels must retain their relative order")
    return sources


def layer_preservation_reason(original: ImageDocument, edited: ImageDocument) -> str | None:
    if not original.metadata.get("has_photoshop_layers"):
        return None
    if original.metadata.get("byte_order") != "IBM PC (little endian)":
        return "Preserving Macintosh-byte-order layer blocks in an IBM-PC TIFF is not implemented."
    try:
        sources = _validate_channel_layout(original, edited)
    except ValueError as exc:
        return str(exc)
    if edited.metadata.get("layer_composite_applied"):
        from .layerediting import merged_pixels_match, validate_state
        if edited.layer_stack is None or edited.layer_state is None:
            return "The edited layer stack is unavailable."
        try:
            validate_state(edited.layer_stack, edited.layer_state)
            reason = edited.layer_stack.composite_reason(edited.layer_state.order, edited.layer_state.visible)
        except ValueError as exc:
            return str(exc)
        if reason:
            return reason
        if not merged_pixels_match(edited):
            return "CMYK/RGB or transparency pixels were painted separately from the edited Photoshop layers."
    else:
        protected = list(range(original.base_count)) + [c.index for c in original.channels if c.kind == "Transparency"]
        if any(not np.array_equal(original.samples[..., index], edited.samples[..., sources.index(index)]) for index in protected):
            return "CMYK/RGB or transparency pixels changed. The original Photoshop layers still contain the old image."
    if sources != list(range(len(original.channels))):
        from .layercheck import spot_structure_layer_reason
        cached_key = "spot_structure_layer_check"
        if cached_key not in original.metadata:
            if original.layer_stack is not None:
                data = original.layer_stack.data
            else:
                with tifffile.TiffFile(original.path, mode="r") as tif:
                    data = bytes(tif.pages[0].tags[37724].value)
            original.metadata[cached_key] = spot_structure_layer_reason(data, original.base_count)
        return original.metadata[cached_key]
    return None


def reduce_half(samples: np.ndarray) -> np.ndarray:
    """Average each 2x2 native-sample block, including partial edge blocks."""
    h, w, count = samples.shape
    sums = np.zeros(((h + 1) // 2, (w + 1) // 2, count), dtype=np.uint32)
    weights = np.zeros(sums.shape[:2], dtype=np.uint32)
    for y in range(2):
        for x in range(2):
            block = samples[y::2, x::2]
            sums[:block.shape[0], :block.shape[1]] += block
            weights[:block.shape[0], :block.shape[1]] += 1
    return ((sums + weights[..., None] // 2) // weights[..., None]).astype(samples.dtype)


def save_tiff_copy(original: ImageDocument, edited: ImageDocument, filename: str | Path,
                   options: SaveOptions = SaveOptions(), overwrite: bool = False) -> Path:
    """Write and reopen a temporary copy before atomically publishing it."""
    source = original.path.resolve()
    destination = Path(filename).resolve()
    if destination == source or (destination.exists() and os.path.samefile(source, destination)):
        raise ValueError("Save an edited copy under a new name. The original image cannot be overwritten.")
    if destination.suffix.lower() not in (".tif", ".tiff"):
        raise ValueError("Choose a .tif or .tiff filename")
    if destination.exists() and not overwrite:
        raise FileExistsError(f"{destination.name} already exists")
    _validate_channel_layout(original, edited)
    if edited.bits not in (8, 16) or edited.color_mode not in ("RGB", "CMYK", "Gray", "WhiteIsZero"):
        raise ValueError("TIFF export supports unsigned 8/16-bit RGB, CMYK and grayscale images")
    signature = original.metadata.get("source_signature")
    if signature is not None and file_signature(source) != signature:
        raise ValueError("The original file changed after opening. Reopen it before saving.")
    reason = layer_preservation_reason(original, edited)
    if options.keep_layers and reason:
        raise ValueError(reason + " Save a merged copy with Photoshop layers unchecked.")
    extra_tags = [(274, 3, 1, edited.orientation, False)]
    resources = edited.photoshop_resources
    if resources and not np.array_equal(original.samples, edited.samples):
        resources = without_thumbnails(resources) or None
    layer_bytes = None
    resolution = ((72, 1), (72, 1))
    unit = 2
    levels = 1
    if original.metadata.get("backend") != "Pillow":
        with tifffile.TiffFile(source, mode="r") as tif:
            page = tif.pages[0]
            layout = inspect_page_layout(tif)
            if layout.independent_pages:
                raise ValueError("Saving TIFFs with additional independent image pages is not implemented")
            resolution = (page.tags.valueof(282, (72, 1)), page.tags.valueof(283, (72, 1)))
            unit = int(page.tags.valueof(296, 2))
            levels = max(1, layout.pyramid_levels)
            if options.keep_layers and 37724 in page.tags:
                layer_bytes = bytes(page.tags[37724].value)
                if edited.metadata.get("layer_composite_applied"):
                    from .layers import LayerStack
                    from .layerediting import composite_samples, _protected_pixels
                    stack, state = edited.layer_stack, edited.layer_state
                    if not isinstance(stack, LayerStack) or stack.data != layer_bytes:
                        raise ValueError("The edited layers do not match the original Photoshop layer block")
                    composed = composite_samples(edited, stack, state)
                    indices, expected = _protected_pixels(edited, composed)
                    if (len(indices) == edited.base_count and np.any(composed[..., -1] != edited.maximum)):
                        raise ValueError("The merged TIFF is missing transparency from the edited layers")
                    if any(not np.array_equal(edited.samples[..., index], expected[..., position])
                           for position, index in enumerate(indices)):
                        raise ValueError("The merged TIFF pixels do not match the edited Photoshop layers")
                    layer_bytes = stack.rewrite(state.order, state.visible)
                extra_tags.append((37724, 7, len(layer_bytes), layer_bytes, False))
            # These contain payloads rather than offsets to other IFDs.
            for code in (700, 33723, 315, 33432):
                tag = page.tags.get(code)
                if tag:
                    extra_tags.append((code, int(tag.dtype), tag.count, tag.value, False))
    else:
        dpi = original.metadata.get("dpi") or (72, 72)
        densities = [Fraction(str(value)).limit_denominator(1_000_000)
                     for value in dpi]
        resolution = tuple((value.numerator, value.denominator)
                           for value in densities)
    if resources:
        extra_tags.append((34377, 1, len(resources), resources, False))
    if edited.color_mode == "CMYK":
        extra_tags.append((332, 3, 1, 1, False))  # CMYK InkSet.
    extras = edited.metadata.get("extra_samples")
    if extras is None:
        extras = [1 if c.associated else 2 if c.kind == "Transparency" else 0 for c in edited.channels[edited.base_count:]]
    reduced = []
    if options.pyramid:
        data = edited.samples
        for _ in range(levels):
            if data.shape[:2] == (1, 1):
                break
            data = reduce_half(data)
            reduced.append(data)
    photo = {"RGB": "rgb", "CMYK": "separated", "Gray": "minisblack", "WhiteIsZero": "miniswhite"}[edited.color_mode]
    common = dict(photometric=photo, planarconfig="contig", extrasamples=extras,
                  compression="lzw", predictor=True, metadata=None)
    handle, temporary = tempfile.mkstemp(prefix=f".{destination.stem}-", suffix=".tif", dir=destination.parent)
    os.close(handle)
    temporary = Path(temporary)
    try:
        with tifffile.TiffWriter(temporary, byteorder="<", bigtiff=False) as tif:
            primary = edited.samples[..., 0] if edited.samples.shape[-1] == 1 else edited.samples
            tif.write(primary, subifds=len(reduced) or None, extratags=extra_tags,
                      iccprofile=edited.icc_profile, resolution=resolution, resolutionunit=unit,
                      software=f"TIFview {__version__}", datetime=True, **common)
            for index, data in enumerate(reduced, 1):
                reduced_resolution = tuple((v[0], v[1] * 2**index) for v in resolution)
                pixels = data[..., 0] if data.shape[-1] == 1 else data
                tif.write(pixels, subfiletype=1, resolution=reduced_resolution, resolutionunit=unit,
                          extratags=[(274, 3, 1, edited.orientation, False)], **common)
        reopened = load_image(temporary)
        if (reopened.bits, reopened.color_mode, reopened.orientation, reopened.samples.dtype) != (
                edited.bits, edited.color_mode, edited.orientation, edited.samples.dtype):
            raise ValueError("Saved bit depth, colour mode or orientation changed")
        if not np.array_equal(reopened.samples, edited.samples):
            raise ValueError("Saved channel pixels did not pass read-back verification")
        if [(c.name, c.kind, c.display, c.associated) for c in reopened.channels] != [(c.name, c.kind, c.display, c.associated) for c in edited.channels]:
            raise ValueError("Saved channel names or types did not pass read-back verification")
        if reopened.icc_profile != edited.icc_profile or reopened.photoshop_resources != resources:
            raise ValueError("Saved Photoshop resources or ICC profile changed")
        with tifffile.TiffFile(temporary) as tif:
            page = tif.pages[0]
            if (tif.is_bigtiff or tif.byteorder != "<" or int(page.compression) != 5 or
                    int(page.planarconfig) != 1 or [int(e) for e in page.extrasamples] != list(extras)):
                raise ValueError("Saved TIFF settings did not pass read-back verification")
            if page.tags.valueof(37724) != layer_bytes:
                raise ValueError("Saved Photoshop layer data changed")
            saved_resolution = (page.tags.valueof(282), page.tags.valueof(283))
            if (int(page.tags.valueof(296)) != unit or
                    tuple(Fraction(*value) for value in saved_resolution) != tuple(Fraction(*value) for value in resolution)):
                raise ValueError("Saved print resolution changed")
            if len(page.subifds or ()) != len(reduced):
                raise ValueError("Saved image pyramid is incomplete")
            for child, expected in zip(page.pages or (), reduced):
                if (child.imagelength, child.imagewidth, child.samplesperpixel) != expected.shape:
                    raise ValueError("Saved image pyramid dimensions changed")
                if not np.array_equal(child.asarray().reshape(expected.shape), expected):
                    raise ValueError("Saved image pyramid did not pass read-back verification")
        if signature is not None and file_signature(source) != signature:
            raise ValueError("The original file changed while saving. Reopen it and retry.")
        if overwrite:
            os.replace(temporary, destination)
        elif os.name == "nt":
            os.rename(temporary, destination)  # Windows refuses to overwrite a racing destination.
        else:
            os.link(temporary, destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)
