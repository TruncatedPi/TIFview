"""Self-contained SVG alignment jobs and physical-size vector export.

Qt renders the admitted static geometry subset. Millimetres use the SVG/CSS
96 px/in definition: https://www.w3.org/TR/SVG2/coords.html#Units
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QTransform
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QGraphicsItem


SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)
MAX_SVG_BYTES = 2 * 2**20
_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_LENGTH = re.compile(rf"\s*({_NUMBER})\s*(mm|cm|in|pt|pc|px)?\s*")
_ELEMENTS = {"svg", "g", "defs", "path", "rect", "circle", "ellipse", "line",
             "polyline", "polygon", "title", "desc", "metadata"}
_ATTRIBUTES = {"id", "version", "width", "height", "x", "y", "viewBox", "preserveAspectRatio",
               "transform", "d", "points", "x1", "x2", "y1", "y2", "cx", "cy", "r", "rx", "ry",
               "fill", "fill-rule", "fill-opacity", "stroke", "stroke-width", "stroke-linecap",
               "stroke-linejoin", "stroke-miterlimit", "stroke-dasharray", "stroke-dashoffset",
               "stroke-opacity", "opacity", "display", "visibility", "style", "overflow"}
_STYLE = {"fill", "fill-rule", "fill-opacity", "stroke", "stroke-width", "stroke-linecap",
          "stroke-linejoin", "stroke-miterlimit", "stroke-dasharray", "stroke-dashoffset",
          "stroke-opacity", "opacity", "display", "visibility"}


def _numbers(value):
    matches = list(re.finditer(_NUMBER, value))
    if re.sub(_NUMBER, "", value).strip(" ,\t\r\n") or len(matches) > 50000:
        raise ValueError("Invalid or excessively complex SVG numbers.")
    numbers = [float(match[0]) for match in matches]
    if not all(math.isfinite(v) and abs(v) <= 10**8 for v in numbers):
        raise ValueError("SVG coordinates exceed the supported range.")
    return numbers


def _validate_path(value):
    arity = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0}
    matches = list(re.finditer(r"([MmLlHhVvCcSsQqTtAaZz])([^MmLlHhVvCcSsQqTtAaZz]*)", value))
    if (not matches or matches[0].start() != len(value) - len(value.lstrip())
            or matches[0][1].upper() != "M"):
        raise ValueError("SVG path must start with a valid moveto.")
    total = 0
    for match in matches:
        command = match[1].upper()
        numbers = _numbers(match[2])
        count = arity[command]
        if (not count and numbers) or (count and (not numbers or len(numbers) % count)):
            raise ValueError("Incomplete SVG path command.")
        if command == "A":
            for i in range(0, len(numbers), 7):
                if numbers[i] < 0 or numbers[i + 1] < 0 or any(numbers[i + flag] not in (0, 1) for flag in (3, 4)):
                    raise ValueError("Invalid SVG elliptical arc.")
        total += len(numbers) + 1
        if total > 50000:
            raise ValueError("SVG path exceeds the geometry limit.")
    return total


def _validate_transform(value):
    count = 0
    for match in re.finditer(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^()]*)\)", value):
        if value[count:match.start()].strip(" ,\t\r\n"):
            raise ValueError("Unsupported SVG transform.")
        numbers = _numbers(match[2])
        sizes = {"matrix": (6,), "translate": (1, 2), "scale": (1, 2), "rotate": (1, 3), "skewX": (1,), "skewY": (1,)}
        if len(numbers) not in sizes[match[1]]:
            raise ValueError("Invalid SVG transform.")
        count = match.end()
    if value[count:].strip(" ,\t\r\n"):
        raise ValueError("Unsupported SVG transform.")


def length_mm(value):
    match = _LENGTH.fullmatch(value or "")
    if not match:
        raise ValueError("SVG needs an explicit positive width and height (mm, cm, in, pt, pc or px).")
    number = float(match[1])
    scale = {"mm": 1, "cm": 10, "in": 25.4, "pt": 25.4 / 72,
             "pc": 25.4 / 6, "px": 25.4 / 96, None: 25.4 / 96}[match[2]]
    result = number * scale
    if not math.isfinite(result) or not 0 < result <= 100000:
        raise ValueError("SVG physical size is outside the supported range.")
    return result


def _root(raw):
    if not raw or len(raw) > MAX_SVG_BYTES:
        raise ValueError("SVG exceeds the 2 MiB limit or is empty.")
    # Normalize the document encoding before checking XML declarations. This
    # also prevents UTF-16 from concealing a DTD in the raw byte scan.
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = raw.decode("utf-16")
    else:
        text = raw.decode("utf-8-sig")
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("Save a self-contained SVG without DTD or entity declarations.")
    if "<?xml-stylesheet" in text.lower():
        raise ValueError("External SVG stylesheets are unsupported.")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError(f"Invalid SVG XML: {exc}") from exc
    if root.tag not in (f"{{{SVG_NS}}}svg", "svg"):
        raise ValueError("The document is not an SVG image.")
    return root


@dataclass(frozen=True)
class SvgArtwork:
    raw: bytes
    name: str
    width_mm: float
    height_mm: float
    view_box: tuple[float, float, float, float]
    source_path: Path | None = field(default=None, compare=False, repr=False)

    @classmethod
    def read(cls, path):
        path = Path(path)
        if path.stat().st_size > MAX_SVG_BYTES:
            raise ValueError("SVG exceeds the 2 MiB limit.")
        return replace(cls.from_bytes(path.read_bytes(), path.name), source_path=path.resolve())

    @classmethod
    def from_bytes(cls, raw, name="Artwork.svg"):
        root = _root(raw)
        width, height = length_mm(root.get("width")), length_mm(root.get("height"))
        if "transform" in root.attrib or root.get("overflow", "hidden") != "hidden":
            raise ValueError("Root SVG transforms or visible overflow are unsupported; move transforms into a group.")
        view_box = root.get("viewBox")
        if view_box is None:
            view_box = (0., 0., width * 96 / 25.4, height * 96 / 25.4)
        else:
            try:
                view_box = tuple(float(v) for v in re.split(r"[\s,]+", view_box.strip()))
            except ValueError as exc:
                raise ValueError("Invalid SVG viewBox.") from exc
            if (len(view_box) != 4 or not all(math.isfinite(v) for v in view_box)
                    or min(view_box[2:]) <= 0 or max(abs(v) for v in view_box) > 10**8):
                raise ValueError("Invalid SVG viewBox.")
        aspect = root.get("preserveAspectRatio", "xMidYMid meet").strip()
        if aspect not in ("none", "xMidYMid", "xMidYMid meet"):
            raise ValueError("This SVG viewport alignment is unsupported; use xMidYMid meet or none.")
        # Preserve viewport aspect on import; callers uniformly scale it later.
        references, identifiers, count, geometry_size = [], set(), 0, 0

        def check(element, depth=0):
            nonlocal count, geometry_size
            count += 1
            if count > 20000 or depth > 64:
                raise ValueError("SVG geometry exceeds the current complexity limit.")
            tag = element.tag
            local = tag.rsplit("}", 1)[-1]
            if local not in _ELEMENTS or (tag.startswith("{") and not tag.startswith("{" + SVG_NS + "}")):
                raise ValueError(f"SVG element {local} is unsupported. Convert text to paths and remove effects or embedded images.")
            if local == "svg" and element is not root:
                raise ValueError("Nested SVG viewports are unsupported on import.")
            if local in ("metadata", "title", "desc"):
                return
            if local == "path":
                geometry_size += _validate_path(element.get("d", ""))
            elif local in ("polyline", "polygon"):
                points = _numbers(element.get("points", ""))
                if len(points) < 4 or len(points) % 2:
                    raise ValueError("Incomplete SVG polygon/polyline.")
                geometry_size += len(points)
            if geometry_size > 100000:
                raise ValueError("SVG document exceeds the geometry limit.")
            if element.get("id"):
                identifier = element.get("id")
                if identifier in identifiers:
                    raise ValueError("Duplicate SVG element ID.")
                identifiers.add(identifier)
            for key, value in element.attrib.items():
                attribute = key.rsplit("}", 1)[-1]
                if key.startswith("{") and key not in ("{http://www.w3.org/1999/xlink}href",):
                    # Editor provenance (Inkscape/Sodipodi), not rendering instructions.
                    continue
                if attribute not in _ATTRIBUTES:
                    raise ValueError(f"SVG attribute {attribute} is unsupported.")
                if attribute == "transform":
                    _validate_transform(value)
                if attribute == "style":
                    for declaration in value.split(";"):
                        if not declaration.strip():
                            continue
                        prop, sep, _ = declaration.partition(":")
                        if not sep or prop.strip() not in _STYLE:
                            raise ValueError(f"SVG style {prop.strip()} is unsupported.")
                if attribute == "href":
                    if not value.startswith("#") or len(value) < 2:
                        raise ValueError("External SVG references are unsupported.")
                    references.append(value[1:])
                for reference in re.findall(r"url\s*\(\s*['\"]?([^)'\"]+)['\"]?\s*\)", value, re.I):
                    if not reference.strip().startswith("#"):
                        raise ValueError("External SVG resources are unsupported.")
                    references.append(reference.strip()[1:])
            for child in element:
                check(child, depth + 1)

        check(root)
        if any(ref not in identifiers for ref in references):
            raise ValueError("SVG contains an unresolved element reference.")
        if references:
            # Cyclic <use> can turn a small XML file into unbounded rendering.
            # The first release deliberately admits paths instead of instances.
            if any(e.tag.rsplit("}", 1)[-1] == "use" for e in root.iter()):
                raise ValueError("Expand SVG clone/use instances to paths before importing.")
        # Canonical self-contained XML gives renderer/export the same viewport.
        root.set("viewBox", " ".join(format(v, ".15g") for v in view_box))
        raw = ET.tostring(root, encoding="utf-8")
        renderer = QSvgRenderer(QByteArray(raw))
        renderer.setAnimationEnabled(False)
        if not renderer.isValid():
            raise ValueError("Qt could not render this SVG.")
        # Qt SVG does not implement SVG clipPath/nested viewport clipping.
        # Admit complete geometry inside the page instead of silently drawing
        # cropped or partially missing cut paths, or exporting a clipping mask
        # that a cutter might ignore.
        probe = copy.deepcopy(root)
        probe_id = "tifview-bounds"
        while probe_id in identifiers:
            probe_id += "-1"
        probe.set("id", probe_id)
        probe_renderer = QSvgRenderer(QByteArray(ET.tostring(probe, encoding="utf-8")))
        bounds = probe_renderer.boundsOnElement(probe_id)
        page_bounds = QRectF(*view_box).adjusted(-1e-6, -1e-6, 1e-6, 1e-6)
        if bounds.isEmpty():
            raise ValueError("SVG contains no visible geometry.")
        if not page_bounds.contains(bounds):
            raise ValueError("SVG geometry or stroke extends outside its viewBox. Expand the SVG page to contain the full cut path before importing.")
        return cls(raw, Path(name).name, width, height, view_box)


@dataclass(frozen=True)
class Placement:
    x_mm: float = 0
    y_mm: float = 0
    width_mm: float = 1
    height_mm: float = 1
    angle: float = 0
    visible: bool = True

    def __post_init__(self):
        if (not all(math.isfinite(v) and abs(v) <= 100000 for v in
                    (self.x_mm, self.y_mm, self.width_mm, self.height_mm, self.angle))
                or min(self.width_mm, self.height_mm) <= 0 or not -360 <= self.angle <= 360
                or type(self.visible) is not bool):
            raise ValueError("Invalid SVG placement.")


class SvgItem(QGraphicsItem):
    """Render vector geometry at every zoom, without converting the TIFF."""
    def __init__(self, artwork):
        super().__init__()
        self.artwork = artwork
        self.renderer = QSvgRenderer(QByteArray(artwork.raw))
        self.renderer.setAnimationEnabled(False)
        aspect = _root(artwork.raw).get("preserveAspectRatio", "xMidYMid meet")
        self.renderer.setAspectRatioMode(Qt.AspectRatioMode.IgnoreAspectRatio if aspect == "none"
                                         else Qt.AspectRatioMode.KeepAspectRatio)
        self.rect = QRectF(0, 0, artwork.width_mm, artwork.height_mm)
        self.setZValue(2)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def boundingRect(self):
        return self.rect

    def paint(self, painter, option, widget=None):
        painter.save()
        painter.setClipRect(self.rect, Qt.ClipOperation.IntersectClip)
        self.renderer.render(painter, self.rect)
        painter.restore()

    def place(self, placement, dpi):
        self.prepareGeometryChange()
        self.rect = QRectF(0, 0, placement.width_mm, placement.height_mm)
        transform = QTransform()
        transform.scale(dpi[0] / 25.4, dpi[1] / 25.4)
        transform.translate(placement.x_mm, placement.y_mm)
        transform.rotate(placement.angle)
        self.setTransform(transform)
        self.setVisible(placement.visible)
        self.update()


def _fingerprint(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def job_data(doc, artwork, placement, image_path=None):
    dpi = doc.metadata.get("dpi")
    if dpi is None:
        raise ValueError("Image has no calibrated print resolution.")
    image_path = Path(image_path or doc.path)
    return {"format": "TIFview SVG alignment", "version": 1,
            "image": {"name": image_path.name, "sha256": _fingerprint(image_path),
                      "width": doc.width, "height": doc.height, "dpi": list(dpi)},
            "svg": artwork.raw.decode("utf-8"), "svg_name": artwork.name,
            "placement": asdict(placement)}


def load_job(path, doc):
    path = Path(path)
    if path.stat().st_size > MAX_SVG_BYTES * 6:
        raise ValueError("Alignment job exceeds the size limit.")
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("format") != "TIFview SVG alignment" or value.get("version") != 1:
        raise ValueError("Unsupported alignment job format.")
    image = value.get("image", {})
    if (image.get("sha256") != _fingerprint(doc.path) or
            (image.get("width"), image.get("height")) != (doc.width, doc.height) or
            tuple(image.get("dpi", ())) != tuple(doc.metadata.get("dpi") or ())):
        raise ValueError("This job belongs to a different image or print size. Open the associated original image first.")
    artwork = SvgArtwork.from_bytes(value["svg"].encode("utf-8"), value["svg_name"])
    placement = Placement(**value["placement"])
    return artwork, placement


def export_svg(doc, artwork, placement):
    dpi = doc.metadata.get("dpi")
    if not dpi or not all(math.isfinite(v) and v > 0 for v in dpi):
        raise ValueError("Image has no calibrated print resolution.")
    width, height = doc.width * 25.4 / dpi[0], doc.height * 25.4 / dpi[1]
    page = ET.Element(f"{{{SVG_NS}}}svg", {
        "width": f"{width:.15g}mm", "height": f"{height:.15g}mm",
        "viewBox": f"0 0 {width:.15g} {height:.15g}", "version": "1.1"})
    # Visibility is an inspection control. Export explicitly includes the cut
    # geometry even if it was hidden while checking image channels.
    group = ET.SubElement(page, f"{{{SVG_NS}}}g", {
        "transform": f"translate({placement.x_mm:.15g} {placement.y_mm:.15g}) rotate({placement.angle:.15g})"})
    source = _root(artwork.raw)
    # Flatten only viewport nesting, never geometry. Qt SVG does not support
    # nested <svg> viewports, whereas ordinary SVG groups work across viewers.
    bx, by, bw, bh = artwork.view_box
    sx, sy = placement.width_mm / bw, placement.height_mm / bh
    if source.get("preserveAspectRatio", "xMidYMid meet") != "none":
        sx = sy = min(sx, sy)
    tx = -bx * sx + (placement.width_mm - bw * sx) / 2
    ty = -by * sy + (placement.height_mm - bh * sy) / 2
    attributes = {key: value for key, value in source.attrib.items()
                  if key.rsplit("}", 1)[-1] in _STYLE | {"id"}}
    attributes["transform"] = f"matrix({sx:.15g} 0 0 {sy:.15g} {tx:.15g} {ty:.15g})"
    geometry_group = ET.SubElement(group, f"{{{SVG_NS}}}g", attributes)
    geometry_group.extend(copy.deepcopy(list(source)))
    return ET.tostring(page, encoding="utf-8", xml_declaration=True)
