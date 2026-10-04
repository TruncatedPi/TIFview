"""Bounded display conversions. Source arrays are never modified."""
from __future__ import annotations

from collections.abc import Callable
import numpy as np
from PIL import Image, ImageCms

from .model import Channel, ImageDocument


# Round native 16-bit samples to the same full-range display as the float path.
# Dividing by 257 (65535 / 255) avoids truncation and needs only a 64 KiB table.
_UINT16_GRAY = ((np.arange(65536, dtype=np.uint32) + 128) // 257).astype(np.uint8)
_BLOCK_PIXELS = 65536


class RenderCancelled(Exception):
    """A newer view request superseded this preview."""


def grayscale(doc: ImageDocument, selected: int, invert: bool = False) -> np.ndarray:
    """Return an independent 8-bit grayscale plane without float RGB expansion."""
    plane = doc.display_samples[..., selected]
    if doc.bits == 16:
        gray = _UINT16_GRAY[plane]
    else:
        gray = np.array(plane, dtype=np.uint8, order="C", copy=True)
        if doc.bits == 1:
            gray *= 255
    inverse = (doc.color_mode == "CMYK" and selected < doc.base_count) or (
        doc.color_mode == "WhiteIsZero" and selected == 0)
    if inverse ^ invert:
        np.subtract(255, gray, out=gray)
    return np.ascontiguousarray(gray)


def mask_coverage(doc: ImageDocument, channel: Channel, invert: bool = False,
                  data: np.ndarray | None = None) -> np.ndarray:
    data = doc.display_samples if data is None else data
    values = data[..., channel.index].astype(np.float32)
    values /= doc.maximum
    if channel.kind == "Spot" or (channel.display and channel.display.mode == 0):
        np.subtract(1, values, out=values)  # Photoshop: black = ink / masked area.
    if invert:
        np.subtract(1, values, out=values)
    return values


def _checker(height: int, width: int, y_offset: int = 0) -> np.ndarray:
    y, x = np.ogrid[y_offset:y_offset + height, :width]
    v = np.where((x // 16 + y // 16) % 2, np.float32(.78), np.float32(.93))
    return np.repeat(v[..., None], 3, axis=2)


def _render_block(doc, data, selected, colored, visible, overlays, opacity, invert, colors, y_offset):
    height, width = data.shape[:2]
    if selected is not None:
        channel = doc.channels[selected]
        coverage = mask_coverage(doc, channel, invert, data)[..., None] * opacity
        color = np.array(colors.get(selected, channel.color), np.float32) / 255
        rgb = _checker(height, width, y_offset)
        rgb *= 1 - coverage
        rgb += color * coverage
    else:
        base = data[..., :doc.base_count].astype(np.float32)
        base /= doc.maximum
        alpha_channels = [c for c in doc.channels if c.kind == "Transparency"]
        if alpha_channels and alpha_channels[0].associated and doc.color_mode != "Palette":
            stored_alpha = data[..., alpha_channels[0].index].astype(np.float32)[..., None]
            stored_alpha /= doc.maximum
            np.divide(base, stored_alpha, out=base, where=stored_alpha > 0)
            np.copyto(base, 0, where=stored_alpha == 0)
            np.clip(base, 0, 1, out=base)
        for index in range(doc.base_count):
            if index not in visible:
                base[..., index] = 0
        if doc.icc_transform is not None:
            if doc.color_mode == "WhiteIsZero":
                np.subtract(1, base, out=base)
            pixels = np.rint(base * 255).astype(np.uint8)
            if doc.base_count == 1:
                pixels = pixels[..., 0]
            input_mode = doc.color_mode if doc.color_mode in ("RGB", "CMYK") else "L"
            image = Image.frombytes(input_mode, (width, height), pixels.tobytes())
            rgb = np.asarray(ImageCms.applyTransform(image, doc.icc_transform), dtype=np.float32)
            rgb /= 255
        elif doc.color_mode == "RGB":
            rgb = base
        elif doc.color_mode == "CMYK":
            rgb = 1 - base[..., :3]
            rgb *= 1 - base[..., 3:4]
        elif doc.color_mode == "Palette":
            rgb = np.moveaxis(doc.colormap[:, data[..., 0]], 0, -1).astype(np.float32)
            rgb /= 65535
            if 0 not in visible:
                rgb.fill(0)
        else:
            if doc.color_mode == "WhiteIsZero":
                np.subtract(1, base, out=base)
            rgb = np.repeat(base, 3, axis=2)
        transparency = [c for c in alpha_channels if c.index in visible]
        if transparency:
            stored = data[..., transparency[0].index]
            # Entirely opaque bands need no checkerboard or alpha arithmetic.
            if not np.all(stored == doc.maximum):
                alpha = stored.astype(np.float32)[..., None]
                alpha /= doc.maximum
                rgb *= alpha
                checker = _checker(height, width, y_offset)
                checker *= 1 - alpha
                rgb += checker
        if overlays:
            for channel in doc.channels[doc.base_count:]:
                if channel.index not in visible or channel.kind == "Transparency":
                    continue
                coverage = mask_coverage(doc, channel, data=data)[..., None] * opacity
                color = np.array(colors.get(channel.index, channel.color), np.float32) / 255
                rgb *= 1 - coverage
                rgb += color * coverage
    np.clip(rgb, 0, 1, out=rgb)
    rgb *= 255
    np.rint(rgb, out=rgb)
    return np.ascontiguousarray(rgb.astype(np.uint8))


def render(doc: ImageDocument, selected: int | None = None, colored: bool = False,
           visible: set[int] | None = None, overlays: bool = False,
           opacity: float = .65, invert: bool = False,
           colors: dict[int, tuple[int, int, int]] | None = None,
           cancelled: Callable[[], bool] | None = None) -> np.ndarray:
    colors = colors or {}
    visible = set(range(len(doc.channels))) if visible is None else visible
    if selected is not None and not colored:
        if cancelled and cancelled():
            raise RenderCancelled()
        return np.repeat(grayscale(doc, selected, invert)[..., None], 3, axis=2)
    data = doc.display_samples
    output = np.empty((doc.height, doc.width, 3), dtype=np.uint8)
    rows = max(1, _BLOCK_PIXELS // doc.width)
    for y in range(0, doc.height, rows):
        if cancelled and cancelled():
            raise RenderCancelled()
        output[y:y + rows] = _render_block(doc, data[y:y + rows], selected, colored,
                                         visible, overlays, opacity, invert, colors, y)
    if cancelled and cancelled():
        raise RenderCancelled()
    return output
