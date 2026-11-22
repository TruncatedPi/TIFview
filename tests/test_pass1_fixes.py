"""Regression tests for Pass 1 saving reliability, layer/canvas fixes, and SVG import."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest
import tifffile
from psdtags import TiffImageSourceData
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QLabel

import tifview.app as gui
from tifview.editing import EditSession
from tifview.layerediting import baseline_matches, default_state
from tifview.layers import LayerStack
from tifview.reader import load_image
from tifview.svg import Placement, SVG_NS, SvgArtwork
from tifview.writer import SaveOptions, save_tiff_copy
from test_app_layers import cleanup, drain, idle, opened, pump
from test_layers import encode_layers, raster
from test_vectors import shape_layer


SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="1mm" height="1mm" viewBox="0 0 10 10">'
    b'<rect x="1" y="1" width="8" height="8" fill="none" stroke="#ff0000" stroke-width="1"/></svg>'
)


def test_save_shortcut_queued_save_during_preview_verify_false_and_mtime_change(tmp_path, monkeypatch):
    app, window = opened(tmp_path)
    try:
        # 1. Ctrl+S is bound alongside Ctrl+Shift+S
        assert QKeySequence(QKeySequence.StandardKey.Save) in window.save_action.shortcuts()

        # 2. Queued save during an active Previewer interrupts preview and completes saving
        monkeypatch.setattr(gui, "ASYNC_PREVIEW_PIXELS", 0)
        started, release = threading.Event(), threading.Event()
        real_render = gui.render

        def controlled_render(*args, **kwargs):
            started.set()
            assert release.wait(5)
            return real_render(*args, **kwargs)

        monkeypatch.setattr(gui, "render", controlled_render)
        window.channels.topLevelItem(1).setCheckState(0, Qt.CheckState.Unchecked)
        pump(app, started.is_set)
        assert window.save_action.isEnabled()

        target_fast = tmp_path / "queued-fast.tif"
        window.save_copy(str(target_fast), SaveOptions(keep_layers=True, verify=False), overwrite=True)
        assert window._pending_save is not None
        release.set()
        pump(app, lambda: target_fast.exists() and idle(window) and window._pending_save is None, seconds=10)
        assert target_fast.exists()
        assert load_image(target_fast).width == window.doc.width

        # 3. Source mtime change rejects by default, succeeds with allow_source_change=True
        doc = window.edits.original
        with doc.path.open("ab") as handle:
            handle.write(b"")  # Bump mtime
        os.utime(doc.path, (doc.path.stat().st_atime + 5, doc.path.stat().st_mtime + 5))
        with pytest.raises(ValueError, match="changed after opening"):
            save_tiff_copy(doc, window.doc, tmp_path / "blocked.tif")
        saved_anyway = save_tiff_copy(
            doc, window.doc, tmp_path / "allowed.tif",
            SaveOptions(keep_layers=True, verify=True), allow_source_change=True,
        )
        assert saved_anyway.exists()
    finally:
        cleanup(app, window)


def test_opaque_cmyk_single_layer_with_soft_alpha_matches_and_expands(tmp_path):
    # Simulates Photoshop opaque CMYK TIFF whose Layer 0 has soft alpha (222..255) matted on white (0 CMYK)
    h, w = 20, 24
    layer_cmyk = np.full((h, w, 4), [200, 100, 50, 10], dtype=np.uint8)
    layer_alpha = np.full((h, w), 255, dtype=np.uint8)
    layer_alpha[:4, :6] = 230
    stored_layer_cmyk = 255 - layer_cmyk
    data = encode_layers([raster(stored_layer_cmyk, layer_alpha, name="Layer 0")])

    # Paper white in CMYK is 0 per channel: matte = (layer * alpha + 127) // 255
    merged_cmyk = ((layer_cmyk.astype(np.uint32) * layer_alpha[..., None] + 127) // 255).astype(np.uint8)
    path = tmp_path / "soft-alpha-opaque.tif"
    tifffile.imwrite(path, merged_cmyk, photometric="separated", metadata=None,
                     resolution=(254, 254), extratags=[(37724, 7, len(data), data, False)])

    doc = load_image(path)
    stack = LayerStack.from_document(doc)
    assert baseline_matches(doc, stack, default_state(stack))

    edits = EditSession(doc)
    edits.attach_layers(stack)
    # Hide/show works
    edits.set_layer_visibility(0, False)
    edits.set_layer_visibility(0, True)
    before_expand = edits.document.samples.copy()
    # Canvas expansion keeps layers + undo/redo + save
    edits.expand_canvas(w + 10, h + 10, 5, 5, transparent=True)
    assert edits.document.canvas.layers_preserved
    expanded_copy = edits.document.samples.copy()
    edits.undo()
    np.testing.assert_array_equal(edits.document.samples, before_expand)
    edits.redo()
    np.testing.assert_array_equal(edits.document.samples, expanded_copy)

    # SVG layer can be added and saved
    art = SvgArtwork.from_bytes(SVG, "cut.svg")
    edits.add_svg_layer(art, Placement(0, 0, 1, 1), "Cut")
    out = tmp_path / "soft-saved.tif"
    save_tiff_copy(doc, edits.document, out, SaveOptions(keep_layers=True))
    assert len(LayerStack.from_document(load_image(out)).layers) == 2


def test_visible_vector_shape_allows_antialiased_edge_diff_but_rejects_mismatch(tmp_path):
    background = np.full((24, 24, 3), 80, dtype=np.uint8)
    raw = encode_layers([raster(background), shape_layer(visible=True)])
    # Render baseline via LayerStack to get Qt's edge pixels, then perturb ONLY 1px edge by <= 25%
    probe_path = tmp_path / "probe.tif"
    tifffile.imwrite(probe_path, background, photometric="rgb", metadata=None,
                     resolution=(72, 72), extratags=[(37724, 7, len(raw), raw, False)])
    probe_doc = load_image(probe_path)
    stack = LayerStack.from_document(probe_doc)
    comp = stack.composite_samples(stack.default_order, stack.default_visible)
    merged_edge = comp[..., :3].copy()
    va = stack.decode_layer(1).alpha
    edge_coords = np.argwhere((va > 0) & (va < 255))
    if len(edge_coords):
        ey, ex = edge_coords[0]
    else:
        # Pick 1px transition next to alpha boundary
        pad = np.pad(va, 1, mode="edge")
        trans = np.argwhere((va != pad[:-2, 1:-1]) | (va != pad[2:, 1:-1]) | (va != pad[1:-1, :-2]) | (va != pad[1:-1, 2:]))
        ey, ex = trans[0]
    merged_edge[ey, ex, 0] = int(merged_edge[ey, ex, 0]) ^ 20  # Small AA difference <= 25%

    aa_path = tmp_path / "vector-aa.tif"
    tifffile.imwrite(aa_path, merged_edge, photometric="rgb", metadata=None,
                     resolution=(72, 72), extratags=[(37724, 7, len(raw), raw, False)])
    aa_doc = load_image(aa_path)
    aa_stack = LayerStack.from_document(aa_doc)
    assert baseline_matches(aa_doc, aa_stack, default_state(aa_stack))

    # Mismatch far inside or >25% must still be rejected
    bad_merged = merged_edge.copy()
    bad_merged[ey, ex] = [255 ^ int(bad_merged[ey, ex, 0]), 255 ^ int(bad_merged[ey, ex, 1]), 255 ^ int(bad_merged[ey, ex, 2])]
    bad_path = tmp_path / "vector-bad.tif"
    tifffile.imwrite(bad_path, bad_merged, photometric="rgb", metadata=None,
                     resolution=(72, 72), extratags=[(37724, 7, len(raw), raw, False)])
    bad_doc = load_image(bad_path)
    bad_stack = LayerStack.from_document(bad_doc)
    assert not baseline_matches(bad_doc, bad_stack, default_state(bad_stack))


def test_edit_session_does_not_double_count_raw_layer_bytes(tmp_path):
    h, w = 20, 20
    img = np.full((h, w, 3), 120, dtype=np.uint8)
    raw = encode_layers([raster(img, name="Base")])
    path = tmp_path / "large-stack-limit.tif"
    tifffile.imwrite(path, img, photometric="rgb", metadata=None,
                     resolution=(254, 254), extratags=[(37724, 7, len(raw), raw, False)])
    doc = load_image(path)
    stack = LayerStack.from_document(doc)
    # Tight budget: > 1x raw stack + delta, < 2x raw stack (old code double-counted before + after)
    tight_limit = int(len(raw) * 1.4) + 16384
    edits = EditSession(doc, history_limit=tight_limit)
    edits.attach_layers(stack)
    art = SvgArtwork.from_bytes(SVG, "cut.svg")
    edits.add_svg_layer(art, Placement(0, 0, 1, 1), "Added")
    assert edits.dirty
    assert len(edits.history) == 1
    assert edits.history[0].bytes <= tight_limit


def test_flatten_layers_and_flatten_and_add_svg_layer_roundtrip(tmp_path):
    pixels = np.full((24, 24, 3), 90, dtype=np.uint8)
    raw = encode_layers([raster(pixels, name="Base"), shape_layer()])
    path = tmp_path / "vector-canvas.tif"
    tifffile.imwrite(path, pixels, photometric="rgb", metadata=None,
                     resolution=(254, 254), extratags=[(37724, 7, len(raw), raw, False)])
    doc = load_image(path)
    edits = EditSession(doc)
    edits.attach_layers(LayerStack.from_document(doc))
    edits.expand_canvas(40, 40, 8, 8, transparent=True)
    assert edits.document.metadata.get("canvas_layer_reason")

    art = SvgArtwork.from_bytes(SVG, "overlay.svg")
    edits.flatten_and_add_svg_layer(art, Placement(1, 1, 1, 1), "Cut Layer")
    assert "canvas_layer_reason" not in edits.document.metadata
    assert edits.document.metadata.get("layers_flattened")
    assert edits.document.canvas.layers_preserved
    assert [l.name for l in edits.document.layer_stack.layers] == ["Merged image", "Cut Layer"]

    out = tmp_path / "flattened-svg.tif"
    save_tiff_copy(doc, edits.document, out, SaveOptions(keep_layers=True))
    saved = load_image(out)
    saved_stack = LayerStack.from_document(saved)
    assert [l.name for l in saved_stack.layers] == ["Merged image", "Cut Layer"]
    assert len(TiffImageSourceData.fromtiff(out).layers.layers) == 2

    # Atomic single undo restores pre-flatten state with canvas_layer_reason
    after_flatten = edits.document.samples.copy()
    edits.undo()
    assert edits.document.metadata.get("canvas_layer_reason")
    edits.redo()
    np.testing.assert_array_equal(edits.document.samples, after_flatten)


def test_gui_svg_save_dialog_flatten_and_add_svg_on_expanded_canvas(tmp_path, monkeypatch):
    app, window = opened(tmp_path)
    try:
        # Mark canvas_layer_reason as blocking layers and install an SVG overlay
        window.doc.metadata["canvas_layer_reason"] = "Canvas expansion cannot retain vector layers"
        art = SvgArtwork.from_bytes(SVG, "overlay.svg")
        window.install_svg(art, Placement(0, 0, .5, .5))
        dialog = gui.SvgSaveDialog(window.doc, reason="Canvas expansion cannot retain vector layers.")
        buttons = dialog.findChild(QDialogButtonBox).buttons()
        flatten_btn = next(b for b in buttons if b.text().startswith("Flatten old layers"))
        assert flatten_btn.isEnabled()
        dialog.close()

        monkeypatch.setattr(gui.SvgSaveDialog, "exec", lambda self: 3)
        monkeypatch.setattr(gui.TiffSaveDialog, "exec", lambda self: 1)
        target = tmp_path / "gui-flattened-svg.tif"
        monkeypatch.setattr(gui.QFileDialog, "getSaveFileName", lambda *args: (str(target), "TIFF"))
        window.choose_save()
        pump(app, lambda: target.exists() and idle(window) and not window._save_after_svg, seconds=10)
        assert [l.name for l in LayerStack.from_document(load_image(target)).layers] == ["Merged image", "overlay"]
    finally:
        window._save_after_svg = False
        window.vectors_panel.mark_saved()
        cleanup(app, window)


def test_unlayered_tiff_expand_canvas_then_add_svg_and_drawn_layers_saves_with_layers(tmp_path):
    app = QApplication.instance() or QApplication([])
    img = np.full((24, 24, 3), 100, dtype=np.uint8)
    path = tmp_path / "flat.tif"
    tifffile.imwrite(path, img, photometric="rgb", metadata=None, resolution=(254, 254))
    doc = load_image(path)
    assert not doc.metadata.get("has_photoshop_layers")

    edits = EditSession(doc)
    edits.expand_canvas(40, 40, 8, 8, transparent=True)
    art = SvgArtwork.from_bytes(SVG, "overlay.svg")
    edits.add_svg_layer(art, Placement(0, 0, 1, 1), "Cut SVG")
    edits.add_drawn_layer("Box", (10., 10.), (20., 20.), color=(0, 255, 0), filled=True, name="Box 1")
    assert edits.document.canvas.layers_preserved

    out1 = tmp_path / "flat-expanded-layers.tif"
    save_tiff_copy(doc, edits.document, out1, SaveOptions(keep_layers=True))
    saved_layers = LayerStack.from_document(load_image(out1)).layers
    assert [l.name for l in saved_layers] == ["Original image", "Cut SVG", "Box 1"]

    # Second canvas expansion also preserves all generated raster layers
    edits.expand_canvas(50, 50, 5, 5, transparent=True)
    assert edits.document.canvas.layers_preserved
    out2 = tmp_path / "flat-expanded-twice.tif"
    save_tiff_copy(doc, edits.document, out2, SaveOptions(keep_layers=True))
    assert [l.name for l in LayerStack.from_document(load_image(out2)).layers] == ["Original image", "Cut SVG", "Box 1"]


def test_svg_importer_accepts_inkscape_metadata_zoomandpan_and_safe_styles():
    app = QApplication.instance() or QApplication([])
    inkscape_svg = b"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg"
     xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd"
     xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
     xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
     width="50mm" height="30mm" viewBox="0 0 50 30" zoomAndPan="magnify" version="1.1">
  <sodipodi:namedview id="namedview1" pagecolor="#ffffff" bordercolor="#000000"/>
  <metadata><rdf:RDF/></metadata>
  <style>
    /* comment */
    .cut { stroke: #ff0000; stroke-width: 0.1mm; fill: none; }
    text.label { font-family: sans-serif; font-size: 3.2mm; }
  </style>
  <rect class="cut" x="5" y="5" width="40" height="20"/>
</svg>"""
    art = SvgArtwork.from_bytes(inkscape_svg, "inkscape.svg")
    root = ET.fromstring(art.raw)
    rect = root.find(f"{{{SVG_NS}}}rect")
    assert "class" not in rect.attrib
    assert "zoomAndPan" in root.attrib
    expected_w = format(0.1 * 96 / 25.4, ".15g")
    assert f"stroke-width:{expected_w}" in rect.get("style")

    # Unsafe stylesheet properties and @import still rejected
    for bad in (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="10mm"><style>@import url("x.css");</style><rect width="5" height="5"/></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="10mm"><style>.a{filter:url(#b)}</style><rect class="a" width="5" height="5"/></svg>',
    ):
        with pytest.raises(ValueError):
            SvgArtwork.from_bytes(bad)
