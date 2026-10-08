"""Lossless canvas padding in displayed coordinates, with separate TIFF copies."""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
import struct

import numpy as np

from .model import Channel, orient
from . import spots, layerediting
from .photoshop import read_resources, rewrite_channel_resources
from .reader import _check_image_limits


@dataclass(frozen=True)
class CanvasGeometry:
    source_size: tuple[int, int]
    size: tuple[int, int]
    offset: tuple[int, int]
    generated_transparency: bool = False
    layers_preserved: bool = False
    origin_offset: tuple[int, int] = (0, 0)

    def validate(self, original, edited):
        values = (*self.source_size, *self.size, *self.offset, *self.origin_offset)
        if (any(type(v) is not int for v in values) or
                self.source_size != (original.width, original.height) or
                self.size != (edited.width, edited.height) or min(self.offset) < 0 or
                any(o + old > new for old, new, o in zip(self.source_size, self.size, self.offset))):
            raise ValueError("Invalid canvas expansion geometry")


def pad_samples(doc, width, height, left, top, channels, transparent):
    if any(type(v) is not int for v in (width, height, left, top)):
        raise ValueError("Canvas dimensions and offsets must be whole pixels")
    if left < 0 or top < 0 or width < doc.width + left or height < doc.height + top:
        raise ValueError("Canvas expansion cannot crop the existing image")
    if max(width, height) > 300000:
        raise ValueError("Canvas dimensions exceed the current limit")
    _check_image_limits(width * height, width * height * len(channels) * doc.samples.dtype.itemsize)
    samples = np.empty((height, width, len(channels)), dtype=doc.samples.dtype)
    alpha = next((c for c in channels if c.kind == "Transparency"), None)
    for c in channels:
        if c.index < doc.base_count:
            white = 0 if doc.color_mode in ("CMYK", "WhiteIsZero") else doc.maximum
            value = 0 if transparent and alpha and alpha.associated else white
        elif c.kind == "Transparency":
            value = 0 if transparent else doc.maximum
        elif c.kind == "Spot":
            value = doc.maximum  # No ink in the added area.
        else:
            value = 0  # No saved selection in the added area.
        samples[..., c.index] = value
    region = samples[top:top + doc.height, left:left + doc.width]
    region[..., :len(doc.channels)] = doc.display_samples
    if len(channels) > len(doc.channels):
        region[..., -1] = doc.maximum  # Previously opaque image, appended alpha.
    # Keep the original TIFF orientation. Six and eight are mutual inverses;
    # all other orientation transforms are self-inverse.
    inverse = {6: 8, 8: 6}.get(doc.orientation, doc.orientation)
    return np.ascontiguousarray(orient(samples, inverse))


def layer_bytes(original, geometry, data):
    """Move understood raster rectangles only; compressed pixels stay exact."""
    from .layers import LayerStack, _Cursor, _tags
    if original.orientation != 1:
        raise ValueError("Preserving layers during canvas expansion requires TIFF orientation 1")
    stack = LayerStack(data, original)
    left, top = geometry.offset
    for layer in stack.layers:
        if layer.kind != "Raster" or layer.issues:
            raise ValueError(f"Canvas expansion cannot retain {layer.name}: {layer.preview_note or layer.kind}")
        if (left or top) and layer.mask is not None:
            raise ValueError("Moving masked layers to a larger canvas is not supported yet")
    if not left and not top:
        return data
    changed = bytearray(data)
    for layer in stack.layers:
        y0, x0, y1, x1 = layer.bounds
        struct.pack_into(stack.byteorder + "4i", changed, layer._record_start,
                         y0 + top, x0 + left, y1 + top, x1 + left)
        # A raster layer's transform reference point is an absolute x/y pair.
        extra_start = layer._record_start + 34 + 6 * len(layer.channel_ids)
        cursor = _Cursor(memoryview(data)[extra_start:layer._record_end], stack.byteorder, extra_start)
        cursor.take(cursor.unpack("I")[0])
        cursor.take(cursor.unpack("I")[0])
        length = cursor.unpack("B")[0]
        cursor.take(length)
        cursor.take((-(length + 1)) % 4)
        for tag in _tags(cursor, 2):
            if tag.key == b"fxrp":
                if tag.payload_end - tag.payload_start != 16:
                    raise ValueError("Unsupported raster transform reference point")
                x, y = struct.unpack_from(stack.byteorder + "2d", data, tag.payload_start)
                if not math.isfinite(x) or not math.isfinite(y):
                    raise ValueError("Invalid raster transform reference point")
                struct.pack_into(stack.byteorder + "2d", changed, tag.payload_start, x + left, y + top)
    return bytes(changed)


