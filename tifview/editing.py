"""Raster edits in source-pixel coordinates; original samples stay immutable."""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
import math
import zlib

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPen

from .model import ImageDocument, orient
from . import spots, layerediting


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


@dataclass
class Layout:
    channels: tuple
    resources: bytes | None
    metadata: dict
    warnings: tuple[str, ...]
    channel_ids: tuple[int, ...]
    layer_state: object | None = None

    @classmethod
    def capture(cls, doc, channel_ids):
        return cls(tuple(doc.channels), doc.photoshop_resources, dict(doc.metadata),
                   tuple(doc.warnings), tuple(channel_ids), doc.layer_state)

    @property
    def bytes(self):
        # Include metadata payloads as well as planes in the bounded history.
        return ((len(self.resources) if self.resources else 0) +
                len(json.dumps(self.metadata, default=str, ensure_ascii=False).encode("utf-8")) +
                sum(len(c.name.encode("utf-8")) + 64 for c in self.channels) +
                sum(len(w.encode("utf-8")) for w in self.warnings))


@dataclass
class StructuralPatch:
    before: Layout
    after: Layout
    forward_order: tuple[int | None, ...] | None
    reverse_order: tuple[int | None, ...] | None
    added_plane: np.ndarray | None
    deleted_plane: np.ndarray | None
    previous_state: int
    next_state: int

    @property
    def bytes(self):
        return (self.before.bytes + self.after.bytes +
                (0 if self.added_plane is None else self.added_plane.nbytes) +
                (0 if self.deleted_plane is None else self.deleted_plane.nbytes))


