"""Bounded, lazy access to Photoshop TIFF layer records and cached pixels.

Opaque tagged descriptors are never parsed or serialized. The original layer
records and compressed channel chunks remain byte-for-byte intact, apart from
the visibility flag when explicitly requested. Layer records in Photoshop TIFF
tag 37724 are stored bottom first; all public ordering arguments are top first.

The Adobe file-format tables describe the records and channel compression:
https://www.adobe.com/devnet-apps/photoshop/fileformatashtml/
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, replace
from pathlib import Path
import struct
import threading
import zlib

import numpy as np
import tifffile

from .model import Channel, ImageDocument, orient
from .reader import file_signature
from .render import render


_SIGNATURE = b"Adobe Photoshop Document Data Block\0"
_DEPTH_KEYS = {b"Layr": 8, b"Lr16": 16, b"Lr32": 32}
_NEUTRAL_RANGE = b"\0\0\xff\xff\0\0\xff\xff"
_ADJUSTMENTS = {b"SoCo", b"GdFl", b"PtFl", b"brit", b"levl", b"curv", b"expA",
                b"vibA", b"hue ", b"hue2", b"blnc", b"blwh", b"phfl", b"mixr",
                b"clrL", b"nvrt", b"post", b"thrs", b"grdm", b"selc", b"CgEd"}
_EFFECTS = {b"lrFX", b"lfx2", b"lmfx", b"lfxs"}
_VECTORS = {b"vmsk", b"vsms", b"vscg", b"vogk", b"vstk"}
_CACHE_BYTES = 96 * 2**20
_DECODE_BYTES = 512 * 2**20
_RAW_BYTES = 512 * 2**20
_MAX_PIXELS = 40_000_000
_MAX_DIMENSION = 300_000


def _coordinate_slice(coordinates, offset=0):
    """A regular sampled interval as a view, avoiding cartesian-index copies."""
    step = int(coordinates[1] - coordinates[0]) if len(coordinates) > 1 else 1
    return slice(int(coordinates[0]) - offset, int(coordinates[-1]) - offset + 1, step)


class LayerError(ValueError):
    """Malformed or unsupported layer structure, reported without guessing."""


class UnsupportedLayerError(LayerError):
    """The requested preview or recomposition requires unsupported features."""


class _Cursor:
    def __init__(self, data: memoryview, byteorder: str, offset: int = 0):
        self.data, self.byteorder, self.offset, self.pos = data, byteorder, offset, 0

    @property
    def remaining(self):
        return len(self.data) - self.pos

    def take(self, count):
        if count < 0 or count > self.remaining:
            raise LayerError("Truncated Photoshop layer data.")
        start = self.pos
        self.pos += count
        return self.data[start:self.pos]

    def unpack(self, fmt):
        return struct.unpack(self.byteorder + fmt, self.take(struct.calcsize(self.byteorder + fmt)))

    def absolute(self):
        return self.offset + self.pos


@dataclass(frozen=True)
class _Tag:
    key: bytes
    start: int
    end: int
    payload_start: int
    payload_end: int


def _tags(cursor: _Cursor, alignment: int):
    expected = b"MIB8" if cursor.byteorder == "<" else b"8BIM"
    result = []
    while cursor.remaining:
        start = cursor.absolute()
        if bytes(cursor.take(4)) != expected:
            raise LayerError("Unsupported Photoshop layer tag signature.")
        key = bytes(cursor.take(4))
        if cursor.byteorder == "<":
            key = key[::-1]
        (length,) = cursor.unpack("I")
        payload_start = cursor.absolute()
        cursor.take(length)
        payload_end = cursor.absolute()
        cursor.take((-length) % alignment)
        result.append(_Tag(key, start, cursor.absolute(), payload_start, payload_end))
    return tuple(result)


@dataclass(frozen=True)
class LayerMask:
    bounds: tuple[int, int, int, int]
    default: int
    flags: int
    density: int = 255
    feather: float = 0.0
    issue: str | None = None

    @property
    def disabled(self):
        return bool(self.flags & 2)


@dataclass(frozen=True)
class Layer:
    index: int  # Stable original record index; never the current display position.
    name: str
    bounds: tuple[int, int, int, int]
    visible: bool
    opacity: int
    blend_mode: str
    kind: str
    layer_id: int | None
    channel_ids: tuple[int, ...]
    issues: tuple[str, ...]
    mask: LayerMask | None
    _record_start: int
    _record_end: int
    _flags_offset: int
    _flags: int
    _channel_sizes: tuple[int, ...]
    _channel_spans: tuple[tuple[int, int], ...] = ()

    @property
    def width(self):
        return self.bounds[3] - self.bounds[1]

    @property
    def height(self):
        return self.bounds[2] - self.bounds[0]

    @property
    def has_pixels(self):
        return self.width > 0 and self.height > 0

    @property
    def preview_note(self):
        return "; ".join(self.issues)


@dataclass(frozen=True)
class LayerPixels:
    samples: np.ndarray  # TIFF polarity, native unsigned process samples only.
    alpha: np.ndarray  # Native unassociated transparency, white = opaque.
    bounds: tuple[int, int, int, int]
    mask: np.ndarray | None = None

    @property
    def nbytes(self):
        return self.samples.nbytes + self.alpha.nbytes + (0 if self.mask is None else self.mask.nbytes)


def _parse_mask(data: memoryview, byteorder: str):
    if not data:
        return None
    cursor = _Cursor(data, byteorder)
    bounds = cursor.unpack("4i")
    default, flags = cursor.unpack("2B")
    if default not in (0, 255):
        raise LayerError("Unsupported Photoshop layer mask default colour.")
    if bounds[2] < bounds[0] or bounds[3] < bounds[1]:
        raise LayerError("Invalid Photoshop layer mask rectangle.")
    density, feather, issue = 255, 0.0, None
    if flags & 16:  # Adobe mask-parameter flag; not the rendered-mask flag (8).
        (parameters,) = cursor.unpack("B")
        if parameters & 1:
            (density,) = cursor.unpack("B")
        if parameters & 2:
            (feather,) = cursor.unpack("d")
        if parameters & 4:
            cursor.take(1)
            issue = "vector mask parameters"
        if parameters & 8:
            cursor.take(8)
            issue = "vector mask parameters"
        if parameters & ~15:
            issue = "unknown layer mask parameters"
    if flags & 1:
        issue = "relative layer mask coordinates"
    if feather != 0 or not np.isfinite(feather):
        issue = "feathered layer mask"
    if len(data) == 20:
        # A density parameter can occupy the two bytes otherwise used for
        # padding. Older plain masks still have both pad bytes here.
        cursor.take(cursor.remaining)
    elif cursor.remaining:
        # Extended masks also include a real-user-mask rectangle. Rendering
        # that relationship requires vector-mask handling, which is separate.
        if cursor.remaining >= 18:
            cursor.take(18)
        if cursor.remaining > 3 or any(cursor.take(cursor.remaining)):
            raise LayerError("Unsupported Photoshop layer mask framing.")
    if cursor.remaining:
        raise LayerError("Unsupported trailing Photoshop layer mask data.")
    return LayerMask(bounds, default, flags, density, feather, issue)


def _decode_channel(data: memoryview, shape: tuple[int, int], bits: int, byteorder: str):
    """Decode one bounded channel; Photoshop pixel words are always big endian."""
    height, width = shape
    expected = height * width * (bits // 8)
    if (height < 0 or width < 0 or max(height, width) > _MAX_DIMENSION
            or height * width > _MAX_PIXELS or expected > _DECODE_BYTES):
        raise LayerError("Photoshop layer exceeds the current decoded pixel limit.")
    if len(data) < 2:
        raise LayerError("Truncated Photoshop layer compression header.")
    (compression,) = struct.unpack(byteorder + "H", data[:2])
    compressed = data[2:]
    if compression not in (0, 1, 2, 3):
        raise UnsupportedLayerError(f"Photoshop layer compression {compression} is unsupported.")
    if not expected:
        if compressed:
            raise LayerError("Unexpected pixels in an empty Photoshop layer channel.")
        return np.zeros(shape, dtype=f"uint{bits}")
    if compression == 0:
        if len(compressed) != expected:
            raise LayerError("Photoshop RAW layer channel length does not match its rectangle.")
        decoded = compressed
    elif compression == 1:
        import imagecodecs

        table_size = height * 2
        if table_size > len(compressed):
            raise LayerError("Truncated Photoshop RLE row table.")
        sizes = np.frombuffer(compressed[:table_size], dtype=byteorder + "u2")
        if sum(int(size) for size in sizes) != len(compressed) - table_size:
            raise LayerError("Photoshop RLE row lengths do not match the bounded channel.")
        decoded = bytearray(expected)
        start, row_size = table_size, width * (bits // 8)
        for row, size in enumerate(sizes):
            end = start + int(size)
            try:
                line = imagecodecs.packbits_decode(compressed[start:end], out=row_size)
            except Exception as exc:
                raise LayerError(f"Invalid Photoshop RLE row: {exc}") from exc
            if len(line) != row_size:
                raise LayerError("Photoshop RLE row decoded to the wrong length.")
            decoded[row * row_size:(row + 1) * row_size] = line
            start = end
    else:
        stream = zlib.decompressobj()
        try:
            decoded = stream.decompress(compressed, expected + 1)
        except zlib.error as exc:
            raise LayerError(f"Invalid Photoshop ZIP channel: {exc}") from exc
        if len(decoded) != expected or not stream.eof or stream.unused_data or stream.unconsumed_tail:
            raise LayerError("Photoshop ZIP channel does not match its bounded rectangle.")
    values = np.frombuffer(decoded, dtype="u1" if bits == 8 else ">u2").reshape(shape)
    if compression == 3:
        # Photoshop's integer prediction operates along each row of samples,
        # rather than cumulatively through the complete byte stream.
        values = np.cumsum(values, axis=1, dtype=np.uint64).astype(f"uint{bits}")
    else:
        values = np.array(values, dtype=f"uint{bits}", order="C", copy=True)
    return values


class LayerStack:
    """Original immutable layer bytes, metadata, and a bounded native pixel cache."""

    def __init__(self, data: bytes, parent: ImageDocument, *, cache_bytes: int = _CACHE_BYTES):
        if len(data) > _RAW_BYTES:
            raise LayerError("Photoshop layer data exceeds the current 512-MiB raw metadata limit.")
        self.data, self.parent = bytes(data), parent
        self.cache_limit = max(0, int(cache_bytes))
        self._cache = OrderedDict()
        self._cache_size = 0
        self._lock = threading.RLock()
        self._parse()

    @classmethod
    def from_bytes(cls, data: bytes, parent: ImageDocument, **kwargs):
        return cls(data, parent, **kwargs)

    @classmethod
    def from_document(cls, document: ImageDocument, **kwargs):
        """Read tag 37724 only on demand, with source-change protection."""
        expected = document.metadata.get("source_signature")
        if expected is not None and file_signature(document.path) != expected:
            raise LayerError("The source changed since import. Reopen it before inspecting layers.")
        with tifffile.TiffFile(document.path, mode="r") as tif:
            if not tif.pages:
                raise LayerError("This TIFF contains no image directory with Photoshop layers.")
            tag = tif.pages[0].tags.get(37724)
            if tag is None:
                raise LayerError("This TIFF does not contain Photoshop layer data.")
            if int(tag.dtype) not in (1, 7):
                raise LayerError("Photoshop layer data must use a TIFF byte or undefined-byte tag type.")
            # This property multiplies the declared count by its datatype size
            # without reading the tag value or allocating its payload.
            if tag.valuebytecount > _RAW_BYTES:
                raise LayerError("Photoshop layer data exceeds the current 512-MiB raw metadata limit.")
            data = tag.value
        if expected is not None and file_signature(document.path) != expected:
            raise LayerError("The source changed while reading Photoshop layers. Reopen it.")
        return cls(data, document, **kwargs)

    @property
    def cache_nbytes(self):
        return self._cache_size

    def clear_cache(self):
        with self._lock:
            self._cache.clear()
            self._cache_size = 0

    def _parse(self):
        if not self.data.startswith(_SIGNATURE):
            raise LayerError("Unsupported Photoshop layer document signature.")
        payload = memoryview(self.data)[len(_SIGNATURE):]
        if bytes(payload[:4]) not in (b"8BIM", b"MIB8"):
            raise LayerError("Unsupported or empty Photoshop layer document format.")
        self.byteorder = "<" if bytes(payload[:4]) == b"MIB8" else ">"
        self._top_tags = _tags(_Cursor(payload, self.byteorder, len(_SIGNATURE)), 4)
        global_issues = []
        for top in self._top_tags:
            value = memoryview(self.data)[top.payload_start:top.payload_end]
            if top.key == b"LMsk" and value:
                # This standard block controls mask-overlay colour/opacity;
                # flag 128 delegates masks to the individual layer records.
                # Historical document-wide mask variants are not interpreted.
                if len(value) not in (13, 14) or value[12] != 128:
                    global_issues.append("unsupported global layer mask settings")
            if top.key in _ADJUSTMENTS | _EFFECTS | _VECTORS | {b"brst", b"Alph"}:
                global_issues.append(f"unsupported document-level layer setting {top.key.decode('ascii', errors='replace')}")
        self.global_issues = tuple(global_issues)
        candidates = [tag for tag in self._top_tags if tag.key in _DEPTH_KEYS and tag.payload_end > tag.payload_start]
        if len(candidates) != 1:
            raise LayerError("Expected one unambiguous Photoshop layer pixel block.")
        self._layer_tag = candidates[0]
        self.bits = _DEPTH_KEYS[self._layer_tag.key]
        if self.bits not in (8, 16) or self.bits != self.parent.bits:
            raise UnsupportedLayerError("Only matching 8-bit or 16-bit TIFF layer pixels are supported.")
        if self.parent.color_mode not in ("RGB", "CMYK", "Gray", "WhiteIsZero"):
            raise UnsupportedLayerError("Layer previews support RGB, CMYK and grayscale TIFFs.")
        tag = self._layer_tag
        cursor = _Cursor(memoryview(self.data)[tag.payload_start:tag.payload_end], self.byteorder, tag.payload_start)
        (self._signed_count,) = cursor.unpack("h")
        count = abs(self._signed_count)
        if count > 4096:
            raise LayerError("The Photoshop layer count exceeds the current limit.")
        layers = []
        for index in range(count):
            start = cursor.absolute()
            bounds = cursor.unpack("4i")
            if bounds[2] < bounds[0] or bounds[3] < bounds[1]:
                raise LayerError("Invalid Photoshop layer rectangle.")
            (channel_count,) = cursor.unpack("H")
            if channel_count > 56:
                raise LayerError("The Photoshop layer channel count exceeds the current limit.")
            channel_ids, sizes = [], []
            for _ in range(channel_count):
                channel, size = cursor.unpack("hI")
                if channel in channel_ids:
                    raise LayerError("Duplicate Photoshop layer channel ID.")
                if size < 2:
                    raise LayerError("Invalid Photoshop layer compressed channel length.")
                channel_ids.append(channel)
                sizes.append(size)
            signature = bytes(cursor.take(4))
            if signature != (b"MIB8" if self.byteorder == "<" else b"8BIM"):
                raise LayerError("Unsupported Photoshop layer blend signature.")
            blend = bytes(cursor.take(4))
            if self.byteorder == "<":
                blend = blend[::-1]
            opacity, clipping = cursor.unpack("2B")
            flags_offset = cursor.absolute()
            flags, _ = cursor.unpack("2B")
            (extra_size,) = cursor.unpack("I")
            extra_offset = cursor.absolute()
            extra = _Cursor(cursor.take(extra_size), self.byteorder, extra_offset)
            (mask_size,) = extra.unpack("I")
            mask = _parse_mask(extra.take(mask_size), self.byteorder)
            (ranges_size,) = extra.unpack("I")
            ranges = extra.take(ranges_size)
            if ranges_size % 8:
                raise LayerError("Unsupported Photoshop layer blending-range framing.")
            (name_size,) = extra.unpack("B")
            name = bytes(extra.take(name_size)).decode("macroman")
            extra.take((-(name_size + 1)) % 4)
            tags = _tags(extra, 2)
            keys = {tag.key for tag in tags}
            layer_id, kind, issues = None, "Raster", []
            for nested in tags:
                value = memoryview(self.data)[nested.payload_start:nested.payload_end]
                if nested.key == b"luni":
                    value_cursor = _Cursor(value, self.byteorder)
                    (units,) = value_cursor.unpack("I")
                    if units > 1_000_000:
                        raise LayerError("Photoshop layer name is too long.")
                    try:
                        name = bytes(value_cursor.take(units * 2)).decode(
                            "utf-16-le" if self.byteorder == "<" else "utf-16-be").rstrip("\0")
                    except UnicodeError as exc:
                        raise LayerError("Invalid Unicode Photoshop layer name.") from exc
                elif nested.key == b"lyid" and len(value) == 4:
                    (layer_id,) = struct.unpack(self.byteorder + "I", value)
                elif nested.key in (b"lsct", b"lsdk"):
                    if len(value) < 4:
                        raise LayerError("Truncated Photoshop layer group marker.")
                    (section,) = struct.unpack(self.byteorder + "I", value[:4])
                    if section in (1, 2, 3):
                        kind = "Group" if section in (1, 2) else "Group end"
                        issues.append("layer groups")
                elif nested.key == b"iOpa":
                    if not value or value[0] != 255:
                        issues.append("fill opacity")
                elif nested.key == b"brst":
                    issues.append("restricted channel blending")
                elif nested.key == b"knko" and any(value):
                    issues.append("layer knockout")
            if keys & _ADJUSTMENTS:
                kind = "Adjustment / fill"
                issues.append("adjustment or fill layer")
            elif keys & _VECTORS:
                kind = "Vector / shape"
                issues.append("vector masks or shapes")
            elif b"TySh" in keys:
                kind = "Text (cached pixels)"
            elif keys & {b"SoLd", b"SoLE", b"PlLd"}:
                kind = "Smart object (cached pixels)"
            if keys & _EFFECTS:
                issues.append("layer effects")
            if keys & {b"FXid", b"FEid", b"ffxi"}:
                issues.append("smart filter effects")
            if clipping:
                issues.append("clipped layer")
            blend_name = blend.decode("ascii", errors="replace")
            if blend != b"norm":
                issues.append(f"blend mode {blend_name}")
            if any(ranges[offset:offset + 8] != _NEUTRAL_RANGE for offset in range(0, ranges_size, 8)):
                issues.append("custom Blend If ranges")
            if flags & 8 and flags & 16:
                issues.append("pixels do not describe the layer appearance")
            if any(channel < -3 or channel >= self.parent.base_count for channel in channel_ids):
                issues.append("extra or unknown layer channels")
            if mask is not None and not mask.disabled:
                if mask.issue:
                    issues.append(mask.issue)
                if -2 not in channel_ids:
                    issues.append("missing layer mask pixels")
                if -3 in channel_ids:
                    issues.append("combined user and vector mask")
            if bounds[2] > bounds[0] and bounds[3] > bounds[1] and not all(
                    channel in channel_ids for channel in range(self.parent.base_count)):
                issues.append("missing process-channel pixels")
            layers.append(Layer(index, name or f"Layer {index + 1}", bounds, not bool(flags & 2),
                                opacity, blend_name, kind, layer_id, tuple(channel_ids), tuple(dict.fromkeys(issues)),
                                mask, start, cursor.absolute(), flags_offset, flags, tuple(sizes)))
        self._records_end = cursor.absolute()
        for index, layer in enumerate(layers):
            spans = []
            for size in layer._channel_sizes:
                start = cursor.absolute()
                cursor.take(size)
                spans.append((start, cursor.absolute()))
            layers[index] = replace(layer, _channel_spans=tuple(spans))
        self._pixels_end = cursor.absolute()
        if cursor.remaining > 1 or (cursor.remaining == 1 and bytes(cursor.take(1)) != b"\0"):
            raise LayerError("Unsupported trailing Photoshop layer pixel data.")
        self.layers = tuple(layers)
        self.default_order = tuple(reversed(range(count)))
        self.default_visible = frozenset(layer.index for layer in self.layers if layer.visible)

    def _selection(self, order=None, visible=None):
        order = self.default_order if order is None else tuple(order)
        if len(order) != len(self.layers) or set(order) != set(range(len(self.layers))):
            raise LayerError("Layer order must contain each original layer exactly once.")
        visible = self.default_visible if visible is None else frozenset(visible)
        if not visible <= set(range(len(self.layers))):
            raise LayerError("Layer visibility contains an unknown layer.")
        return order, visible

    def composite_reason(self, order=None, visible=None):
        order, visible = self._selection(order, visible)
        if self.global_issues:
            return "; ".join(self.global_issues) + "."
        if any(layer.kind.startswith("Group") for layer in self.layers):
            return "Layer groups cannot yet be recomposed or reordered safely."
        for index in order:
            layer = self.layers[index]
            if index in visible and layer.opacity and layer.issues:
                return f"{layer.name}: {layer.preview_note}."
        return None

    def decode_layer(self, index: int):
        if not 0 <= index < len(self.layers):
            raise IndexError("Layer index out of range.")
        with self._lock:
            cached = self._cache.get(index)
            if cached is not None:
                self._cache.move_to_end(index)
                return cached
        layer = self.layers[index]
        shape = layer.height, layer.width
        base_count, bits = self.parent.base_count, self.bits
        byte_count = layer.height * layer.width * (base_count + 1) * (bits // 8)
        if (max(layer.height, layer.width) > _MAX_DIMENSION
                or layer.height * layer.width > _MAX_PIXELS or byte_count > _DECODE_BYTES):
            raise LayerError("This layer exceeds the current 40-million-pixel / 512-MiB decoded limit.")
        if not all(channel in layer.channel_ids for channel in range(base_count)) and layer.has_pixels:
            raise UnsupportedLayerError("This layer does not contain every process-channel pixel plane.")
        maximum = (1 << bits) - 1
        samples = np.zeros((*shape, base_count), dtype=f"uint{bits}")
        alpha = np.full(shape, maximum, dtype=f"uint{bits}")
        mask = None
        for channel, (start, end) in zip(layer.channel_ids, layer._channel_spans):
            if channel < -2 or channel >= base_count:
                continue
            channel_shape = shape
            if channel == -2:
                if layer.mask is None:
                    continue
                top, left, bottom, right = layer.mask.bounds
                channel_shape = bottom - top, right - left
                if byte_count + channel_shape[0] * channel_shape[1] * (bits // 8) > _DECODE_BYTES:
                    raise LayerError("The layer and its mask exceed the decoded-memory limit.")
            plane = _decode_channel(memoryview(self.data)[start:end], channel_shape, bits, self.byteorder)
            if channel >= 0:
                if self.parent.color_mode in ("CMYK", "WhiteIsZero"):
                    np.subtract(maximum, plane, out=plane)
                samples[..., channel] = plane
            elif channel == -1:
                alpha = plane
            else:
                mask = plane
        for array in (samples, alpha, mask):
            if array is not None:
                array.flags.writeable = False
        result = LayerPixels(samples, alpha, layer.bounds, mask)
        with self._lock:
            if result.nbytes <= self.cache_limit:
                while self._cache and self._cache_size + result.nbytes > self.cache_limit:
                    _, old = self._cache.popitem(last=False)
                    self._cache_size -= old.nbytes
                previous = self._cache.pop(index, None)
                if previous is not None:
                    self._cache_size -= previous.nbytes
                self._cache[index] = result
                self._cache_size += result.nbytes
        return result

    def _effective_alpha(self, layer: Layer, pixels: LayerPixels, y: np.ndarray, x: np.ndarray,
                         *, raw_preview: bool = False):
        maximum = (1 << self.bits) - 1
        local_y, local_x = y - layer.bounds[0], x - layer.bounds[1]
        alpha = pixels.alpha[_coordinate_slice(local_y), _coordinate_slice(local_x)].astype(np.float64) / maximum
        alpha *= layer.opacity / 255
        mask = layer.mask
        if mask is not None and not mask.disabled and pixels.mask is not None:
            if mask.issue and raw_preview:
                return alpha
            if mask.issue:
                raise UnsupportedLayerError(f"{layer.name}: {mask.issue}.")
            mask_y, mask_x = y - mask.bounds[0], x - mask.bounds[1]
            coverage = np.full(alpha.shape, mask.default / 255, dtype=np.float64)
            inside_y = np.flatnonzero((mask_y >= 0) & (mask_y < pixels.mask.shape[0]))
            inside_x = np.flatnonzero((mask_x >= 0) & (mask_x < pixels.mask.shape[1]))
            if len(inside_y) and len(inside_x):
                coverage[inside_y[0]:inside_y[-1] + 1, inside_x[0]:inside_x[-1] + 1] = (
                    pixels.mask[_coordinate_slice(mask_y[inside_y]), _coordinate_slice(mask_x[inside_x])] / maximum)
            if mask.flags & 4:
                coverage = 1 - coverage
            if mask.density != 255:
                coverage = 1 - (1 - coverage) * (mask.density / 255)
            alpha *= coverage
        return alpha

    def _compose(self, order, visible, stride=1, *, raw_preview=False):
        if not isinstance(stride, int) or stride < 1:
            raise ValueError("Layer preview stride must be a positive integer.")
        height, width = self.parent.samples.shape[:2]
        if max(height, width) > _MAX_DIMENSION:
            raise LayerError("The layer canvas exceeds the current dimension limit.")
        ys, xs = np.arange(0, height, stride), np.arange(0, width, stride)
        maximum, count = (1 << self.bits) - 1, self.parent.base_count
        output = np.zeros((len(ys), len(xs), count + 1), dtype=f"uint{self.bits}")
        band_rows = max(1, 65536 // max(1, len(xs)))
        active = [index for index in reversed(order) if index in visible
                  and self.layers[index].has_pixels and self.layers[index].opacity]
        if len(active) <= 1:
            # A single layer has no colour blending: retain its native words
            # directly, calculating only mask/opacity coverage in float bands.
            background = 0 if self.parent.color_mode in ("CMYK", "WhiteIsZero") else maximum
            output[..., :count] = background
            if active:
                layer = self.layers[active[0]]
                ix = np.flatnonzero((xs >= layer.bounds[1]) & (xs < layer.bounds[3]))
                if len(ix) and np.any((ys >= layer.bounds[0]) & (ys < layer.bounds[2])):
                    pixels = self.decode_layer(active[0])
                    for y_start in range(0, len(ys), band_rows):
                        by = ys[y_start:y_start + band_rows]
                        iy = np.flatnonzero((by >= layer.bounds[0]) & (by < layer.bounds[2]))
                        if not len(iy):
                            continue
                        alpha = self._effective_alpha(layer, pixels, by[iy], xs[ix], raw_preview=raw_preview)
                        region = output[y_start + iy[0]:y_start + iy[-1] + 1, ix[0]:ix[-1] + 1]
                        region[..., :count] = pixels.samples[_coordinate_slice(by[iy], layer.bounds[0]),
                                                            _coordinate_slice(xs[ix], layer.bounds[1])]
                        np.copyto(region[..., :count], background, where=alpha[..., None] == 0)
                        region[..., count] = np.rint(alpha * maximum)
            output.flags.writeable = False
            return output
        decoded_layers, decoded_bytes = {}, 0
        # Band compositing keeps floating-point work bounded even at 100% zoom.
        for y_start in range(0, len(ys), band_rows):
            by = ys[y_start:y_start + band_rows]
            premultiplied = np.zeros((len(by), len(xs), count), dtype=np.float64)
            alpha_out = np.zeros((len(by), len(xs)), dtype=np.float64)
            for index in reversed(order):
                if index not in visible:
                    continue
                layer = self.layers[index]
                if not layer.has_pixels or not layer.opacity:
                    continue
                iy = np.flatnonzero((by >= layer.bounds[0]) & (by < layer.bounds[2]))
                ix = np.flatnonzero((xs >= layer.bounds[1]) & (xs < layer.bounds[3]))
                if not len(iy) or not len(ix):
                    continue
                pixels = decoded_layers.get(index)
                if pixels is None:
                    pixels = self.decode_layer(index)
                    if decoded_bytes + pixels.nbytes > _DECODE_BYTES:
                        raise LayerError("The visible layer stack exceeds the 512-MiB decoded-memory limit.")
                    decoded_layers[index] = pixels
                    decoded_bytes += pixels.nbytes
                source_alpha = self._effective_alpha(layer, pixels, by[iy], xs[ix], raw_preview=raw_preview)
                source = pixels.samples[_coordinate_slice(by[iy], layer.bounds[0]),
                                        _coordinate_slice(xs[ix], layer.bounds[1])].astype(np.float64)
                source /= maximum
                destination = premultiplied[iy[0]:iy[-1] + 1, ix[0]:ix[-1] + 1]
                destination *= 1 - source_alpha[..., None]
                destination += source * source_alpha[..., None]
                old_alpha = alpha_out[iy[0]:iy[-1] + 1, ix[0]:ix[-1] + 1]
                old_alpha *= 1 - source_alpha
                old_alpha += source_alpha
            np.divide(premultiplied, alpha_out[..., None], out=premultiplied, where=alpha_out[..., None] > 0)
            background = 0 if self.parent.color_mode in ("CMYK", "WhiteIsZero") else 1
            np.copyto(premultiplied, background, where=alpha_out[..., None] == 0)
            np.clip(premultiplied, 0, 1, out=premultiplied)
            output[y_start:y_start + len(by), :, :count] = np.rint(premultiplied * maximum)
            output[y_start:y_start + len(by), :, count] = np.rint(alpha_out * maximum)
        output.flags.writeable = False
        return output

    def composite_samples(self, order=None, visible=None):
        """Native stored-orientation process + unassociated alpha, alpha last.

        Callers preserve spot/saved-alpha samples separately. If the original
        TIFF uses associated transparency, its process samples need association
        with the returned alpha before writing the merged primary image.
        """
        order, visible = self._selection(order, visible)
        reason = self.composite_reason(order, visible)
        if reason:
            raise UnsupportedLayerError(reason)
        return self._compose(order, visible)

    def _render_samples(self, samples):
        channels = list(self.parent.channels[:self.parent.base_count])
        channels.append(Channel(self.parent.base_count, "Layer transparency", "Transparency",
                                "Photoshop layer alpha", (130, 130, 130), associated=False))
        doc = ImageDocument(Path(self.parent.path), samples, channels, self.parent.color_mode,
                            self.parent.base_count, self.bits, self.parent.orientation,
                            icc_profile=self.parent.icc_profile if self.parent.icc_transform is not None else None,
                            icc_transform=self.parent.icc_transform)
        return render(doc)

    def render_layer(self, index: int, stride: int = 1):
        """Show cached layer pixels at their original canvas position.

        Unsupported adjustments/groups/shapes without cached process pixels
        cannot be displayed as raster artwork. Effects, clipping, vectors and
        unsupported masks are not synthesized; callers show ``preview_note``.
        """
        if not 0 <= index < len(self.layers):
            raise IndexError("Layer index out of range.")
        layer = self.layers[index]
        if not layer.has_pixels:
            raise UnsupportedLayerError(f"{layer.name} has no cached raster pixels to display ({layer.kind}).")
        return self._render_samples(self._compose((index,), {index}, stride, raw_preview=True))

    def render_composite(self, order=None, visible=None, stride: int = 1):
        order, visible = self._selection(order, visible)
        reason = self.composite_reason(order, visible)
        if reason:
            raise UnsupportedLayerError(reason)
        return self._render_samples(self._compose(order, visible, stride))

    def rewrite(self, order=None, visible=None):
        """Reorder original record/chunk pairs and change only hidden-bit flags."""
        order, visible = self._selection(order, visible)
        reason = self.composite_reason(order, visible)
        if reason:
            raise UnsupportedLayerError(reason)
        if order == self.default_order and visible == self.default_visible:
            return self.data
        tag = self._layer_tag
        records, pixels = [], []
        for index in reversed(order):  # File records are bottom first.
            layer = self.layers[index]
            record = bytearray(self.data[layer._record_start:layer._record_end])
            offset = layer._flags_offset - layer._record_start
            record[offset] = layer._flags & ~2 if index in visible else layer._flags | 2
            records.append(record)
            pixels.extend(memoryview(self.data)[start:end] for start, end in layer._channel_spans)
        payload = (struct.pack(self.byteorder + "h", self._signed_count)
                   + b"".join(records) + b"".join(pixels)
                   + self.data[self._pixels_end:tag.payload_end])
        if len(payload) != tag.payload_end - tag.payload_start:
            raise LayerError("Layer rewrite unexpectedly changed the original block length.")
        return self.data[:tag.payload_start] + payload + self.data[tag.payload_end:]
