"""Native TIFF composites and separate SVG pixel layers survive save and history."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from dataclasses import replace

import numpy as np
import pytest
import tifffile
from psdtags import TiffImageSourceData
from PySide6.QtWidgets import QApplication, QDialogButtonBox

from tifview.canvasdialog import CanvasSizeDialog
from tifview.editing import EditSession
from tifview.layers import LayerStack
from tifview.reader import load_image
from tifview.svg import SvgArtwork, Placement
from tifview.writer import save_tiff_copy, SaveOptions
from tools.make_demo import photoshop_resources
from test_layers import encode_layers, raster
from test_app_layers import opened, drain, cleanup


SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="3mm" height="3mm" viewBox="0 0 30 30"><rect x="5" y="5" width="20" height="20" fill="red"/></svg>'


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def source(tmp_path, depth=8, mode="RGB", layers=True):
    base = {"RGB": 3, "CMYK": 4, "Gray": 1, "WhiteIsZero": 1}[mode]
    maximum = 2**depth - 1
    samples = np.random.default_rng(171).integers(0, maximum + 1, (80, 100, base + 1), dtype=f"uint{depth}")
    resources = photoshop_resources(["White Ink"], [2])
    tags = [(34377, 7, len(resources), resources, False)]
    if layers:
        native = samples[..., :base]
        stored = maximum - native if mode in ("CMYK", "WhiteIsZero") else native
        data = encode_layers([raster(stored, name="Original")], depth=depth)
        tags.append((37724, 7, len(data), data, False))
    path = tmp_path / "SYNTHETIC-svg-source.tif"
    tifffile.imwrite(path, samples, photometric={"RGB":"rgb", "CMYK":"separated", "Gray":"minisblack", "WhiteIsZero":"miniswhite"}[mode],
                     extrasamples=[0], metadata=None, resolution=(254,254), extratags=tags)
    return load_image(path)


@pytest.mark.parametrize("depth", [8, 16])
@pytest.mark.parametrize("mode", ["RGB", "CMYK", "Gray", "WhiteIsZero"])
@pytest.mark.parametrize("layers", [False, True])
def test_added_svg_layer_native_save_independent_decode_and_exact_history(tmp_path, app, depth, mode, layers):
    doc = source(tmp_path, depth, mode, layers)
    original_bytes = doc.path.read_bytes()
    e = EditSession(doc)
    artwork = SvgArtwork.from_bytes(SVG)
    e.add_svg_layer(artwork, Placement(1, 2, 3, 3), "Cut — 印刷")
    after = e.document.samples.copy()
    expected = {"RGB": [255,0,0], "CMYK": [0,255,255,0], "Gray": [76], "WhiteIsZero": [179]}[mode]
    np.testing.assert_array_equal(after[30,20,:doc.base_count], np.array(expected) * (257 if depth == 16 else 1))
    np.testing.assert_array_equal(after[0], doc.samples[0])
    np.testing.assert_array_equal(after[..., -1], doc.samples[..., -1])
    target = tmp_path / "added.tif"
    save_tiff_copy(doc, e.document, target)
    saved = load_image(target); saved_stack = LayerStack.from_document(saved)
    assert saved_stack.layers[-1].name == "Cut — 印刷" and len(saved_stack.layers) == 2
    np.testing.assert_array_equal(saved.samples, after)
    independent = TiffImageSourceData.fromtiff(target)
    assert len(independent.layers.layers) == 2
    assert independent.layers.layers[-1].channels[-1].channelid == -1
    e.undo(); np.testing.assert_array_equal(e.document.samples, doc.samples)
    assert e.document.generated_layer_data is None
    e.redo(); np.testing.assert_array_equal(e.document.samples, after)
    e.set_layer_visibility(1, False); np.testing.assert_array_equal(e.document.samples[..., :doc.base_count], doc.samples[..., :doc.base_count])
    e.undo(); np.testing.assert_array_equal(e.document.samples, after)
    e.move_layer(1, 1); np.testing.assert_array_equal(e.document.samples[..., :doc.base_count], doc.samples[..., :doc.base_count])
    e.undo(); np.testing.assert_array_equal(e.document.samples, after)
    e.expand_canvas(114, 90, 9, 3)
    save_tiff_copy(doc, e.document, tmp_path / "expanded.tif")
    np.testing.assert_array_equal(e.document.samples[3:83,9:109,:after.shape[-1]], after)
    e.undo(); np.testing.assert_array_equal(e.document.samples, after)
    e.redo(); save_tiff_copy(doc, e.document, tmp_path / "expanded-again.tif")
    assert doc.path.read_bytes() == original_bytes


def test_add_multiple_layers_mixed_spot_edits_and_canvas_then_reopen(tmp_path, app):
    doc = source(tmp_path); e = EditSession(doc)
    e.expand_canvas(120, 100, 10, 10)
    art = SvgArtwork.from_bytes(SVG)
    e.add_svg_layer(art, Placement(1, 1, 3, 3), "First")
    e.add_spot("Varnish")
    e.add_svg_layer(art, Placement(5, 4, 3, 3, visible=False), "Second")
    e.set_layer_visibility(2, True)
    e.move_layer(2, 1)
    target = tmp_path / "combined.tif"; save_tiff_copy(doc, e.document, target)
    saved = load_image(target); stack = LayerStack.from_document(saved)
    assert [stack.layers[i].name for i in stack.default_order] == ["First", "Second", "Original"]
    e2 = EditSession(saved); e2.attach_layers(stack); e2.set_layer_visibility(0, False)
    save_tiff_copy(saved, e2.document, tmp_path / "reopened-hidden.tif")
    while e.can_undo: e.undo()
    np.testing.assert_array_equal(e.document.samples, doc.samples)
    while e.can_redo: e.redo()
    np.testing.assert_array_equal(e.document.samples, saved.samples)


def test_added_layer_cannot_bypass_native_or_original_record_checks(tmp_path, app):
    doc = source(tmp_path); e = EditSession(doc)
    art = SvgArtwork.from_bytes(SVG)
    with pytest.raises(ValueError, match="outside"):
        e.add_svg_layer(art, Placement(50,0,3,3), "Outside")
    assert not e.dirty
    e.add_svg_layer(art, Placement(width_mm=3,height_mm=3), "Added")
    changed = bytearray(e.document.layer_stack.data)
    first = e.document.layer_stack.layers[0]
    changed[first._flags_offset] ^= 2
    forged = replace(e.document, generated_layer_data=bytes(changed), layer_stack=LayerStack(bytes(changed), e.document))
    with pytest.raises(ValueError, match="original Photoshop layer record"):
        save_tiff_copy(doc, forged, tmp_path / "bad.tif")
    e.apply(0, (0,0,2,2), np.full((2,2),255,np.uint8), 0)
    with pytest.raises(ValueError, match="painted"):
        e.add_svg_layer(art, Placement(width_mm=3,height_mm=3), "Would discard paint")


@pytest.mark.parametrize("depth", [8,16])
@pytest.mark.parametrize("associated", [False,True])
@pytest.mark.parametrize("layers", [False,True])
def test_svg_layer_over_existing_transparency_keeps_association_and_spots(tmp_path, app, depth, associated, layers):
    maximum = 2**depth - 1
    straight = np.full((60,80,3), maximum // 3, dtype=f"uint{depth}")
    alpha = np.full((60,80), maximum // 2, dtype=f"uint{depth}")
    process = ((straight.astype(np.uint32) * alpha[...,None] + maximum//2) // maximum).astype(straight.dtype) if associated else straight
    spot = np.arange(60*80, dtype=np.uint16).reshape(60,80).astype(straight.dtype)
    samples = np.dstack([process,spot,alpha])
    resources = photoshop_resources(["White Ink"],[2])
    tags = [(34377,7,len(resources),resources,False)]
    if layers:
        raw = encode_layers([raster(straight,alpha)],depth=depth)
        tags.append((37724,7,len(raw),raw,False))
    p = tmp_path / 'SYNTHETIC-alpha.tif'
    tifffile.imwrite(p,samples,photometric='rgb',extrasamples=[0,1 if associated else 2],resolution=(254,254),metadata=None,extratags=tags)
    doc = load_image(p); e = EditSession(doc)
    e.add_svg_layer(SvgArtwork.from_bytes(SVG),Placement(width_mm=3,height_mm=3),"SVG")
    assert e.document.channels[-1].associated == associated
    np.testing.assert_array_equal(e.document.samples[40:],samples[40:])
    np.testing.assert_array_equal(e.document.samples[...,3],spot)
    save_tiff_copy(doc,e.document,tmp_path/'alpha-added.tif')
    e.undo();np.testing.assert_array_equal(e.document.samples,samples)


def test_canvas_edges_use_each_axis_print_resolution(tmp_path, app):
    doc = source(tmp_path);doc.metadata['dpi'] = (254,508)
    dialog = CanvasSizeDialog(doc);dialog.units.setCurrentIndex(1);dialog.mode.setCurrentIndex(1)
    dialog.fields['left'].setValue(1);dialog.fields['bottom'].setValue(2)
    assert dialog.canvas_geometry() == (110,120,10,0)
    dialog.close()


@pytest.mark.parametrize("anchor", range(9))
def test_manual_canvas_size_anchor_and_edge_controls(tmp_path, app, anchor):
    doc = source(tmp_path)
    dialog = CanvasSizeDialog(doc)
    assert dialog.background.currentText() == "Transparent"
    dialog.fields["width"].setValue(120); dialog.fields["height"].setValue(110)
    dialog.anchor.button(anchor).click()
    row, column = divmod(anchor, 3)
    assert dialog.canvas_geometry() == (120,110,10*column,15*row)
    dialog.units.setCurrentIndex(1)
    assert dialog.canvas_geometry() == (120,110,10*column,15*row)
    dialog.mode.setCurrentIndex(1)
    dialog.fields["left"].setValue(1); dialog.fields["bottom"].setValue(2)
    assert dialog.canvas_geometry() == (110,100,10,0)
    dialog.units.setCurrentIndex(0)
    assert dialog.canvas_geometry() == (110,100,10,0)
    dialog.mode.setCurrentIndex(0); dialog.fields["width"].setValue(99)
    assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    dialog.close()


def test_gui_add_and_save_offer_include_pending_svg(tmp_path, monkeypatch):
    import tifview.app as gui
    app, window = opened(tmp_path)
    try:
        art = SvgArtwork.from_bytes(SVG.replace(b'3mm', b'.5mm'))
        window.install_svg(art)
        monkeypatch.setattr(gui.SvgSaveDialog, "exec", lambda self: 0)
        window.choose_save(); assert window.vectors_panel.artwork is art
        monkeypatch.setattr(gui.SvgSaveDialog, "exec", lambda self: 1)
        monkeypatch.setattr(gui.TiffSaveDialog, "exec", lambda self: 1)
        target = tmp_path / "gui-added.tif"
        monkeypatch.setattr(gui.QFileDialog, "getSaveFileName", lambda *a: (str(target), "TIFF"))
        window.choose_save()
        from test_app_layers import pump, idle
        pump(app, lambda: target.exists() and idle(window) and not window._save_after_svg, seconds=12)
        assert window.vectors_panel.artwork is None
        assert window.tabs.currentWidget() is window.layers_panel
        saved = load_image(target)
        assert len(LayerStack.from_document(saved).layers) == 3
        window.undo(); drain(app,window)
        assert len(window.doc.layer_stack.layers) == 2
        window.redo(); drain(app,window)
        assert len(window.doc.layer_stack.layers) == 3
    finally:
        window._save_after_svg = False
        window.vectors_panel.mark_saved(); cleanup(app,window)
