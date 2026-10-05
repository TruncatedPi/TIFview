"""Bounded Photoshop path/solid-shape reader; original descriptors stay opaque on save.

Only the explicitly understood solid-colour Normal shape subset is rendered.
Adobe reference: https://www.adobe.com/devnet-apps/photoshop/fileformatashtml/
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import struct

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter, QPainterPath, QPainterPathStroker


class VectorError(ValueError):
    pass


class Cursor:
    def __init__(self, data, endian):
        if len(data) > 2 * 2**20:
            raise VectorError("vector descriptor exceeds the 2 MiB limit")
        self.data, self.endian, self.pos, self.nodes = memoryview(data), endian, 0, 0

    def take(self, size):
        if size < 0 or self.pos + size > len(self.data):
            raise VectorError("truncated vector descriptor")
        result = bytes(self.data[self.pos:self.pos + size])
        self.pos += size
        return result

    def number(self, fmt):
        return struct.unpack(self.endian + fmt, self.take(struct.calcsize(fmt)))[0]

    def code(self):
        data = self.take(4)
        return data[::-1] if self.endian == "<" else data

    def key(self):
        size = self.number("I")
        if size > 65536:
            raise VectorError("vector descriptor key exceeds the limit")
        return (self.take(size) if size else self.code()).decode("ascii")

    def text(self):
        size = self.number("I")
        if size > 65536:
            raise VectorError("vector descriptor text exceeds the limit")
        return self.take(size * 2).decode("utf-16-le" if self.endian == "<" else "utf-16-be")

    def descriptor(self, depth=0):
        if depth > 16:
            raise VectorError("vector descriptor nesting exceeds the limit")
        self.text()
        result = {"class": self.key()}
        size = self.number("I")
        if size > 4096:
            raise VectorError("vector descriptor item count exceeds the limit")
        for _ in range(size):
            key = self.key()
            if key in result:
                raise VectorError("duplicate vector descriptor key")
            result[key] = self.value(self.code(), depth + 1)
        return result

    def value(self, kind, depth):
        self.nodes += 1
        if self.nodes > 8192 or depth > 16:
            raise VectorError("vector descriptor complexity exceeds the limit")
        if kind in (b"Objc", b"GlbO"):
            return self.descriptor(depth)
        if kind == b"doub":
            value = self.number("d")
            if not math.isfinite(value):
                raise VectorError("non-finite vector value")
            return value
        if kind == b"long":
            return self.number("i")
        if kind == b"bool":
            value = self.number("B")
            if value not in (0, 1):
                raise VectorError("invalid vector boolean")
            return bool(value)
        if kind == b"enum":
            return (self.key(), self.key())
        if kind == b"UntF":
            return (self.code().decode("ascii"), self.value(b"doub", depth + 1))
        if kind == b"TEXT":
            return self.text()
        if kind == b"VlLs":
            count = self.number("I")
            if count > 4096:
                raise VectorError("vector list exceeds the limit")
            return [self.value(self.code(), depth + 1) for _ in range(count)]
        raise VectorError(f"unsupported vector descriptor type {kind!r}")

    def finish(self):
        remaining = self.take(len(self.data) - self.pos)
        if len(remaining) > 3 or any(remaining):
            raise VectorError("unexpected trailing vector descriptor data")


def descriptor(data, endian, content=False):
    cursor = Cursor(data, endian)
    key = cursor.code() if content else None
    if cursor.number("I") != 16:
        raise VectorError("unsupported vector descriptor version")
    result = cursor.descriptor()
    cursor.finish()
    if content and key != b"SoCo":
        raise VectorError("only solid-colour vector content is supported")
    return result


@dataclass(frozen=True)
class Subpath:
    closed: bool
    operation: int
    knots: tuple  # Incoming, anchor, outgoing (y, x) control points, normalized.


@dataclass(frozen=True)
class Geometry:
    subpaths: tuple[Subpath, ...]
    initial_fill: bool
    inverted: bool
    disabled: bool

    def path(self, width, height):
        result = QPainterPath()
        result.setFillRule(Qt.FillRule.OddEvenFill)
        if self.initial_fill:
            result.addRect(0, 0, width, height)
        for subpath in self.subpaths:
            path = QPainterPath()
            path.setFillRule(Qt.FillRule.OddEvenFill)
            knots = subpath.knots
            path.moveTo(knots[0][3] * width, knots[0][2] * height)
            pairs = zip(knots, knots[1:] + (knots[:1] if subpath.closed else ()))
            for previous, current in pairs:
                path.cubicTo(previous[5] * width, previous[4] * height,
                             current[1] * width, current[0] * height,
                             current[3] * width, current[2] * height)
            if subpath.closed:
                path.closeSubpath()
            if subpath.operation == 1:
                # addPath retains open curves, unlike area-union on an empty path.
                if result.isEmpty():
                    result = path
                else:
                    result = result.united(path)
            elif subpath.operation == 2:
                result = result.subtracted(path)
            elif subpath.operation == 3:
                result = result.intersected(path)
            else:
                result = result.united(path).subtracted(result.intersected(path))
        if self.inverted:
            canvas = QPainterPath()
            canvas.addRect(0, 0, width, height)
            result = canvas.subtracted(result)
        return result


def geometry(data, endian):
    cursor = Cursor(data, endian)
    if cursor.number("I") != 3:
        raise VectorError("unsupported vector mask version")
    flags = cursor.number("I")
    if flags & ~7:
        raise VectorError("unsupported vector mask flags")
    paths, pending, initial, fill_seen = [], None, False, False
    while len(cursor.data) - cursor.pos >= 26:
        kind = cursor.number("H")
        raw = cursor.take(24)
        if kind in (0, 3):
            if pending is not None:
                raise VectorError("incomplete vector subpath")
            count, operation = struct.unpack(endian + "2H", raw[:4])
            # Older records reserve all bytes after count; modern records use
            # operation plus opaque provenance fields. Both are retained on save.
            if not any(raw[2:]):
                operation = 1
            if not 0 < count <= 8192 or operation not in (0, 1, 2, 3):
                raise VectorError("unsupported vector subpath length or operation")
            pending = [kind == 0, count, operation, []]
        elif kind in (1, 2, 4, 5):
            if pending is None or (kind in (1, 2)) != pending[0]:
                raise VectorError("unexpected vector knot")
            knot = tuple(value / 2**24 for value in struct.unpack(endian + "6i", raw))
            pending[3].append(knot)
            if len(pending[3]) == pending[1]:
                paths.append(Subpath(pending[0], pending[2], tuple(pending[3])))
                pending = None
        elif kind == 6:
            if any(raw) or fill_seen:
                raise VectorError("unsupported vector fill rule")
            fill_seen = True
        elif kind == 8:
            value = struct.unpack(endian + "H", raw[:2])[0]
            if value not in (0, 1) or any(raw[2:]):
                raise VectorError("unsupported initial vector fill")
            initial = bool(value)
        else:
            raise VectorError("unsupported vector path record")
        if len(paths) > 1024:
            raise VectorError("vector subpath count exceeds the limit")
    cursor.finish()
    if pending or not paths:
        raise VectorError("empty or incomplete vector path")
    return Geometry(tuple(paths), initial, bool(flags & 1), bool(flags & 4))


def colour(value, mode):
    value = value.get("Clr ", {})
    cls = value.get("class")
    keys, scale = {"RGBC": (("Rd  ", "Grn ", "Bl  "), 255),
                   "CMYC": (("Cyn ", "Mgnt", "Ylw ", "Blck"), 100),
                   "Grsc": (("Gry ",), 100)}.get(cls, ((), 1))
    expected = {"RGB": "RGBC", "CMYK": "CMYC", "Gray": "Grsc", "WhiteIsZero": "Grsc"}.get(mode)
    if not keys or cls != expected:
        raise VectorError("vector colour space does not match the TIFF process channels")
    if set(value) != {"class", *keys}:
        raise VectorError("unsupported vector colour parameters")
    values = tuple(float(value[k]) / scale for k in keys)
    if not all(math.isfinite(v) and 0 <= v <= 1 for v in values):
        raise VectorError("invalid vector colour")
    if mode == "WhiteIsZero":
        values = tuple(1 - v for v in values)
    return values


def width_pixels(value, resolution):
    unit, amount = value
    factors = {"#Pxl": 1, "#Pnt": resolution / 72, "#Mlm": resolution / 25.4,
               "#Rlt": resolution / 72}
    if unit not in factors or not math.isfinite(amount) or not 0 <= amount <= 10000:
        raise VectorError("unsupported vector stroke width")
    width = amount * factors[unit]
    if width > 30000:
        raise VectorError("vector stroke width exceeds the limit")
    return width


@dataclass(frozen=True)
class Shape:
    geometry: Geometry
    fill: tuple | None
    stroke: tuple | None
    stroke_width: float
    alignment: str = "strokeStyleAlignCenter"
    cap: str = "strokeStyleButtCap"
    join: str = "strokeStyleMiterJoin"
    miter: float = 10
    stroke_opacity: float = 1

    def paths(self, width, height):
        path = self.geometry.path(width, height)
        stroke = QPainterPath()
        if self.stroke is not None and self.stroke_width:
            stroker = QPainterPathStroker()
            stroker.setWidth(self.stroke_width * (1 if self.alignment == "strokeStyleAlignCenter" else 2))
            stroker.setCapStyle({"strokeStyleButtCap": Qt.PenCapStyle.FlatCap,
                                 "strokeStyleRoundCap": Qt.PenCapStyle.RoundCap,
                                 "strokeStyleSquareCap": Qt.PenCapStyle.SquareCap}[self.cap])
            stroker.setJoinStyle({"strokeStyleMiterJoin": Qt.PenJoinStyle.MiterJoin,
                                  "strokeStyleRoundJoin": Qt.PenJoinStyle.RoundJoin,
                                  "strokeStyleBevelJoin": Qt.PenJoinStyle.BevelJoin}[self.join])
            stroker.setMiterLimit(self.miter)
            stroke = stroker.createStroke(path)
            if self.alignment == "strokeStyleAlignOutside":
                stroke = stroke.subtracted(path)
            elif self.alignment == "strokeStyleAlignInside":
                stroke = stroke.intersected(path)
        return path, stroke

    def pixels(self, width, height, bits, base_count):
        # Rasterize coverage independently of colour, retaining 16-bit colour
        # words and CMYK ink values; no conversion through an RGB composite.
        fill_path, stroke_path = self.paths(width, height)
        maximum = 2**bits - 1
        samples = np.empty((height, width, base_count), dtype=f"uint{bits}")
        alpha = np.zeros((height, width), dtype=f"uint{bits}")
        rows = max(1, 65536 // max(1, width))
        for y in range(0, height, rows):
            count = min(rows, height - y)
            premult = np.zeros((count, width, base_count), np.float64)
            coverage = np.zeros((count, width), np.float64)
            for path, colour_value, opacity in ((fill_path, self.fill, 1),
                                                (stroke_path, self.stroke, self.stroke_opacity)):
                if colour_value is None or path.isEmpty() or not opacity:
                    continue
                mask = QImage(width, count, QImage.Format.Format_Grayscale8)
                if mask.isNull():
                    raise MemoryError("Not enough memory for vector coverage")
                mask.fill(0)
                painter = QPainter(mask)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.translate(0, -y)
                painter.fillPath(path, Qt.GlobalColor.white)
                painter.end()
                source = np.frombuffer(mask.constBits(), np.uint8).reshape(count, mask.bytesPerLine())[:, :width]
                opacity_mask = source.astype(np.float64) * (opacity / 255)
                premult *= 1 - opacity_mask[..., None]
                premult += np.asarray(colour_value) * opacity_mask[..., None]
                coverage = coverage * (1 - opacity_mask) + opacity_mask
            np.divide(premult, coverage[..., None], out=premult, where=coverage[..., None] > 0)
            samples[y:y + count] = np.rint(np.clip(premult, 0, 1) * maximum)
            alpha[y:y + count] = np.rint(coverage * maximum)
        return samples, alpha


def read_shape(tags, endian, mode):
    masks = [key for key in (b"vsms", b"vmsk") if key in tags]
    if len(masks) != 1:
        raise VectorError("shape requires one vector mask")
    path = geometry(tags[masks[0]], endian)
    if path.disabled or path.inverted or path.initial_fill:
        raise VectorError("disabled, inverted or initially filled shape mask")
    if any(not subpath.closed or subpath.operation != 1 for subpath in path.subpaths):
        raise VectorError("only closed, additive solid shapes are supported")
    content = (descriptor(tags[b"vscg"], endian, True) if b"vscg" in tags else
               descriptor(tags[b"SoCo"], endian) if b"SoCo" in tags else None)
    if content is None:
        raise VectorError("vector mask on a raster or non-solid fill layer")
    if b"vstk" not in tags:
        return Shape(path, colour(content, mode), None, 0)
    style = descriptor(tags[b"vstk"], endian)
    if style.get("class") != "strokeStyle" or style.get("strokeStyleVersion") != 2:
        raise VectorError("unsupported vector stroke version")
    known = {"class", "strokeStyleVersion", "strokeEnabled", "fillEnabled", "strokeStyleLineWidth",
             "strokeStyleLineDashOffset", "strokeStyleMiterLimit", "strokeStyleLineCapType",
             "strokeStyleLineJoinType", "strokeStyleLineAlignment", "strokeStyleScaleLock",
             "strokeStyleStrokeAdjust", "strokeStyleLineDashSet", "strokeStyleBlendMode",
             "strokeStyleOpacity", "strokeStyleContent", "strokeStyleResolution"}
    if set(style) - known:
        raise VectorError("unknown vector stroke settings")
    fill_enabled, stroke_enabled = style.get("fillEnabled"), style.get("strokeEnabled")
    if type(fill_enabled) is not bool or type(stroke_enabled) is not bool:
        raise VectorError("missing vector fill/stroke enable flags")
    fill = colour(content, mode) if fill_enabled else None
    if not stroke_enabled:
        return Shape(path, fill, None, 0)
    if style.get("strokeStyleLineDashSet") or style.get("strokeStyleLineDashOffset", ("#Pnt", 0))[1]:
        raise VectorError("dashed vector strokes")
    if style.get("strokeStyleBlendMode") != ("BlnM", "normal") or style.get("strokeStyleStrokeAdjust"):
        raise VectorError("non-normal or adjusted vector stroke")
    resolution = float(style["strokeStyleResolution"])
    if not math.isfinite(resolution) or not 0 < resolution <= 100000:
        raise VectorError("invalid vector stroke resolution")
    width = width_pixels(style["strokeStyleLineWidth"], resolution)
    alignment = style["strokeStyleLineAlignment"][1]
    cap, join = style["strokeStyleLineCapType"][1], style["strokeStyleLineJoinType"][1]
    if alignment not in ("strokeStyleAlignCenter", "strokeStyleAlignInside", "strokeStyleAlignOutside"):
        raise VectorError("unsupported vector stroke alignment")
    if cap not in ("strokeStyleButtCap", "strokeStyleRoundCap", "strokeStyleSquareCap"):
        raise VectorError("unsupported vector stroke cap")
    if join not in ("strokeStyleMiterJoin", "strokeStyleRoundJoin", "strokeStyleBevelJoin"):
        raise VectorError("unsupported vector stroke join")
    miter = float(style["strokeStyleMiterLimit"])
    if not math.isfinite(miter) or not 0 < miter <= 10000:
        raise VectorError("invalid vector miter limit")
    unit, opacity = style["strokeStyleOpacity"]
    if unit != "#Prc" or not 0 <= opacity <= 100:
        raise VectorError("invalid vector stroke opacity")
    if style["strokeStyleContent"].get("class") != "solidColorLayer":
        raise VectorError("non-solid vector stroke content")
    return Shape(path, fill, colour(style["strokeStyleContent"], mode), width,
                 alignment, cap, join, miter, opacity / 100)
