"""Render SVG into a native Photoshop raster layer, preserving source records."""
from dataclasses import replace
import io
import struct

import numpy as np
from PIL import Image, ImageCms
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter

from . import layerediting, spots
from .layers import LayerStack
from .svg import SvgItem, canvas_fit
from .reader import _check_image_limits


def raster_block(doc, samples, alpha, bounds, name):
    """Serialize only new, understood pixels; never parse/reserialize old tags."""
    from psdtags import (PsdChannel, PsdChannelId, PsdCompressionType, PsdFormat, PsdKey,
                         PsdLayer, PsdLayerFlag, PsdLayers, PsdRectangle, PsdUnknown,
                         PsdUserMask, TiffImageSourceData)
    if not name.strip() or len(name) > 255 or "\0" in name:
        raise ValueError("Use a layer name with 1–255 characters and no null characters")
    stored = doc.maximum - samples if doc.color_mode in ("CMYK", "WhiteIsZero") else samples
    channels = [PsdChannel(PsdChannelId(i), data=stored[..., i].copy()) for i in range(doc.base_count)]
    channels.append(PsdChannel(PsdChannelId.TRANSPARENCY_MASK, data=alpha.copy()))
    unicode_name = name.encode("utf-16-le")
    info = [PsdUnknown(PsdKey.UNICODE_LAYER_NAME, PsdFormat.LE32BIT,
                      struct.pack("<I", len(unicode_name) // 2) + unicode_name)]
    legacy_name = name.encode("macroman", errors="replace")[:255].decode("macroman")
    layer = PsdLayer(legacy_name, channels, PsdRectangle(*bounds), flags=PsdLayerFlag.PHOTOSHOP5, info=info)
    source = TiffImageSourceData(PsdFormat.LE32BIT,
                                PsdLayers(PsdKey.LAYER if doc.bits == 8 else PsdKey.LAYER_16, [layer]), PsdUserMask())
    compression = PsdCompressionType.RLE if samples.shape[1] * samples.dtype.itemsize <= 50000 else PsdCompressionType.ZIP
    return source.tobytes(compression=compression)


def append_layer(stack, single):
    """Append a top layer without touching original records or channel chunks."""
    if stack.byteorder != "<" or single.byteorder != "<" or len(single.layers) != 1:
        raise ValueError("Adding layers requires IBM-PC Photoshop layer data")
    count = len(stack.layers) + 1
    if count > 4096:
        raise ValueError("The Photoshop layer count exceeds the current limit")
    tag = stack._layer_tag
    layer = single.layers[0]
    payload = (struct.pack("<h", -count if stack._signed_count < 0 else count)
               + stack.data[tag.payload_start + 2:stack._records_end]
               + single.data[layer._record_start:layer._record_end]
               + stack.data[stack._records_end:stack._pixels_end]
               + b"".join(single.data[start:end] for start, end in layer._channel_spans))
    payload += b"\0" * (len(payload) % 2)
    header = stack.data[tag.start:tag.payload_start - 4] + struct.pack("<I", len(payload))
    return stack.data[:tag.start] + header + payload + b"\0" * (-len(payload) % 4) + stack.data[tag.end:]


def small_parent(doc):
    # Parsing needs dimensions/type, not a second reference to a huge image.
    return replace(doc, samples=np.broadcast_to(np.zeros((1, 1, len(doc.channels)), doc.samples.dtype), doc.samples.shape),
                   layer_stack=None, layer_merged_samples=None, generated_layer_data=None)


def svg_pixels(doc, artwork, placement):
    if canvas_fit(doc, artwork, placement) != (doc.width, doc.height, 0, 0):
        raise ValueError("The SVG extends outside the canvas. Use Canvas size or Expand canvas to SVG before adding the layer.")
    _check_image_limits(doc.width * doc.height, doc.width * doc.height * (doc.base_count + 1) * doc.samples.dtype.itemsize)
    image = QImage(doc.width, doc.height, QImage.Format.Format_RGBA8888)
    if image.isNull():
        raise ValueError("Could not allocate the SVG layer image")
    image.fill(Qt.GlobalColor.transparent)
    item = SvgItem(artwork)
    item.place(placement, doc.metadata["dpi"])
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setTransform(item.transform())
        item.paint(painter, None)
    finally:
        painter.end()
    rgba = np.frombuffer(image.constBits(), np.uint8).reshape(doc.height, image.bytesPerLine() // 4, 4)[:, :doc.width]
    ys = np.flatnonzero(np.any(rgba[..., 3], axis=1))
    xs = np.flatnonzero(np.any(rgba[..., 3], axis=0))
    if not len(xs) or not len(ys):
        raise ValueError("The SVG has no visible pixels to add")
    x0, x1, y0, y1 = int(xs[0]), int(xs[-1]) + 1, int(ys[0]), int(ys[-1]) + 1
    rgba = rgba[y0:y1, x0:x1].copy()
    rgb = Image.fromarray(rgba[..., :3])
    mode = "L" if doc.base_count == 1 else doc.color_mode
    if doc.icc_profile:
        try:
            transform = ImageCms.buildTransformFromOpenProfiles(ImageCms.createProfile("sRGB"),
                        ImageCms.ImageCmsProfile(io.BytesIO(doc.icc_profile)), "RGB", mode)
            converted = ImageCms.applyTransform(rgb, transform)
        except Exception as exc:
            raise ValueError("Cannot convert SVG colours into the image's ICC profile") from exc
    else:
        converted = rgb.convert(mode)
    samples = np.asarray(converted, dtype=doc.samples.dtype)
    if doc.base_count == 1:
        samples = samples[..., None]
    if doc.bits == 16:
        samples = samples * 257
    if doc.color_mode == "WhiteIsZero":
        samples = doc.maximum - samples
    alpha = rgba[..., 3].astype(doc.samples.dtype)
    if doc.bits == 16:
        alpha *= 257
    return samples, alpha, (y0, x0, y1, x1)


def base_stack(doc):
    """Create a base layer only when the source has no Photoshop layer stack."""
    samples = doc.samples[..., :doc.base_count].copy()
    transparency = next((c for c in doc.channels if c.kind == "Transparency"), None)
    alpha = (doc.samples[..., transparency.index].copy() if transparency else
             np.full(doc.samples.shape[:2], doc.maximum, doc.samples.dtype))
    if transparency and transparency.associated:
        values = np.divide(samples.astype(np.float64) * doc.maximum, alpha[..., None],
                           out=np.zeros(samples.shape, np.float64), where=alpha[..., None] > 0)
        samples = np.rint(np.clip(values, 0, doc.maximum)).astype(doc.samples.dtype)
    data = raster_block(doc, samples, alpha, (0, 0, doc.height, doc.width), "Original image")
    return LayerStack(data, small_parent(doc))


def svg_layer_reason(doc, artwork, placement):
    """Cheap preflight shared by the controls and the pixel-layer worker."""
    if doc.bits not in (8, 16) or doc.color_mode not in ("RGB", "CMYK", "Gray", "WhiteIsZero"):
        return "Adding SVG layers supports unsigned 8/16-bit RGB, CMYK and grayscale images"
    if doc.orientation != 1:
        return "Adding SVG layers currently requires TIFF orientation 1"
    if not doc.metadata.get("dpi"):
        return "Adding an SVG layer needs calibrated print resolution"
    if doc.metadata.get("canvas_layer_reason"):
        return doc.metadata["canvas_layer_reason"] + ". Save and reopen a merged copy before adding a new layer."
    if canvas_fit(doc, artwork, placement) != (doc.width, doc.height, 0, 0):
        return "The SVG extends outside the canvas. Use Canvas size or Expand canvas to SVG before adding the layer."
    return None


def add_svg_layer(original, doc, artwork, placement, name):
    spots._validate(doc)
    reason = svg_layer_reason(doc, artwork, placement)
    if reason:
        raise ValueError(reason)
    stack = doc.layer_stack
    if stack is None:
        stack = LayerStack.from_document(original) if original.metadata.get("has_photoshop_layers") else base_stack(doc)
    if original.metadata.get("has_photoshop_layers") or doc.generated_layer_data is not None:
        layerediting.require_unchanged_merged_pixels(original, doc, stack)
    state = doc.layer_state or layerediting.default_state(stack)
    baseline = layerediting.composite_samples(doc, stack, state)
    indices, expected = layerediting._protected_pixels(doc, baseline)
    if any(not np.array_equal(doc.samples[..., index], expected[..., position]) for position, index in enumerate(indices)):
        raise ValueError("The existing layers do not reproduce the image pixels. Save/reopen a merged copy before adding SVG artwork.")
    samples, alpha, bounds = svg_pixels(doc, artwork, placement)
    single = LayerStack(raster_block(doc, samples, alpha, bounds, name), small_parent(doc))
    data = append_layer(stack, single)
    added_index = len(stack.layers)
    new_stack = LayerStack(data, small_parent(doc))
    shown = state.visible | ({added_index} if placement.visible else set())
    new_state = layerediting.LayerState((added_index, *state.order), frozenset(shown))
    result, added_alpha = layerediting.recompose_document(doc, new_stack, new_state)
    metadata = dict(result.metadata, has_photoshop_layers=True, byte_order="IBM PC (little endian)")
    return replace(result, metadata=metadata, generated_layer_data=data), added_alpha


def validated_layer_data(original, edited):
    """Verify generated bytes and all pre-existing opaque records independently."""
    stack = edited.layer_stack
    if stack is None or stack.data != edited.generated_layer_data:
        raise ValueError("Generated layer data does not match the edited Photoshop stack")
    if original.metadata.get("has_photoshop_layers"):
        source = LayerStack.from_document(original)
        if edited.canvas is not None:
            from .canvas import layer_bytes
            source = LayerStack(layer_bytes(original, edited.canvas, source.data), small_parent(edited))
        if len(stack.layers) < len(source.layers):
            raise ValueError("An original Photoshop layer is missing")
        for before, after in zip(source.layers, stack.layers):
            if source.data[before._record_start:before._record_end] != stack.data[after._record_start:after._record_end]:
                raise ValueError("An original Photoshop layer record was modified")
            if tuple(source.data[a:b] for a, b in before._channel_spans) != tuple(stack.data[a:b] for a, b in after._channel_spans):
                raise ValueError("Original Photoshop layer pixels were modified")
        old_tags = [(t.key, source.data[t.start:t.end]) for t in source._top_tags if t is not source._layer_tag]
        new_tags = [(t.key, stack.data[t.start:t.end]) for t in stack._top_tags if t is not stack._layer_tag]
        if old_tags != new_tags:
            raise ValueError("Original Photoshop document layer settings were modified")
    return stack.data