def expand_document(original, doc, width, height, left=0, top=0, transparent=True, origin_left=None, origin_top=None):
    spots._validate(doc)
    if type(transparent) is not bool:
        raise ValueError("Choose a transparent or white canvas background")
    if (width, height, left, top) == (doc.width, doc.height, 0, 0):
        return doc
    if any(v is not None and (type(v) is not int or v < 0) for v in (origin_left, origin_top)):
        raise ValueError("Canvas origin offsets must be nonnegative whole pixels")
    channels = list(doc.channels)
    metadata = dict(doc.metadata)
    resources = doc.photoshop_resources
    added = transparent and not any(c.kind == "Transparency" for c in channels)
    if added:
        channels.append(Channel(len(channels), "Transparency", "Transparency",
                                "TIFF ExtraSamples=2 generated by canvas expansion", (130, 130, 130)))
        extras = spots._extras(doc)
        resources = rewrite_channel_resources(resources, doc.channels, channels,
                                              [*range(len(doc.channels)), None], doc.base_count, extras)
        metadata["extra_samples"] = [*extras, 2]
        metadata["channel_source_indices"] = [*spots._source_indices(doc), None]
        metadata["canvas_generated_transparency"] = True
        metadata["photoshop_resource_ids"] = read_resources(resources).resource_ids if resources else []
    generated = (any(c.kind == "Transparency" for c in channels)
                 and not any(c.kind == "Transparency" for c in original.channels))
    if generated:
        metadata["canvas_generated_transparency"] = True
    previous = doc.canvas
    geometry = CanvasGeometry(previous.source_size if previous else (doc.width, doc.height),
                              (width, height),
                              ((previous.offset[0] if previous else 0) + left,
                               (previous.offset[1] if previous else 0) + top),
                              generated,
                              origin_offset=((previous.origin_offset[0] if previous else 0) + (left if origin_left is None else origin_left),
                                             (previous.origin_offset[1] if previous else 0) + (top if origin_top is None else origin_top)))
    samples = pad_samples(doc, width, height, left, top, channels, transparent)
    result = replace(doc, samples=samples, channels=channels, metadata=metadata,
                     photoshop_resources=resources, canvas=geometry,
                     layer_stack=None, layer_state=None,
                     layer_merged_samples=None, layer_merged_transparency=None)
    metadata.pop("layer_composite_applied", None)
    metadata.pop("canvas_layer_reason", None)
    if original.metadata.get("has_photoshop_layers"):
        try:
            from .layers import LayerStack
            if not transparent:
                raise ValueError("White canvas padding would require a new Photoshop background layer")
            if previous and not previous.layers_preserved:
                raise ValueError(doc.metadata.get("canvas_layer_reason", "The earlier canvas change requires a merged copy"))
            source_stack = original.layer_stack or LayerStack.from_document(original)
            state = doc.layer_state or layerediting.default_state(source_stack)
            data = layer_bytes(original, geometry, source_stack.data)
            layerediting.require_unchanged_merged_pixels(original, doc, source_stack)
            # LayerStack uses this array only for shape/dtype, not source values.
            # A tiny broadcast view avoids holding full-image history snapshots.
            parent = replace(result, samples=np.broadcast_to(np.zeros((1, 1, len(channels)), dtype=samples.dtype), samples.shape))
            stack = LayerStack(data, parent)
            composed = stack.composite_samples(state.order, state.visible)
            indices, values = layerediting._protected_pixels(result, composed)
            if any(not np.array_equal(samples[..., index], values[..., position])
                   for position, index in enumerate(indices)):
                raise ValueError("The expanded native composite does not match the original raster layers")
            geometry = replace(geometry, layers_preserved=True)
            metadata["layer_composite_applied"] = True
            result = replace(result, canvas=geometry, layer_stack=stack, layer_state=state,
                             layer_merged_samples=samples,
                             layer_merged_transparency=next((c.index for c in channels if c.kind == "Transparency"), None))
        except ValueError as exc:
            metadata["canvas_layer_reason"] = str(exc)
    return result
