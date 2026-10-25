"""Unit and GUI tests for drawing onto a new pixel layer."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import tifffile
from PIL import ImageCms
from psdtags import TiffImageSourceData
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

import tifview.app as gui
from tifview.drawlayer import add_drawn_layer, convert_color, draw_layer_reason, draw_pixels
from tifview.editing import EditSession
from tifview.layerpanel import LAYER_ID_ROLE
from tifview.layers import LayerStack
from tifview.reader import load_image
from tifview.writer import SaveOptions, layer_preservation_reason, save_tiff_copy
from tools.make_demo import photoshop_resources
from test_layers import encode_layers, raster
from test_app_layers import cleanup, displayed_pixel, drain, opened, pump


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def make_doc(tmp_path, depth=8, mode="RGB", layers=True, width=80, height=60, orientation=1):
    base = {"RGB": 3, "CMYK": 4, "Gray": 1, "WhiteIsZero": 1}[mode]
    maximum = 2**depth - 1
    samples = np.random.default_rng(42).integers(0, maximum + 1, (height, width, base), dtype=f"uint{depth}")
    tags = []
    if layers:
        stored = maximum - samples if mode in ("CMYK", "WhiteIsZero") else samples
        data = encode_layers([raster(stored, name="Background")], depth=depth)
        tags.append((37724, 7, len(data), data, False))
    path = tmp_path / f"source_{mode}_{depth}_{layers}.tif"
    photo = {"RGB": "rgb", "CMYK": "separated", "Gray": "minisblack", "WhiteIsZero": "miniswhite"}[mode]
    data_to_write = samples[..., 0] if base == 1 else samples
    tifffile.imwrite(path, data_to_write, photometric=photo, metadata=None,
                     resolution=(72, 72), extratags=tags)
    doc = load_image(path)
    if orientation != 1:
        doc = replace(doc, orientation=orientation)
    return doc


def test_draw_layer_reason_and_rejections(tmp_path):
    doc = make_doc(tmp_path, depth=8, mode="RGB", layers=False)
    assert draw_layer_reason(doc) is None

    bad_orient = replace(doc, orientation=6)
    assert "orientation 1" in draw_layer_reason(bad_orient)

    bad_canvas = replace(doc, metadata={"canvas_layer_reason": "Canvas expanded"})
    assert "Canvas expanded" in draw_layer_reason(bad_canvas)

    bad_mode = replace(doc, color_mode="Palette")
    assert "supports unsigned 8/16-bit" in draw_layer_reason(bad_mode)

    # Empty shape rejection
    with pytest.raises(ValueError, match="no visible pixels"):
        draw_pixels(doc, "Box", (10., 10.), (10., 10.), (255, 0, 0), width=1, filled=False)

    # Empty text rejection
    with pytest.raises(ValueError, match="no visible pixels"):
        draw_pixels(doc, "Text", (10., 10.), (10., 10.), (255, 0, 0), text="   ")

    # Invalid layer name
    with pytest.raises(ValueError, match="layer name"):
        add_drawn_layer(doc, doc, "Box", (10., 10.), (30., 30.), name="")

    with pytest.raises(ValueError, match="layer name"):
        add_drawn_layer(doc, doc, "Box", (10., 10.), (30., 30.), name="Bad\0Name")


def test_convert_color(tmp_path):
    doc_rgb8 = make_doc(tmp_path, depth=8, mode="RGB")
    c = convert_color(doc_rgb8, QColor(255, 0, 0))
    np.testing.assert_array_equal(c, [255, 0, 0])

    c_hex = convert_color(doc_rgb8, "#00ff00")
    np.testing.assert_array_equal(c_hex, [0, 255, 0])

    doc_rgb16 = make_doc(tmp_path, depth=16, mode="RGB")
    c16 = convert_color(doc_rgb16, (255, 0, 0))
    np.testing.assert_array_equal(c16, [65535, 0, 0])

    doc_cmyk8 = make_doc(tmp_path, depth=8, mode="CMYK")
    c_cmyk = convert_color(doc_cmyk8, (10, 20, 30, 40))
    np.testing.assert_array_equal(c_cmyk, [10, 20, 30, 40])

    doc_gray8 = make_doc(tmp_path, depth=8, mode="Gray")
    c_gray = convert_color(doc_gray8, 128)
    np.testing.assert_array_equal(c_gray, [128])

    doc_w0_8 = make_doc(tmp_path, depth=8, mode="WhiteIsZero")
    c_w0 = convert_color(doc_w0_8, 255)
    np.testing.assert_array_equal(c_w0, [0])


def test_rgb_drawing_respects_embedded_icc_profile_and_rejects_invalid_icc(tmp_path):
    raw_icc = bytearray(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
    idx = raw_icc.find(b"para")
    raw_icc[idx + 8:idx + 16] = b"\x00\x00\x00\x00\x00\x01\x00\x00"
    linear_rgb_icc = bytes(raw_icc)

    doc_rgb8 = replace(make_doc(tmp_path, depth=8, mode="RGB"), icc_profile=linear_rgb_icc)
    np.testing.assert_array_equal(convert_color(doc_rgb8, (128, 128, 128)), [55, 55, 55])
    np.testing.assert_array_equal(convert_color(doc_rgb8, QColor(128, 128, 128)), [55, 55, 55])

    session = EditSession(doc_rgb8)
    session.add_drawn_layer("Box", (5., 5.), (25., 25.), color=(128, 128, 128), filled=True, name="Linear Box")
    np.testing.assert_array_equal(session.document.samples[15, 15, :3], [55, 55, 55])

    doc_rgb16 = replace(make_doc(tmp_path, depth=16, mode="RGB"), icc_profile=linear_rgb_icc)
    np.testing.assert_array_equal(convert_color(doc_rgb16, (128, 128, 128)), [55 * 257, 55 * 257, 55 * 257])

    # Corrupt or incompatible ICC profile must raise ValueError instead of silently falling back
    bad_icc_doc = replace(doc_rgb8, icc_profile=b"corrupt-icc-data")
    with pytest.raises(ValueError, match="Cannot convert drawing colour into the image's ICC profile"):
        convert_color(bad_icc_doc, (128, 128, 128))

    bad_session = EditSession(bad_icc_doc)
    with pytest.raises(ValueError, match="Cannot convert drawing colour into the image's ICC profile"):
        bad_session.add_drawn_layer("Box", (5., 5.), (25., 25.), color=(128, 128, 128), filled=True, name="Fail")
    assert not bad_session.dirty


@pytest.mark.parametrize("depth", [8, 16])
@pytest.mark.parametrize("mode", ["RGB", "CMYK", "Gray", "WhiteIsZero"])
@pytest.mark.parametrize("layers", [False, True])
@pytest.mark.parametrize("tool", ["Box", "Ellipse", "Line", "Text"])
def test_add_drawn_layer_tools_modes_and_depths(tmp_path, app, depth, mode, layers, tool):
    doc = make_doc(tmp_path, depth=depth, mode=mode, layers=layers)
    session = EditSession(doc)
    text_arg = "Test" if tool == "Text" else ""
    color = (255, 0, 0) if mode == "RGB" else (0, 255, 255, 0) if mode == "CMYK" else 0
    session.add_drawn_layer(tool, (10., 10.), (40., 30.), color=color,
                            width=3, filled=True, text=text_arg, name=f"Drawn {tool}")

    assert session.dirty
    assert session.document.generated_layer_data is not None
    stack = session.document.layer_stack
    assert stack is not None
    assert stack.layers[-1].name == f"Drawn {tool}"
    expected_count = 2 if not layers else 2
    assert len(stack.layers) == expected_count

    # Check undo and redo
    after_samples = session.document.samples.copy()
    session.undo()
    np.testing.assert_array_equal(session.document.samples, doc.samples)
    assert session.document.generated_layer_data is None

    session.redo()
    np.testing.assert_array_equal(session.document.samples, after_samples)


def test_drawn_layer_visibility_reorder_and_save(tmp_path, app):
    doc = make_doc(tmp_path, depth=8, mode="RGB", layers=True)
    session = EditSession(doc)
    red = (255, 0, 0)
    session.add_drawn_layer("Box", (5., 5.), (25., 25.), color=red, filled=True, name="Red Box")

    # Center of drawn box should now be red
    np.testing.assert_array_equal(session.document.samples[15, 15, :3], [255, 0, 0])
    painted_samples = session.document.samples.copy()

    # Toggle visibility: hide layer 1
    session.set_layer_visibility(1, False)
    # Merged samples should revert to base image
    np.testing.assert_array_equal(session.document.samples[15, 15, :3], doc.samples[15, 15, :3])

    # Undo hiding
    session.undo()
    np.testing.assert_array_equal(session.document.samples, painted_samples)

    # Add second layer: Blue Box
    blue = (0, 0, 255)
    session.add_drawn_layer("Box", (5., 5.), (25., 25.), color=blue, filled=True, name="Blue Box")
    # Top layer is blue
    np.testing.assert_array_equal(session.document.samples[15, 15, :3], [0, 0, 255])
    assert len(session.document.layer_stack.layers) == 3

    # Reorder layer 2 (Blue Box) down one position
    session.move_layer(2, 1)
    # Now Red Box (layer 1) is above Blue Box, so samples at (15, 15) should be red
    np.testing.assert_array_equal(session.document.samples[15, 15, :3], [255, 0, 0])

    # Save TIFF copy preserving layers
    target = tmp_path / "saved_drawn_layers.tif"
    save_tiff_copy(doc, session.document, target, SaveOptions(keep_layers=True))

    # Read back and verify
    saved = load_image(target)
    assert saved.metadata.get("has_photoshop_layers")
    saved_stack = LayerStack.from_document(saved)
    assert len(saved_stack.layers) == 3
    layer_names = [layer.name for layer in saved_stack.layers]
    assert "Red Box" in layer_names
    assert "Blue Box" in layer_names
    assert "Background" in layer_names

    # Independent decode with psdtags
    psd_source = TiffImageSourceData.fromtiff(target)
    assert len(psd_source.layers.layers) == 3


def test_gui_layer_drawing(tmp_path, monkeypatch):
    app, window = opened(tmp_path)
    try:
        # Switch to Layers tab
        window.tabs.setCurrentIndex(1)
        drain(app, window)
        assert window.edit_toolbar.isEnabled()
        assert window.view.drawing_enabled
        assert window.draw_color_action.isVisible()
        assert not window.shade_action.isVisible()

        # Select an individual layer first (row 2 = "Red background", RGB 210, 20, 30)
        tree = window.layers_panel.tree
        tree.setCurrentItem(tree.topLevelItem(2))
        drain(app, window)
        assert window.layers_panel.selected_index() == 0
        assert displayed_pixel(window, 2, 2)[:3] == (210, 20, 30)

        # Select Box tool
        window.tool_combo.setCurrentText("Box")
        window.fill.setChecked(True)
        window.draw_color = (0, 255, 0)
        window.update_draw_color_button()

        # Mock layer name input dialog
        monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Green Box", True))

        # Apply drawing on canvas
        window.apply_drawing("Box", (1., 1.), (5., 5.))
        drain(app, window)

        # Check that the layer was added and the viewer switched back to composite ("Layer stack")
        # so the newly drawn green pixels are immediately visible on the canvas
        assert window.edits.dirty
        assert window.layers_panel.selected_index() is None
        assert displayed_pixel(window, 2, 2)[:3] == (0, 255, 0)
        stack = window.doc.layer_stack
        assert stack is not None
        assert stack.layers[-1].name == "Green Box"

        # Check layers panel
        assert any("Green Box" in tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount()))

        # Verify failed ICC profile in GUI shows clear warning without adding layer
        warnings = []
        monkeypatch.setattr(QMessageBox, "warning", lambda *a: warnings.append(a[2]))
        window.doc = replace(window.doc, icc_profile=b"invalid-icc")
        window.apply_drawing("Box", (1., 1.), (5., 5.))
        assert any("ICC profile" in w for w in warnings)
        window.doc = window.edits.document

        # Undo via window
        window.undo()
        drain(app, window)
        assert len(window.doc.layer_stack.layers) == 2

        # Redo via window
        window.redo()
        drain(app, window)
        assert len(window.doc.layer_stack.layers) == 3
        assert window.doc.layer_stack.layers[-1].name == "Green Box"

        # Save TIFF copy
        target = tmp_path / "gui_drawn_layer.tif"
        save_tiff_copy(window.edits.original, window.doc, target)
        saved = load_image(target)
        saved_stack = LayerStack.from_document(saved)
        assert len(saved_stack.layers) == 3
        assert saved_stack.layers[-1].name == "Green Box"
    finally:
        cleanup(app, window)
