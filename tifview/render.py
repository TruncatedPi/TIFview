"""Display-only conversions. Source arrays are never modified."""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageCms

from .model import Channel, ImageDocument


def mask_coverage(doc: ImageDocument, channel: Channel, invert: bool = False) -> np.ndarray:
    values = doc.display_samples[..., channel.index].astype(np.float32) / doc.maximum
    if channel.kind == "Spot" or (channel.display and channel.display.mode == 0):
        values = 1 - values  # Photoshop: black = ink / masked area.
    elif channel.kind == "Process" and doc.color_mode == "CMYK":
        pass  # TIFF CMYK: high sample = high ink.
    # Unknown extras and regular alpha default to high sample = mask coverage.
    return 1 - values if invert else values


def _checker(height: int, width: int) -> np.ndarray:
    y, x = np.ogrid[:height, :width]
    v = np.where((x // 16 + y // 16) % 2, .78, .93).astype(np.float32)
    return np.repeat(v[..., None], 3, axis=2)


def render(doc: ImageDocument, selected: int | None = None, colored: bool = False,
           visible: set[int] | None = None, overlays: bool = False,
           opacity: float = .65, invert: bool = False,
           colors: dict[int, tuple[int, int, int]] | None = None) -> np.ndarray:
    colors = colors or {}
    visible = set(range(len(doc.channels))) if visible is None else visible
    data = doc.display_samples
    if selected is not None:
        channel = doc.channels[selected]
        if not colored:
            plane = data[..., selected].astype(np.float32) / doc.maximum
            # Match conventional process separation display: high ink = dark.
            if (doc.color_mode == "CMYK" and selected < doc.base_count) or (
                    doc.color_mode == "WhiteIsZero" and selected == 0):
                plane = 1 - plane
            if invert:
                plane = 1 - plane
            rgb = np.repeat(plane[..., None], 3, axis=2)
        else:
            coverage = mask_coverage(doc, channel, invert)[..., None] * opacity
            color = np.array(colors.get(selected, channel.color), np.float32) / 255
            rgb = _checker(doc.height, doc.width) * (1 - coverage) + color * coverage
    else:
        base = data[..., :doc.base_count].astype(np.float32) / doc.maximum
        alpha_channels = [c for c in doc.channels if c.kind == "Transparency"]
        if alpha_channels and alpha_channels[0].associated and doc.color_mode != "Palette":
            stored_alpha = data[..., alpha_channels[0].index].astype(np.float32)[..., None] / doc.maximum
            # Unassociate native samples before color conversion, also when the
            # transparency checkbox is off. CMYK samples are ink quantities.
            base = np.divide(base, stored_alpha, out=np.zeros_like(base), where=stored_alpha > 0)
            base = np.clip(base, 0, 1)
        for index in range(doc.base_count):
            if index not in visible:
                base[..., index] = 0
        if doc.icc_transform is not None:
            input_values = 1 - base if doc.color_mode == "WhiteIsZero" else base
            pixels = np.rint(np.clip(input_values, 0, 1) * 255).astype(np.uint8)
            if doc.base_count == 1:
                pixels = pixels[..., 0]
            input_mode = doc.color_mode if doc.color_mode in ("RGB", "CMYK") else "L"
            image = Image.frombytes(input_mode, (doc.width, doc.height), pixels.tobytes())
            rgb = np.asarray(ImageCms.applyTransform(image, doc.icc_transform),
                             dtype=np.float32) / 255
        elif doc.color_mode == "RGB":
            rgb = base
        elif doc.color_mode == "CMYK":
            rgb = (1 - base[..., :3]) * (1 - base[..., 3:4])
        elif doc.color_mode == "Palette":
            rgb = np.moveaxis(doc.colormap[:, data[..., 0]], 0, -1).astype(np.float32) / 65535
            if 0 not in visible:
                rgb = np.zeros_like(rgb)
        else:
            if doc.color_mode == "WhiteIsZero":
                base = 1 - base
            rgb = np.repeat(base, 3, axis=2)
        transparency = [c for c in alpha_channels if c.index in visible]
        if transparency:
            channel = transparency[0]
            alpha = data[..., channel.index].astype(np.float32)[..., None] / doc.maximum
            rgb = np.clip(rgb, 0, 1) * alpha + _checker(doc.height, doc.width) * (1 - alpha)
        if overlays:
            for channel in doc.channels[doc.base_count:]:
                if channel.index not in visible or channel.kind == "Transparency":
                    continue
                coverage = mask_coverage(doc, channel)[..., None] * opacity
                color = np.array(colors.get(channel.index, channel.color), np.float32) / 255
                rgb = rgb * (1 - coverage) + color * coverage
    return np.ascontiguousarray(np.rint(np.clip(rgb, 0, 1) * 255).astype(np.uint8))