@dataclass
class LayerDelta:
    """Lossless, self-inverse native pixel differences in bounded row bands."""
    channel_indices: tuple[int, ...]
    bounds: tuple[int, int, int, int]
    bands: tuple[tuple[int, bytes], ...]

    @property
    def bytes(self):
        return 72 + sum(len(data) + 32 for _, data in self.bands)

    @classmethod
    def capture(cls, old, new, indices, bounds, budget):
        x0, y0, x1, y1 = bounds
        rows = max(1, 2**20 // ((x1 - x0) * len(indices) * old.dtype.itemsize))
        bands, used = [], 72
        for start in range(y0, y1, rows):
            stop = min(y1, start + rows)
            delta = np.bitwise_xor(old[start:stop, x0:x1, indices], new[start:stop, x0:x1, indices])
            encoded = zlib.compress(delta.tobytes(), level=1)
            used += len(encoded) + 32
            if used > budget:
                raise ValueError("Layer change exceeds the 128 MiB undo limit")
            bands.append((stop - start, encoded))
        return cls(indices, bounds, tuple(bands))


@dataclass
class LayerPatch:
    before: Layout
    after: Layout
    pixels: LayerDelta | None
    added_plane: np.ndarray | None
    previous_state: int
    next_state: int

    @property
    def bytes(self):
        return (self.before.bytes + self.after.bytes +
                (0 if self.pixels is None else self.pixels.bytes) +
                (0 if self.added_plane is None else self.added_plane.nbytes))


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
        self.history: list[Patch | StructuralPatch | LayerPatch] = []
        self.cursor = 0
        self.state = self.saved_state = self._sequence = 0
        self.history_limit = history_limit
        self._channel_ids = tuple(range(len(original.channels)))
        self._next_channel_id = len(self._channel_ids)

    @property
    def channel_ids(self):
        """Logical identities survive channel moves, undo and redo."""
        return self._channel_ids

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
        doc = self.document
        sources = doc.metadata.get("channel_source_indices", range(len(doc.channels)))
        changed = set()
        for index, source in enumerate(sources):
            channel = doc.channels[index]
            if source is None or not 0 <= source < len(self.original.channels):
                changed.add(index)
                continue
            before = self.original.channels[source]
            if (source != index or replace(channel, index=source) != before or
                    not np.array_equal(doc.samples[..., index], self.original.samples[..., source])):
                changed.add(index)
        return changed

    def mark_saved(self):
        self.saved_state = self.state

    def _editable(self):
        if self._samples is None:
            self._samples = self.document.samples.copy()
            self.document = replace(self.document, samples=self._samples.view(),
                                    metadata=dict(self.document.metadata), warnings=list(self.document.warnings))
        return orient(self._samples, self.document.orientation)

    def _adopt(self, document, channel_ids):
        # A metadata change can share our private writable backing array. Other
        # changes replace the plane layout and copy only on the next pixel edit.
        if self._samples is not None and not np.shares_memory(document.samples, self._samples):
            self._samples = None
        self.document = document
        self._channel_ids = tuple(channel_ids)

    def _push(self, patch):
        if patch.bytes > self.history_limit:
            raise ValueError("Change exceeds the 128 MiB undo limit")
        del self.history[self.cursor:]
        self.history.append(patch)
        self.cursor += 1
        while sum(p.bytes for p in self.history) > self.history_limit and len(self.history) > 1:
            self.history.pop(0)
            self.cursor -= 1

    def _structure(self, document, channel_ids, forward_order=None, reverse_order=None,
                   added_plane=None, deleted_plane=None):
        before = Layout.capture(self.document, self.channel_ids)
        after = Layout.capture(document, channel_ids)
        self._sequence += 1
        patch = StructuralPatch(before, after, forward_order, reverse_order,
                                added_plane, deleted_plane, self.state, self._sequence)
        self._push(patch)
        self._adopt(document, channel_ids)
        self.state = patch.next_state

    def add_spot(self, name: str, color=None, solidity=None, source_index=None) -> int:
        """Add an empty native mask or duplicate a spot, retaining exact undo."""
        doc = self.document
        plane_bytes = doc.samples[..., 0].nbytes
        if plane_bytes > self.history_limit:
            raise ValueError("Channel exceeds the 128 MiB undo limit")
        result = spots.add_spot(doc, name, color, solidity, source_index)
        index = len(doc.channels)
        ids = (*self.channel_ids, self._next_channel_id)
        forward = (*range(index), None)
        reverse = tuple(range(index))
        plane = result.samples[..., index].copy()
        plane.flags.writeable = False
        self._structure(result, ids, forward, reverse, added_plane=plane)
        self._next_channel_id += 1
        return index

    def delete_spot(self, index: int) -> bool:
        doc = self.document
        result = spots.delete_spot(doc, index)
        order = tuple(i for i in range(len(doc.channels)) if i != index)
        reverse = tuple(None if i == index else i if i < index else i - 1
                        for i in range(len(doc.channels)))
        plane = doc.samples[..., index].copy()
        plane.flags.writeable = False
        self._structure(result, tuple(self.channel_ids[i] for i in order), order, reverse,
                        deleted_plane=plane)
        return True

    def move_spot(self, index: int, target_sequence: int) -> int:
        doc = self.document
        result = spots.move_spot(doc, index, target_sequence)
        if result is doc:
            return index
        slots = [c.index for c in doc.channels if c.kind == "Spot"]
        moved = slots.copy()
        moved.remove(index)
        moved.insert(target_sequence - 1, index)
        order = list(range(len(doc.channels)))
        for slot, source in zip(slots, moved):
            order[slot] = source
        reverse = tuple(order.index(i) for i in range(len(order)))
        self._structure(result, tuple(self.channel_ids[i] for i in order), tuple(order), reverse)
        return slots[target_sequence - 1]

    def update_spot(self, index: int, name: str, color=None, solidity=None) -> bool:
        result = spots.update_spot(self.document, index, name, color, solidity)
        if result is self.document:
            return False
        self._structure(result, self.channel_ids)
        return True

    def attach_layers(self, stack):
        """Attach a lazily decoded layer inventory without making an edit."""
        if self.document.layer_stack is stack and self.document.layer_state is not None:
            return
        if self.document.layer_stack is not None and self.document.layer_state is not None:
            raise ValueError("A different layer stack is already attached")
        state = layerediting.default_state(stack)
        layerediting.validate_state(stack, state)
        self.original.layer_stack = stack
        self.original.layer_state = state
        if self.document is not self.original:
            self.document = replace(self.document, layer_stack=stack, layer_state=state)
        # Spot history may predate lazy layer loading. Its captured layouts
        # describe this same baseline layer state, so undo must retain it.
        for patch in self.history:
            if isinstance(patch, StructuralPatch):
                for layout in (patch.before, patch.after):
                    if layout.layer_state is None:
                        layout.layer_state = state

    def _layer_change(self, state):
        doc = self.document
        stack = doc.layer_stack
        if stack is None or doc.layer_state is None:
            raise ValueError("Load Photoshop layers before editing their visibility or order")
        layerediting.validate_state(stack, state)
        if state == doc.layer_state:
            return False
        layerediting.require_unchanged_merged_pixels(self.original, doc, stack)
        result, added = layerediting.recompose_document(doc, stack, state)
        indices = tuple(range(doc.base_count)) + tuple(c.index for c in doc.channels if c.kind == "Transparency")
        old, new = doc.display_samples, result.display_samples
        difference = np.zeros(old.shape[:2], dtype=bool)
        for index in indices:
            difference |= old[..., index] != new[..., index]
        pixel_patch = None
        ids = (*self.channel_ids, self._next_channel_id) if added else self.channel_ids
        before_layout, after_layout = Layout.capture(doc, self.channel_ids), Layout.capture(result, ids)
        required = before_layout.bytes + after_layout.bytes + (result.samples[..., -1].nbytes if added else 0)
        if required > self.history_limit:
            raise ValueError("Layer change exceeds the 128 MiB undo limit")
        if np.any(difference):
            ys = np.flatnonzero(np.any(difference, axis=1))
            xs = np.flatnonzero(np.any(difference, axis=0))
            x0, y0, x1, y1 = int(xs[0]), int(ys[0]), int(xs[-1]) + 1, int(ys[-1]) + 1
            pixel_patch = LayerDelta.capture(old, new, indices, (x0, y0, x1, y1),
                                            self.history_limit - required)
        plane = result.samples[..., -1].copy() if added else None
        if plane is not None:
            plane.flags.writeable = False
        patch = LayerPatch(before_layout, after_layout,
                           pixel_patch, plane, self.state, self._sequence + 1)
        # Allocate and validate everything before changing history or the
        # document. A rejected large composite therefore remains untouched.
        self._push(patch)
        self._sequence += 1
        self._adopt(result, ids)
        self.state = patch.next_state
        if added:
            self._next_channel_id += 1
        return True

    def set_layer_visibility(self, index: int, visible: bool) -> bool:
        state = self.document.layer_state
        if state is None or not isinstance(index, int) or isinstance(index, bool) or index not in state.order:
            raise ValueError("Choose a valid Photoshop layer")
        if not isinstance(visible, bool):
            raise ValueError("Layer visibility must be true or false")
        shown = set(state.visible)
        shown.add(index) if visible else shown.discard(index)
        return self._layer_change(layerediting.LayerState(state.order, frozenset(shown)))

    def move_layer(self, index: int, delta: int) -> int:
        state = self.document.layer_state
        if state is None or not isinstance(index, int) or isinstance(index, bool) or index not in state.order:
            raise ValueError("Choose a valid Photoshop layer")
        if not isinstance(delta, int) or isinstance(delta, bool) or delta not in (-1, 1):
            raise ValueError("Move a layer up or down by one position")
        position = state.order.index(index)
        target = position + delta
        if not 0 <= target < len(state.order):
            return index
        order = list(state.order)
        order[position], order[target] = order[target], order[position]
        self._layer_change(layerediting.LayerState(tuple(order), state.visible))
        return index

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
        self._push(patch)
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
            patch = self.history[self.cursor - 1]
            if isinstance(patch, LayerPatch):
                self._restore_layer_patch(patch, forward=False)
            elif isinstance(patch, StructuralPatch):
                layout = patch.before
                document = spots.restore_layout(self.document, layout.channels, layout.resources,
                                                layout.metadata, patch.reverse_order, patch.deleted_plane,
                                                layout.warnings)
                self._adopt(replace(document, layer_state=layout.layer_state), layout.channel_ids)
            else:
                self._write(patch, patch.before)
            self.cursor -= 1
            self.state = patch.previous_state

    def redo(self):
        if self.can_redo:
            patch = self.history[self.cursor]
            if isinstance(patch, LayerPatch):
                self._restore_layer_patch(patch, forward=True)
            elif isinstance(patch, StructuralPatch):
                layout = patch.after
                document = spots.restore_layout(self.document, layout.channels, layout.resources,
                                                layout.metadata, patch.forward_order, patch.added_plane,
                                                layout.warnings)
                self._adopt(replace(document, layer_state=layout.layer_state), layout.channel_ids)
            else:
                self._write(patch, patch.after)
            self.cursor += 1
            self.state = patch.next_state

    def _restore_layer_patch(self, patch: LayerPatch, forward: bool):
        layout = patch.after if forward else patch.before
        order = None
        if patch.added_plane is not None:
            count = len(patch.before.channels)
            order = (*range(count), None) if forward else tuple(range(count))
        document = spots.restore_layout(self.document, layout.channels, layout.resources,
                                        layout.metadata, order, patch.added_plane if forward else None,
                                        layout.warnings)
        self._adopt(replace(document, layer_state=layout.layer_state), layout.channel_ids)
        if patch.pixels is not None:
            self._toggle_layer_delta(patch.pixels)
        if self.document.metadata.get("layer_composite_applied"):
            self.document = replace(self.document, layer_merged_samples=self.document.samples,
                                    layer_merged_transparency=next((c.index for c in self.document.channels
                                                                    if c.kind == "Transparency"), None))
            # Future painting must preserve the immutable proof array. It is
            # shared with this restored image and is never held in history.
            self._samples = None
        else:
            self.document = replace(self.document, layer_merged_samples=None, layer_merged_transparency=None)

    def _toggle_layer_delta(self, delta: LayerDelta):
        x0, y0, x1, _ = delta.bounds
        pixels = self._editable()
        for rows, encoded in delta.bands:
            values = np.frombuffer(zlib.decompress(encoded), dtype=pixels.dtype)
            values = values.reshape(rows, x1 - x0, len(delta.channel_indices))
            region = pixels[y0:y0 + rows, x0:x1]
            for position, channel in enumerate(delta.channel_indices):
                np.bitwise_xor(region[..., channel], values[..., position], out=region[..., channel])
            y0 += rows
