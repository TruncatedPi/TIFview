"""Geometry, native channels, opaque records and physical SVG alignment."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dataclasses import replace
import json
import struct
import xml.etree.ElementTree as ET

import numpy as np
import pytest
import tifffile
from psdtags import PsdFormat, PsdKey, PsdLayerFlag, PsdUnknown
from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QMessageBox

from tifview.editing import EditSession
from tifview.layers import LayerStack
from tifview.psvectors import VectorError, colour, descriptor, geometry, read_shape, width_pixels
from tifview.reader import load_image
from tifview.svg import Placement, SVG_NS, SvgArtwork, SvgItem, export_svg, job_data, length_mm, load_job
from tifview.writer import save_tiff_copy
from test_layers import encode_layers, parent, raster
from tools.make_demo import photoshop_resources


def fourcc(value, endian):
    return value[::-1] if endian == "<" else value


def descriptor_bytes(value, endian="<"):
    def key(value):
        raw = value.encode("ascii")
        return struct.pack(endian + "I", 0) + fourcc(raw, endian) if len(raw) == 4 else struct.pack(endian + "I", len(raw)) + raw

    def desc(value):
        parts = [struct.pack(endian + "I", 0), key(value["class"]), struct.pack(endian + "I", len(value) - 1)]
        for k, v in value.items():
            if k == "class":
                continue
            if isinstance(v, dict):
                kind, raw = b"Objc", desc(v)
            elif isinstance(v, bool):
                kind, raw = b"bool", bytes([v])
            elif isinstance(v, float):
                kind, raw = b"doub", struct.pack(endian + "d", v)
            elif isinstance(v, int):
                kind, raw = b"long", struct.pack(endian + "i", v)
            elif isinstance(v, tuple) and isinstance(v[1], str):
                kind, raw = b"enum", key(v[0]) + key(v[1])
            elif isinstance(v, tuple):
                kind, raw = b"UntF", fourcc(v[0].encode(), endian) + struct.pack(endian + "d", v[1])
            elif isinstance(v, list):
                assert not v
                kind, raw = b"VlLs", struct.pack(endian + "I", 0)
            parts.extend([key(k), fourcc(kind, endian), raw])
        return b"".join(parts)
    return struct.pack(endian + "I", 16) + desc(value)


def rectangle_geometry(endian="<", flags=0):
    raw = struct.pack(endian + "II", 3, flags)
    raw += struct.pack(endian + "H", 6) + bytes(24)
    raw += struct.pack(endian + "H", 8) + bytes(24)
    raw += struct.pack(endian + "4H", 0, 4, 1, 1) + bytes(18)
    for y, x in ((.25, .25), (.25, .75), (.75, .75), (.75, .25)):
        raw += struct.pack(endian + "H6i", 1, *([int(y * 2**24), int(x * 2**24)] * 3))
    return raw


def shape_tags(endian="<", stroke=False, mode="RGB"):
    rgb = {"class": "RGBC", "Rd  ": 255., "Grn ": 0., "Bl  ": 0.}
    if mode == "CMYK":
        rgb = {"class": "CMYC", "Cyn ": 12.5, "Mgnt": 50., "Ylw ": 100., "Blck": 0.}
    content = descriptor_bytes({"class": "null", "Clr ": rgb}, endian)
    tags = {b"vsms": rectangle_geometry(endian), b"vscg": fourcc(b"SoCo", endian) + content}
    if stroke:
        tags[b"vstk"] = descriptor_bytes({
            "class": "strokeStyle", "strokeStyleVersion": 2, "fillEnabled": False, "strokeEnabled": True,
            "strokeStyleLineWidth": ("#Mlm", 1.), "strokeStyleLineDashOffset": ("#Pnt", 0.),
            "strokeStyleMiterLimit": 100., "strokeStyleLineCapType": ("strokeStyleLineCapType", "strokeStyleButtCap"),
            "strokeStyleLineJoinType": ("strokeStyleLineJoinType", "strokeStyleMiterJoin"),
            "strokeStyleLineAlignment": ("strokeStyleLineAlignment", "strokeStyleAlignOutside"),
            "strokeStyleScaleLock": False, "strokeStyleStrokeAdjust": False, "strokeStyleLineDashSet": [],
            "strokeStyleBlendMode": ("BlnM", "normal"), "strokeStyleOpacity": ("#Prc", 100.),
            "strokeStyleContent": {"class": "solidColorLayer", "Clr ": rgb}, "strokeStyleResolution": 50.8}, endian)
    return tags


def shape_layer(endian="<", visible=False, stroke=False, mode="RGB", depth=8):
    fmt = PsdFormat.LE32BIT if endian == "<" else PsdFormat.BE32BIT
    tags = shape_tags(endian, stroke, mode)
    info = [PsdUnknown(PsdKey(key), fmt, raw) for key, raw in tags.items()]
    count = 4 if mode == "CMYK" else 3
    result = raster(np.empty((0, 0, count), dtype=f"uint{depth}"), name="Vector rectangle", visible=visible, info=info)
    result.flags |= PsdLayerFlag(16)
    return result


@pytest.mark.parametrize("endian", ["<", ">"])
@pytest.mark.parametrize("depth", [8, 16])
@pytest.mark.parametrize("mode", ["RGB", "CMYK"])
def test_empty_raster_shape_uses_path_and_native_process_values(depth, endian, mode):
    doc = parent(depth, mode, shape=(20, 20))
    stack = LayerStack(encode_layers([shape_layer(endian, True, mode=mode, depth=depth)], depth, endian), doc)
    layer = stack.layers[0]
    assert layer.bounds == (0, 0, 0, 0) and layer.paint_bounds == (0, 0, 20, 20)
    assert layer.kind == "Vector shape" and not layer.issues
    result = stack.composite_samples()
    assert result.dtype == doc.samples.dtype
    assert result[10, 10, -1] == doc.maximum and result[1, 1, -1] == 0
    expected = (1, 0, 0) if mode == "RGB" else (.125, .5, 1, 0)
    np.testing.assert_array_equal(result[10, 10, :-1], np.rint(np.array(expected) * doc.maximum))
    assert stack.rewrite() == stack.data


def test_outside_millimetre_stroke_is_not_a_filled_or_center_stroked_rectangle():
    shape = read_shape(shape_tags(stroke=True), "<", "RGB")
    assert shape.fill is None and shape.stroke_width == pytest.approx(2)
    samples, alpha = shape.pixels(20, 20, 8, 3)
    assert alpha[10, 10] == 0 and alpha[10, 5] == 0  # Interior starts at x=5.
    assert alpha[10, 4] == 255 and alpha[10, 3] == 255 and alpha[10, 2] == 0
    np.testing.assert_array_equal(samples[10, 4], [255, 0, 0])


@pytest.mark.parametrize("alignment,inside,outside", [("strokeStyleAlignInside", True, False),
                                                     ("strokeStyleAlignCenter", True, True)])
def test_inside_and_center_stroke_coverage(alignment, inside, outside):
    shape = replace(read_shape(shape_tags(stroke=True), "<", "RGB"), alignment=alignment)
    _, alpha = shape.pixels(20, 20, 8, 3)
    assert bool(alpha[10, 5]) == inside and bool(alpha[10, 4]) == outside


@pytest.mark.parametrize("value", [b"", struct.pack("<I", 17), struct.pack("<I", 16) + b"\xff" * 8,
                                     b"x" * (2 * 2**20 + 1)], ids=["empty", "version", "truncated", "oversize"])
def test_malformed_descriptors_are_bounded(value):
    with pytest.raises(VectorError):
        descriptor(value, "<")


def test_mask_flags_and_wrong_colour_space_remain_explicitly_unsupported():
    for flags in (1, 4, 8):
        tags = shape_tags()
        tags[b"vsms"] = rectangle_geometry(flags=flags)
        with pytest.raises(VectorError):
            read_shape(tags, "<", "RGB")
    with pytest.raises(VectorError, match="colour space"):
        read_shape(shape_tags(), "<", "CMYK")
    with pytest.raises(VectorError):
        geometry(rectangle_geometry()[:-27], "<")
    assert width_pixels(("#Pnt", 72), 600) == 600
    assert width_pixels(("#Mlm", 25.4), 360) == 360


def test_vector_visibility_save_and_undo_keep_original_descriptors_and_spot_pixels(tmp_path):
    background = np.full((20, 20, 3), [30, 50, 80], np.uint8)
    raw = encode_layers([raster(background), shape_layer(stroke=True)])
    path = tmp_path / "SYNTHETIC-vector-layers.tif"
    pixels = np.dstack([background, np.arange(400, dtype=np.uint8).reshape(20, 20)])
    resources = photoshop_resources(["White Ink"], [2])
    tifffile.imwrite(path, pixels, photometric="rgb", extrasamples=[0], metadata=None,
                     resolution=(360, 360), extratags=[(37724, 7, len(raw), raw, False),
                                                    (34377, 7, len(resources), resources, False)])
    original = path.read_bytes()
    doc = load_image(path)
    stack = LayerStack.from_document(doc)
    edits = EditSession(doc)
    edits.attach_layers(stack)
    edits.set_layer_visibility(1, True)
    assert not np.array_equal(edits.document.samples[..., :3], doc.samples[..., :3])
    np.testing.assert_array_equal(edits.document.samples[..., 3], pixels[..., 3])
    after = edits.document.samples.copy()
    edits.undo()
    np.testing.assert_array_equal(edits.document.samples, pixels)
    edits.redo()
    np.testing.assert_array_equal(edits.document.samples, after)
    target = tmp_path / "SYNTHETIC-shape-visible.tif"
    save_tiff_copy(doc, edits.document, target)
    saved = load_image(target)
    np.testing.assert_array_equal(saved.samples, after)
    saved_stack = LayerStack.from_document(saved)
    assert saved_stack.layers[1].visible and saved_stack.layers[1].vector is not None
    before_record = bytearray(raw[stack.layers[1]._record_start:stack.layers[1]._record_end])
    before_record[stack.layers[1]._flags_offset - stack.layers[1]._record_start] &= ~2
    saved_layer = saved_stack.layers[1]
    assert before_record == saved_stack.data[saved_layer._record_start:saved_layer._record_end]
    assert path.read_bytes() == original


def test_visible_vector_baseline_mismatch_does_not_silently_replace_native_composite(tmp_path):
    background = np.full((20, 20, 3), 80, np.uint8)
    raw = encode_layers([raster(background), shape_layer(visible=True)])
    path = tmp_path / "SYNTHETIC-vector-mismatch.tif"
    tifffile.imwrite(path, background, photometric="rgb", metadata=None,
                     extratags=[(37724, 7, len(raw), raw, False)])
    doc = load_image(path)
    session = EditSession(doc)
    session.attach_layers(LayerStack.from_document(doc))
    with pytest.raises(ValueError, match="does not match"):
        session.set_layer_visibility(1, False)
    np.testing.assert_array_equal(session.document.samples, background)
    assert not session.dirty


SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="50mm" height="25mm" viewBox="10 20 100 50"><path d="M20 30 L100 30 L100 60 L20 60 Z" fill="none" stroke="#ff0000" stroke-width="1"/></svg>'


@pytest.mark.parametrize("value,expected", [("1in", 25.4), ("72pt", 25.4), ("6pc", 25.4),
                                          ("96px", 25.4), ("96", 25.4), ("2.54cm", 25.4), ("25.4mm", 25.4)])
def test_svg_absolute_units(value, expected):
    assert length_mm(value) == pytest.approx(expected)


@pytest.mark.parametrize("body", ['<text>Cut me</text>', '<image href="file:///D:/a.png"/>',
                                   '<path filter="url(#blur)"/>', '<style>.a{fill:red}</style>',
                                   '<foreignObject/>', '<use href="#loop" id="loop"/>',
                                   '<path style="vector-effect:non-scaling-stroke"/>'])
def test_svg_rejects_appearance_it_cannot_reliably_render(body):
    raw = ('<svg xmlns="http://www.w3.org/2000/svg" width="1in" height="1in">' + body + '</svg>').encode()
    with pytest.raises(ValueError):
        SvgArtwork.from_bytes(raw)


def test_svg_dtd_is_rejected_in_utf16_too():
    text = '<!DOCTYPE svg [<!ENTITY x "test">]><svg width="10" height="10"/>'
    with pytest.raises(ValueError, match="DTD"):
        SvgArtwork.from_bytes(text.encode("utf-16"))


def test_svg_clipping_and_outside_page_geometry_are_rejected_explicitly():
    artwork = SvgArtwork.from_bytes(SVG.replace(b'M20 30 L100 30 L100 60 L20 60 Z', b'M0 0 L200 0 L200 100 L0 100 Z'))
    assert artwork.full_rect(50, 25).width() > 100
    with pytest.raises(ValueError, match="clipPath"):
        SvgArtwork.from_bytes(SVG.replace(b'<path ', b'<defs><clipPath id="clip"/></defs><path '))


@pytest.mark.parametrize("path", ["M20 30 L", "M20 30 L10", "M20 30 R50 60", "L20 30",
                                 "M20 30 A4 4 0 2 0 50 60", "M20 30 L1e999 60"])
def test_invalid_svg_paths_are_rejected_before_qt_silently_renders_a_partial_path(path):
    with pytest.raises(ValueError):
        SvgArtwork.from_bytes(SVG.replace(b'M20 30 L100 30 L100 60 L20 60 Z', path.encode()))


def test_invalid_svg_transform_is_rejected():
    with pytest.raises(ValueError, match="transform"):
        SvgArtwork.from_bytes(SVG.replace(b'<path ', b'<path transform="matrix3d(1 0 0)" '))


def test_vectors_gui_alignment_undo_job_and_export_do_not_paint_pixels(tmp_path, monkeypatch):
    from test_app_layers import opened, cleanup
    app, window = opened(tmp_path)
    try:
        before = window.doc.samples.copy()
        artwork = SvgArtwork.from_bytes(SVG)
        window.install_svg(artwork)
        assert window.tabs.currentWidget() is window.vectors_panel
        assert window.view.svg_item is not None and not window.view.drawing_enabled
        panel = window.vectors_panel
        panel.mark_saved()
        panel.fields["x_mm"].setValue(8)
        assert panel.dirty and window.undo_action.isEnabled()
        window.undo()
        assert panel.placement.x_mm == 0 and not panel.dirty
        window.redo()
        assert panel.placement.x_mm == 8
        panel.fields["width_mm"].setValue(75)
        assert panel.placement.height_mm == 37.5
        panel.visible.setChecked(False)
        assert not window.view.svg_item.isVisible()
        np.testing.assert_array_equal(window.doc.samples, before)
        assert not window.edits.dirty
        path = tmp_path / "SYNTHETIC-gui.tifview.json"
        monkeypatch.setattr("tifview.app.QFileDialog.getSaveFileName", lambda *args: (str(path), ""))
        window.choose_save_job()
        assert not panel.dirty
        artwork2, state = load_job(path, window.doc)
        assert artwork2 == artwork and state == panel.placement
        with pytest.raises(ValueError, match="source"):
            window.save_vector_file(window.doc.path, b"do not overwrite")
        window.remove_svg()
        assert window.view.svg_item is None
        window.install_svg(artwork2, state, dirty=False)
        window.copy_saved(str(window.doc.path))
        panel.fields["x_mm"].setValue(9)
        window.copy_saved(str(window.doc.path))
        assert panel.dirty and window.isWindowModified()  # TIFF save does not save alignment.
    finally:
        window.vectors_panel.mark_saved()
        cleanup(app, window)


def test_svg_viewport_fractional_size_stays_exact_during_position_edits():
    from tifview.vectorpanel import VectorsPanel
    app = QApplication.instance() or QApplication([])
    artwork = SvgArtwork.from_bytes(SVG)
    panel = VectorsPanel()
    state = Placement(width_mm=50.123456789, height_mm=25.0617283945)
    panel.set_artwork(artwork, state)
    panel.fields["x_mm"].setValue(12)
    assert panel.placement.width_mm == state.width_mm
    assert panel.placement.height_mm == state.height_mm


def svg_doc(tmp_path, dpi=(254, 508), orientation=1):
    path = tmp_path / "SYNTHETIC-physical-page.tif"
    tifffile.imwrite(path, np.zeros((1000, 1000, 3), np.uint8), photometric="rgb", metadata=None,
                     resolution=dpi, extratags=[(274, 3, 1, orientation, False)])
    return load_image(path)


def test_alignment_job_reopens_exactly_and_rejects_wrong_image(tmp_path):
    artwork = SvgArtwork.from_bytes(SVG)
    doc = svg_doc(tmp_path)
    placement = Placement(4.125, -2, 50, 25, 90, False)
    path = tmp_path / "SYNTHETIC.tifview.json"
    path.write_text(json.dumps(job_data(doc, artwork, placement)), encoding="utf-8")
    loaded, state = load_job(path, doc)
    assert loaded == artwork and state == placement
    with doc.path.open("ab") as file:
        file.write(b"changed source")
    with pytest.raises(ValueError, match="different image"):
        load_job(path, doc)


def test_svg_export_retains_paths_and_page_size_with_anisotropic_dpi_and_orientation(tmp_path):
    doc = svg_doc(tmp_path, orientation=6)
    assert doc.metadata["dpi"] == (508, 254)
    svg = export_svg(doc, SvgArtwork.from_bytes(SVG), Placement(5, 8, 50, 25, 90, False))
    root = ET.fromstring(svg)
    assert root.get("width") == "50mm" and root.get("height") == "100mm"
    group = root.find(f"{{{SVG_NS}}}g")
    assert group.get("transform") == "translate(5 8) rotate(90)"
    path = root.find(f".//{{{SVG_NS}}}path")
    assert path.get("d") == "M20 30 L100 30 L100 60 L20 60 Z"
    assert not root.findall(f".//{{{SVG_NS}}}image")


@pytest.mark.parametrize("angle", [0, 90, -30])
@pytest.mark.parametrize("dpi", [(254, 254), (254, 508)])
@pytest.mark.parametrize("raw", [SVG, SVG.replace(b'100 50', b'100 100'),
                                 SVG.replace(b'100 50', b'100 100').replace(b'<svg ', b'<svg preserveAspectRatio="none" '),
                                 ], ids=["normal", "meet", "stretch"])
def test_svg_item_scene_position_matches_export_pixels(tmp_path, angle, dpi, raw):
    app = QApplication.instance() or QApplication([])
    doc = svg_doc(tmp_path, dpi=dpi)
    artwork = SvgArtwork.from_bytes(raw)
    state = Placement(8, 12, 50, 25, angle)
    item = SvgItem(artwork)
    item.place(state, doc.metadata["dpi"])
    image = QImage(1000, 1000, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    painter.setTransform(item.transform())
    item.paint(painter, None)
    painter.end()
    exported = QImage(1000, 1000, QImage.Format.Format_RGB32)
    exported.fill(Qt.GlobalColor.white)
    renderer = QSvgRenderer(QByteArray(export_svg(doc, artwork, state)))
    painter = QPainter(exported)
    renderer.render(painter, QRectF(0, 0, 1000, 1000))
    painter.end()
    assert image == exported


@pytest.mark.parametrize("length", ["25.4mm", "2.54cm", "1in", "72pt", "6pc", "96px"])
@pytest.mark.parametrize("inline", [False, True])
def test_absolute_shape_and_stroke_lengths_match_svg_user_units(length, inline):
    app = QApplication.instance() or QApplication([])
    paint = f'style="fill:none;stroke:red;stroke-width:{length};stroke-dasharray:{length},48px;stroke-dashoffset:-{length}"' if inline else f'fill="none" stroke="red" stroke-width="{length}" stroke-dasharray="{length},48px" stroke-dashoffset="-{length}"'
    raw = f'<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="100mm" viewBox="0 0 400 400"><rect x="{length}" y="{length}" width="{length}" height="{length}" {paint}/></svg>'.encode()
    artwork = SvgArtwork.from_bytes(raw)
    root = ET.fromstring(artwork.raw)
    rect = root.find(f"{{{SVG_NS}}}rect")
    assert all(rect.get(key) == "96" for key in ("x", "y", "width", "height"))
    if inline:
        assert "stroke-width:96" in rect.get("style")
        assert "stroke-dasharray:96 48" in rect.get("style")
        assert "stroke-dashoffset:-96" in rect.get("style")
    else:
        assert rect.get("stroke-width") == "96"
        assert rect.get("stroke-dasharray") == "96 48"
        assert rect.get("stroke-dashoffset") == "-96"
    assert artwork.geometry_bounds == pytest.approx((48, 48, 192, 192))
    assert SvgArtwork.from_bytes(artwork.raw).raw == artwork.raw


def test_mirrored_negative_viewbox_outline_uses_absolute_stroke_for_canvas_and_export(tmp_path):
    from tifview.svg import canvas_fit
    # Independent geometry oracle, with no customer artwork or sample pixels.
    path = tmp_path / "SYNTHETIC-physical-stroke.tif"
    tifffile.imwrite(path, np.zeros((1663, 4260), np.uint8), photometric="minisblack",
                     metadata=None, resolution=(720, 720))
    doc = load_image(path)
    raw = b'<svg xmlns="http://www.w3.org/2000/svg" width="151mm" height="59.5mm" viewBox="-251 60 151 59.5"><path transform="matrix(-1,0,0,1,0,0)" style="stroke:green;stroke-width:0.05mm;fill:none" d="M251 60H100V119.5H251Z"/></svg>'
    artwork = SvgArtwork.from_bytes(raw)
    half = .05 * 96 / 25.4 / 2
    assert artwork.geometry_bounds == pytest.approx((-251-half, 60-half, 151+2*half, 59.5+2*half))
    assert canvas_fit(doc, artwork, Placement(width_mm=151, height_mm=59.5)) == (4286, 1693, 3, 3)
    output = export_svg(doc, artwork, Placement(width_mm=151, height_mm=59.5))
    exported_path = ET.fromstring(output).find(f".//{{{SVG_NS}}}path")
    assert exported_path.get("d") == "M251 60H100V119.5H251Z"
    assert exported_path.get("transform") == "matrix(-1,0,0,1,0,0)"
    assert "stroke-width:0.188976377952756" in exported_path.get("style")


@pytest.mark.parametrize("length", ["10%", "2em", "1e100mm", "calc(1mm + 1px)"])
def test_unresolved_svg_lengths_are_rejected_before_incorrect_canvas_bounds(length):
    raw = f'<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="10mm"><rect width="10" height="10" stroke="red" stroke-width="{length}"/></svg>'.encode()
    with pytest.raises(ValueError, match="lengths|range"):
        SvgArtwork.from_bytes(raw)



def test_inherited_absolute_svg_paint_lengths_skip_opaque_metadata():
    app = QApplication.instance() or QApplication([])
    raw = b'<svg xmlns="http://www.w3.org/2000/svg" width="20mm" height="20mm" viewBox="0 0 200 200" style="stroke:red;stroke-width:1mm;fill:none"><metadata><private stroke-width="10%">unchanged</private></metadata><g><rect x="50" y="50" width="100" height="100"/></g></svg>'
    artwork = SvgArtwork.from_bytes(raw)
    half = 96 / 25.4 / 2
    assert artwork.geometry_bounds == pytest.approx((50-half, 50-half, 100+2*half, 100+2*half))
    root = ET.fromstring(artwork.raw)
    assert root.find(f".//{{{SVG_NS}}}private").get("stroke-width") == "10%"
