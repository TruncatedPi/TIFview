"""Render drawn shapes and text into a native Photoshop raster layer."""
from __future__ import annotations

import io
import math
from dataclasses import replace

import numpy as np
from PIL import Image, ImageCms
from PySide6.QtGui import QColor

from . import layerediting, spots
from .editing import raster_shape
from .layers import LayerStack
from .reader import _check_image_limits
from .svglayer import append_layer, base_stack, raster_block, small_parent


def draw_layer_reason(doc) -> str | None:
    """Check whether the document supports adding a drawn Photoshop layer."""
    if doc.bits not in (8, 16) or doc.color_mode not in ("RGB", "CMYK", "Gray", "WhiteIsZero"):
        return "Adding drawing layers supports unsigned 8/16-bit RGB, CMYK and grayscale images"
    if doc.orientation != 1:
        return "Adding drawing layers currently requires TIFF orientation 1"
    if doc.metadata.get("canvas_layer_reason"):
        return doc.metadata["canvas_layer_reason"] + ". Save and reopen a merged copy before adding a new layer."
    return None


def convert_color(doc, color) -> np.ndarray:
    """Convert user or UI color into native document process samples."""
    if isinstance(color, str):
        qcolor = QColor(color)
        if not qcolor.isValid():
            raise ValueError(f"Invalid colour string: {color!r}")
        rgb_tuple = (qcolor.red(), qcolor.green(), qcolor.blue())
    elif isinstance(color, QColor):
        if not color.isValid():
            raise ValueError("Invalid QColor")
        rgb_tuple = (color.red(), color.green(), color.blue())
    elif isinstance(color, (int, float, np.integer)):
        val = int(round(color))
        rgb_tuple = (val, val, val)
    elif isinstance(color, (tuple, list, np.ndarray)):
        if len(color) == 3:
            rgb_tuple = tuple(int(round(v)) for v in color)
        elif len(color) == 1:
            val = int(round(color[0]))
            rgb_tuple = (val, val, val)
        elif len(color) == doc.base_count and not doc.icc_profile:
            values = np.array(color, dtype=np.float64)
            if doc.bits == 16 and np.max(values) <= 255:
                values = values * 257
            if doc.color_mode == "WhiteIsZero":
                values = doc.maximum - values
            return np.rint(np.clip(values, 0, doc.maximum)).astype(doc.samples.dtype)
        else:
            raise ValueError(f"Color tuple length {len(color)} does not match mode {doc.color_mode}")
    else:
        raise TypeError(f"Unsupported color type: {type(color)}")

    if any(v < 0 or v > 255 for v in rgb_tuple):
        raise ValueError("Drawing RGB colour values must be in 0..255")

    rgb = Image.new("RGB", (1, 1), rgb_tuple[:3])
    mode = "L" if doc.base_count == 1 else doc.color_mode
    if doc.icc_profile:
        try:
            transform = ImageCms.buildTransformFromOpenProfiles(
                ImageCms.createProfile("sRGB"),
                ImageCms.ImageCmsProfile(io.BytesIO(doc.icc_profile)),
                "RGB", mode
            )
            converted = ImageCms.applyTransform(rgb, transform)
        except Exception as exc:
            raise ValueError("Cannot convert drawing colour into the image's ICC profile") from exc
    else:
        converted = rgb.convert(mode)

    pixel = np.asarray(converted, dtype=doc.samples.dtype).reshape(-1)
    if doc.bits == 16:
        pixel = pixel * 257
    if doc.color_mode == "WhiteIsZero":
        pixel = doc.maximum - pixel
    return np.clip(pixel, 0, doc.maximum).astype(doc.samples.dtype)


def draw_pixels(doc, tool: str, start: tuple[float, float], end: tuple[float, float],
                color, width: int = 3, filled: bool = False, text: str = "",
                font_family: str = "Segoe UI", text_size: int = 32):
    """Rasterize drawing shape or text and return native process samples, alpha, and bounds."""
    mask = raster_shape(tool, start, end, (doc.width, doc.height), width, filled,
                        text, font_family, text_size)
    if mask is None:
        raise ValueError("The drawn shape has no visible pixels to add")
    (rx0, ry0, rx1, ry1), coverage = mask
    ys = np.flatnonzero(np.any(coverage > 0, axis=1))
    xs = np.flatnonzero(np.any(coverage > 0, axis=0))
    if not len(xs) or not len(ys):
        raise ValueError("The drawn shape has no visible pixels to add")

    cy0, cy1 = int(ys[0]), int(ys[-1]) + 1
    cx0, cx1 = int(xs[0]), int(xs[-1]) + 1
    x0, y0 = rx0 + cx0, ry0 + cy0
    x1, y1 = rx0 + cx1, ry0 + cy1
    coverage = coverage[cy0:cy1, cx0:cx1]

    h, w = y1 - y0, x1 - x0
    _check_image_limits(w * h, w * h * (doc.base_count + 1) * doc.samples.dtype.itemsize)

    pixel = convert_color(doc, color)
    samples = np.empty((h, w, doc.base_count), dtype=doc.samples.dtype)
    samples[...] = pixel

    if doc.bits == 16:
        alpha = (coverage.astype(np.uint16) * 257)
    else:
        alpha = coverage.copy()

    return samples, alpha, (y0, x0, y1, x1)


def add_drawn_layer(original, doc, tool: str, start: tuple[float, float], end: tuple[float, float],
                    color=(0, 0, 0), width: int = 3, filled: bool = False, text: str = "",
                    font_family: str = "Segoe UI", text_size: int = 32, name: str | None = None):
    """Render a drawn shape/text into a new native Photoshop layer, recomposing the image."""
    spots._validate(doc)
    reason = draw_layer_reason(doc)
    if reason:
        raise ValueError(reason)

    if name is None:
        layer_name = f"{tool} 1"
    else:
        layer_name = name.strip()
    if not layer_name or len(layer_name) > 255 or "\0" in layer_name:
        raise ValueError("Use a layer name with 1–255 characters and no null characters")

    stack = doc.layer_stack
    if stack is None:
        stack = LayerStack.from_document(original) if original.metadata.get("has_photoshop_layers") else base_stack(doc)

    if original.metadata.get("has_photoshop_layers") or doc.generated_layer_data is not None:
        layerediting.require_unchanged_merged_pixels(original, doc, stack)

    state = doc.layer_state or layerediting.default_state(stack)
    baseline = layerediting.composite_samples(doc, stack, state)
    indices, expected = layerediting._protected_pixels(doc, baseline)
    if any(not np.array_equal(doc.samples[..., index], expected[..., position]) for position, index in enumerate(indices)):
        raise ValueError("The existing layers do not reproduce the image pixels. Save/reopen a merged copy before adding a layer.")

    samples, alpha, bounds = draw_pixels(doc, tool, start, end, color, width, filled, text, font_family, text_size)
    single = LayerStack(raster_block(doc, samples, alpha, bounds, layer_name), small_parent(doc))
    data = append_layer(stack, single)
    added_index = len(stack.layers)
    new_stack = LayerStack(data, small_parent(doc))
    shown = state.visible | {added_index}
    new_state = layerediting.LayerState((added_index, *state.order), frozenset(shown))
    result, added_alpha = layerediting.recompose_document(doc, new_stack, new_state)
    metadata = dict(result.metadata, has_photoshop_layers=True, byte_order="IBM PC (little endian)")
    return replace(result, metadata=metadata, generated_layer_data=data), added_alpha
