"""Raster edits in source-pixel coordinates; original samples stay immutable."""
from __future__ import annotations

from dataclasses import dataclass, replace
import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPen

from .model import ImageDocument, orient


@dataclass
class Patch:
    channel_indices: tuple[int, ...]
    bounds: tuple[int, int, int, int]
    before: np.ndarray
    after: np.ndarray
    previous_state: int
    next_state: int

    @property
    def bytes(self):
        return self.before.nbytes + self.after.nbytes


def raster_shape(tool: str, start: tuple[float, float], end: tuple[float, float],
                 image_size: tuple[int, int], width: int = 3, filled: bool = False,
                 text: str = "", font_family: str = "Segoe UI", text_size: int = 32):
    """Return a clipped 8-bit coverage mask and its source-pixel rectangle."""
    if tool not in ("Ellipse", "Box", "Line", "Text"):
        raise ValueError("Choose Ellipse, Box, Line or Text")
    if not all(math.isfinite(v) for v in (*start, *end)):
        raise ValueError("Invalid drawing coordinates")
    if not 1 <= width <= 300 or not 1 <= text_size <= 1000:
        raise ValueError("Invalid stroke width or text size")
    image_width, image_height = image_size
    font = QFont(font_family)
    font.setPixelSize(text_size)
    if tool == "Text":
        if not text.strip():
            return None
        metrics = QFontMetricsF(font)
        text_rect = metrics.boundingRect(QRectF(0, 0, image_width, 100000),
                                         Qt.TextFlag.TextDontClip, text)
        rect = QRectF(start[0], start[1], text_rect.width() + text_size, text_rect.height() + text_size)
        padding = 2
    else:
        rect = QRectF(QPointF(*start), QPointF(*end)).normalized()
        padding = width / 2 + 2
    x0 = max(0, math.floor(rect.left() - padding))
    y0 = max(0, math.floor(rect.top() - padding))
    x1 = min(image_width, math.ceil(rect.right() + padding) + 1)
    y1 = min(image_height, math.ceil(rect.bottom() + padding) + 1)
    if x1 <= x0 or y1 <= y0:
        return None
    image = QImage(x1 - x0, y1 - y0, QImage.Format.Format_ARGB32)
    if image.isNull():
        raise MemoryError("Not enough memory for this drawing")
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.translate(-x0, -y0)
        painter.setPen(QPen(QColor("white"), width, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(QColor("white") if filled else Qt.BrushStyle.NoBrush)
        if tool == "Ellipse":
            painter.drawEllipse(QRectF(QPointF(*start), QPointF(*end)).normalized())
        elif tool == "Box":
            painter.drawRect(QRectF(QPointF(*start), QPointF(*end)).normalized())
        elif tool == "Line":
            painter.drawLine(QPointF(*start), QPointF(*end))
        else:
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop |
                             Qt.TextFlag.TextDontClip, text)
    finally:
        painter.end()
    # ARGB32 is stored as native-endian 0xAARRGGBB; extracting alpha avoids
    # assumptions about RGB byte order and keeps the rasterizer display-only.
    words = np.frombuffer(image.constBits(), dtype=np.uint32).reshape(image.height(), image.bytesPerLine() // 4)
    coverage = (words[:, :image.width()] >> 24).astype(np.uint8)
    return (x0, y0, x1, y1), coverage


class EditSession:
    """Copy on first edit, bounded patch history, exact undo and redo."""
    def __init__(self, original: ImageDocument, history_limit: int = 128 * 2**20):
        self.original = self.document = original
        self._samples = None
        self.history: list[Patch] = []
        self.cursor = 0
        self.state = self.saved_state = self._sequence = 0
        self.history_limit = history_limit

    @property
    def dirty(self):
        return self.state != self.saved_state

    @property
    def can_undo(self):
        return self.cursor > 0

    @property
    def can_redo(self):
        return self.cursor < len(self.history)

    def changed_channels(self):
        if self._samples is None:
            return set()
        return {index for index in range(len(self.document.channels))
                if not np.array_equal(self._samples[..., index], self.original.samples[..., index])}

    def mark_saved(self):
        self.saved_state = self.state

    def _editable(self):
        if self._samples is None:
            self._samples = self.original.samples.copy()
            self.document = replace(self.original, samples=self._samples.view(),
                                    metadata=dict(self.original.metadata), warnings=list(self.original.warnings))
        return orient(self._samples, self.original.orientation)

    def apply(self, channel: int, bounds, coverage: np.ndarray, shade: int, invert: bool = False):
        doc = self.document
        if doc.bits not in (8, 16) or doc.color_mode == "Palette":
            raise ValueError("Pixel editing supports unsigned 8/16-bit RGB, CMYK and grayscale images")
        if not 0 <= channel < len(doc.channels) or not 0 <= shade <= doc.maximum:
            raise ValueError("Invalid channel or paint shade")
        x0, y0, x1, y1 = bounds
        if not (0 <= x0 < x1 <= doc.width and 0 <= y0 < y1 <= doc.height):
            raise ValueError("Drawing lies outside the image")
        if coverage.shape != (y1 - y0, x1 - x0) or coverage.dtype != np.uint8:
            raise ValueError("Invalid coverage mask")
        indices = (channel,)
        alpha = next((c for c in doc.channels if c.kind == "Transparency" and c.associated), None)
        if alpha and channel == alpha.index:
            indices = tuple(range(doc.base_count)) + (channel,)
        data = doc.display_samples[y0:y1, x0:x1]
        before = data[..., indices].copy()
        value = doc.maximum - shade if ((doc.color_mode == "CMYK" and channel < doc.base_count) or
                                        (doc.color_mode == "WhiteIsZero" and channel == 0)) ^ invert else shade
        # 64-bit arithmetic retains every unpainted 16-bit sample exactly.
        weight = coverage.astype(np.int64)
        new = ((data[..., channel].astype(np.int64) * (255 - weight) + value * weight + 127) // 255).astype(data.dtype)
        after = before.copy()
        if alpha and channel < doc.base_count:
            new = np.where(coverage > 0, np.minimum(new, data[..., alpha.index]), data[..., channel])
        after[..., indices.index(channel)] = new
        if alpha and channel == alpha.index:
            old_alpha = data[..., channel].astype(np.float64)
            for position, base in enumerate(range(doc.base_count)):
                straight = np.divide(data[..., base], old_alpha, out=np.zeros_like(old_alpha), where=old_alpha > 0)
                rescaled = np.rint(np.clip(straight, 0, 1) * new).astype(data.dtype)
                after[..., position] = np.where(coverage > 0, rescaled, data[..., base])
        if np.array_equal(before, after):
            return False
        required = before.nbytes + after.nbytes
        if required > self.history_limit:
            raise ValueError("Drawing exceeds the 128 MiB undo limit. Draw a smaller region.")
        self._sequence += 1
        patch = Patch(indices, bounds, before, after, self.state, self._sequence)
        del self.history[self.cursor:]
        self.history.append(patch)
        self.cursor += 1
        while sum(p.bytes for p in self.history) > self.history_limit and len(self.history) > 1:
            self.history.pop(0)
            self.cursor -= 1
        self._write(patch, after)
        self.state = patch.next_state
        return True

    def _write(self, patch: Patch, values):
        x0, y0, x1, y1 = patch.bounds
        data = self._editable()[y0:y1, x0:x1]
        for position, channel in enumerate(patch.channel_indices):
            data[..., channel] = values[..., position]

    def undo(self):
        if self.can_undo:
            self.cursor -= 1
            patch = self.history[self.cursor]
            self._write(patch, patch.before)
            self.state = patch.previous_state

    def redo(self):
        if self.can_redo:
            patch = self.history[self.cursor]
            self._write(patch, patch.after)
            self.cursor += 1
            self.state = patch.next_state
