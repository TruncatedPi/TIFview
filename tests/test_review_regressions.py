"""User-reported review cases across canvas, SVG layers and alignment history."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from dataclasses import replace
import struct

import numpy as np
import pytest
import tifffile
from psdtags import PsdFormat, PsdKey, PsdUnknown
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QLabel

import tifview.app as gui
from tifview.canvas import _reference_points
from tifview.editing import EditSession
from tifview.layers import LayerStack
from tifview.reader import load_image
from tifview.render import render
from tifview.svg import MAX_SVG_BYTES, Placement, SvgArtwork, canvas_fit, load_job
from tifview.writer import SaveOptions, layer_preservation_reason, save_tiff_copy
from test_app_layers import opened, drain, cleanup, make_source
from test_layers import encode_layers, raster
from test_svg_layers import SVG, source


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def no_unexpected_modal(monkeypatch):
    errors = []
    monkeypatch.setattr(gui.QMessageBox, "warning", lambda *args: errors.append(args[1:]))
    yield
    assert not errors


@pytest.mark.parametrize("x,y", [(-1, 0), (1, 0), (0, -1), (0, 1)])
def test_centred_svg_canvas_preserves_relative_alignment_and_fits_all_edges(tmp_path, monkeypatch, x, y):
    app, window = opened(tmp_path)
    try:
        artwork = SvgArtwork.from_bytes(SVG)
        placement = Placement(x, y, .5, .5)
        original = window.doc.samples.copy()
        window.install_svg(artwork, placement)
        width, height, left, top = canvas_fit(window.doc, artwork, placement)
        left, top = max(left, width - 16 - left), max(top, height - 12 - top)
        def centre(dialog):
            dialog.anchor.setCurrentIndex(1)
            assert dialog.canvas_geometry() == (16 + 2 * left, 12 + 2 * top, left, top)
            assert f"{16 + 2 * left:,} × {12 + 2 * top:,}" in dialog.summary.text()
            return 1
        monkeypatch.setattr(gui.CanvasFitDialog, "exec", centre)
        window.fit_svg_canvas(); drain(app, window)
        np.testing.assert_array_equal(window.doc.samples[top:top + 12, left:left + 16, :4], original)
        dx, dy = window.doc.metadata["dpi"]
        assert window.vectors_panel.placement.x_mm == pytest.approx(x + left * 25.4 / dx)
        assert window.vectors_panel.placement.y_mm == pytest.approx(y + top * 25.4 / dy)
        assert canvas_fit(window.doc, artwork, window.vectors_panel.placement) == (window.doc.width, window.doc.height, 0, 0)
        window.undo(); drain(app, window)
        assert window.doc.width == 16
        assert window.vectors_panel.placement.x_mm == pytest.approx(x)
        assert window.vectors_panel.placement.y_mm == pytest.approx(y)
        window.redo(); drain(app, window)
        assert window.doc.width == 16 + 2 * left
    finally:
        window.vectors_panel.mark_saved(); cleanup(app, window)


def test_generated_layers_white_padding_requires_explicit_merged_save_and_survives_undo(tmp_path, app):
    doc = source(tmp_path, layers=False)
    edits = EditSession(doc)
    edits.add_svg_layer(SvgArtwork.from_bytes(SVG), Placement(width_mm=3, height_mm=3), "Outline")
    layered = edits.document.samples.copy()
    edits.expand_canvas(110, 90, 2, 3, transparent=False)
    for iteration in range(2):
        reason = layer_preservation_reason(doc, edits.document)
        assert "White canvas" in reason
        dialog = gui.TiffSaveDialog(doc, edits.document)
        assert not dialog.layers.isEnabled() and not dialog.layers.isChecked()
        assert any(reason in label.text() for label in dialog.findChildren(QLabel))
        dialog.close()
        with pytest.raises(ValueError, match="layers unchecked"):
            save_tiff_copy(doc, edits.document, tmp_path / "must-not-flatten.tif")
        edits.expand_canvas(edits.document.width + 2, edits.document.height + 2)
    for _ in range(3):
        edits.undo()
    np.testing.assert_array_equal(edits.document.samples, layered)
    target = tmp_path / "layers-restored.tif"
    save_tiff_copy(doc, edits.document, target)
    assert len(LayerStack.from_document(load_image(target)).layers) == 2
    edits.redo()
    merged = tmp_path / "explicit-merged.tif"
    save_tiff_copy(doc, edits.document, merged, SaveOptions(keep_layers=False))
    assert not load_image(merged).metadata["has_photoshop_layers"]
    np.testing.assert_array_equal(load_image(merged).samples, edits.document.samples)


def test_vector_and_canvas_undo_redo_follow_operation_order(tmp_path):
    app, window = opened(tmp_path)
    try:
        window.install_svg(SvgArtwork.from_bytes(SVG), Placement(width_mm=.5, height_mm=.5))
        panel = window.vectors_panel
        snapshots = []
        def snapshot():
            snapshots.append((window.doc.samples.copy(), panel.placement))
        snapshot()
        panel.fields["x_mm"].setValue(.1); snapshot()
        panel.fields["y_mm"].setValue(.2); snapshot()
        window.perform_layer_change("expand_canvas", 30, 24, 4, 3); drain(app, window); snapshot()
        panel.fields["angle"].setValue(30); snapshot()
        window.perform_layer_change("expand_canvas", 40, 32, 2, 1); drain(app, window); snapshot()
        def check(expected):
            pixels, placement = expected
            np.testing.assert_array_equal(window.doc.samples, pixels)
            for name in ("x_mm", "y_mm", "width_mm", "height_mm", "angle"):
                assert getattr(panel.placement, name) == pytest.approx(getattr(placement, name))
        for saved in reversed(snapshots[:-1]):
            assert window.undo_action.isEnabled()
            window.undo(); drain(app, window); check(saved)
        assert not window.undo_action.isEnabled()
        for saved in snapshots[1:]:
            assert window.redo_action.isEnabled()
            window.redo(); drain(app, window); check(saved)
        assert not window.redo_action.isEnabled()
        window.undo(); drain(app, window)
        panel.fields["angle"].setValue(45)
        assert not window.edits.can_redo and not window.redo_action.isEnabled()
        window.undo(); drain(app, window)
        assert panel.can_redo
        window.perform_layer_change("expand_canvas", 50, 40, 1, 1); drain(app, window)
        assert not panel.can_redo and not window.redo_action.isEnabled()
    finally:
        window.vectors_panel.mark_saved(); cleanup(app, window)


@pytest.mark.parametrize("left,top", [(0, 0), (4, 3)])
def test_saved_job_becomes_dirty_when_bound_canvas_changes_even_after_tiff_save(tmp_path, monkeypatch, left, top):
    app, window = opened(tmp_path)
    try:
        window.install_svg(SvgArtwork.from_bytes(SVG), Placement(.1, .1, .5, .5))
        job = tmp_path / "alignment.tifview.json"
        monkeypatch.setattr(gui.QFileDialog, "getSaveFileName", lambda *args: (str(job), ""))
        window.choose_save_job()
        on_disk = job.read_bytes()
        assert not window.vectors_panel.dirty
        window.perform_layer_change("expand_canvas", 30, 24, left, top); drain(app, window)
        assert window.vectors_panel.dirty
        window.undo(); drain(app, window)
        assert not window.vectors_panel.dirty
        window.redo(); drain(app, window)
        target = tmp_path / "expanded.tif"
        save_tiff_copy(window.edits.original, window.doc, target)
        window.copy_saved(str(target))
        assert not window.edits.dirty
        assert window.vectors_panel.dirty and window.isWindowModified()
        monkeypatch.setattr(gui.QMessageBox, "question", lambda *args: gui.QMessageBox.StandardButton.Cancel)
        assert not window.confirm_discard()
        assert job.read_bytes() == on_disk
        window.choose_save_job()
        assert not window.vectors_panel.dirty and not window.isWindowModified()
        art, placement = load_job(job, load_image(target))
        assert art == window.vectors_panel.artwork and placement == window.vectors_panel.placement
    finally:
        window.vectors_panel.mark_saved(); cleanup(app, window)


@pytest.mark.parametrize("transparent", [True, False])
def test_lazy_layer_attachment_backfills_baseline_canvas_history_only(tmp_path, transparent):
    doc = load_image(make_source(tmp_path))
    edits = EditSession(doc)
    assert doc.layer_stack is None
    edits.expand_canvas(24, 20, 2, 3, transparent=transparent)
    rebased_stack = edits.document.layer_stack
    assert (rebased_stack is not None) == transparent
    edits.undo()
    stack = LayerStack.from_document(edits.document)
    edits.attach_layers(stack)
    assert edits.history[0].before.layer_stack is stack
    for _ in range(3):
        edits.redo()
        assert edits.document.layer_stack is rebased_stack
        edits.undo()
        assert edits.document.layer_stack is stack
        assert edits.document.layer_state is not None
    save_tiff_copy(doc, edits.document, tmp_path / "baseline-again.tif")


@pytest.mark.parametrize("problem", ["orientation", "outside", "canvas"])
def test_svg_layer_controls_explain_known_unsupported_states(tmp_path, problem):
    app, window = opened(tmp_path)
    try:
        if problem == "orientation":
            doc = replace(window.doc, orientation=3)
            window.accept_document(doc, render(doc))
        if problem == "canvas":
            window.doc.metadata["canvas_layer_reason"] = "White canvas needs a background layer"
        placement = Placement(x_mm=20 if problem == "outside" else 0, width_mm=.5, height_mm=.5)
        window.install_svg(SvgArtwork.from_bytes(SVG), placement)
        assert not window.svg_layer_action.isEnabled()
        assert not window.vectors_panel.layer_button.isEnabled()
        reason = window.svg_layer_action.toolTip()
        assert {"orientation": "orientation 1", "outside": "outside", "canvas": "White canvas"}[problem] in reason
        dialog = gui.SvgSaveDialog(window.doc, reason=reason)
        buttons = dialog.findChild(QDialogButtonBox).buttons()
        assert not next(b for b in buttons if b.text().startswith("Add SVG")).isEnabled()
        assert next(b for b in buttons if b.text() == "Save without SVG").isEnabled()
        assert any(reason in label.text() for label in dialog.findChildren(QLabel))
        dialog.close()
    finally:
        window.vectors_panel.mark_saved(); cleanup(app, window)


def test_svg_outside_canvas_can_still_save_without_svg(tmp_path, monkeypatch):
    app, window = opened(tmp_path)
    try:
        original = window.doc.samples.copy()
        window.install_svg(SvgArtwork.from_bytes(SVG), Placement(x_mm=20, width_mm=.5, height_mm=.5))
        target = tmp_path / "without-svg.tif"
        monkeypatch.setattr(gui.SvgSaveDialog, "exec", lambda self: 2)
        monkeypatch.setattr(gui.TiffSaveDialog, "exec", lambda self: 1)
        monkeypatch.setattr(gui.QFileDialog, "getSaveFileName", lambda *args: (str(target), "TIFF"))
        window.choose_save(); drain(app, window)
        np.testing.assert_array_equal(load_image(target).samples, original)
        assert window.vectors_panel.artwork is not None
    finally:
        window.vectors_panel.mark_saved(); cleanup(app, window)


def test_fractional_raster_reference_points_rebase_from_original_after_svg_and_repeated_growth(tmp_path, app):
    point = 23.217612806301457
    assert (point + 66) + 45 != point + 111
    samples = np.full((80, 100, 3), 100, np.uint8)
    ref = PsdUnknown(PsdKey.REFERENCE_POINT, PsdFormat.LE32BIT, struct.pack("<2d", point, .25))
    data = encode_layers([raster(samples, info=[ref])])
    path = tmp_path / "fractional-reference.tif"
    tifffile.imwrite(path, samples, photometric="rgb", metadata=None, resolution=(254, 254),
                     extratags=[(37724, 7, len(data), data, False)])
    doc = load_image(path); edits = EditSession(doc)
    edits.add_svg_layer(SvgArtwork.from_bytes(SVG), Placement(width_mm=3, height_mm=3), "SVG")
    edits.expand_canvas(166, 82, 66, 1)
    edits.expand_canvas(211, 84, 45, 1)
    stack = edits.document.layer_stack
    _, actual = next(_reference_points(stack, stack.layers[0]))
    assert actual == (point + 111, .25 + 2)
    target = tmp_path / "reference-preserved.tif"
    save_tiff_copy(doc, edits.document, target)
    np.testing.assert_array_equal(load_image(target).samples, edits.document.samples)
    edits.undo(); edits.redo()
    save_tiff_copy(doc, edits.document, tmp_path / "reference-redone.tif")


def test_svg_normalization_cannot_create_unreopenable_oversized_artwork(app):
    header = b'<svg xmlns="http://www.w3.org/2000/svg" width="3mm" height="3mm">'
    rect = b'<rect x="0.05mm" y="0.05mm" width="0.05mm" height="0.05mm" rx="0.05mm" ry="0.05mm" stroke-width="0.05mm"/>'
    raw = header + rect * 13000 + b'</svg>'
    assert len(raw) < MAX_SVG_BYTES
    with pytest.raises(ValueError, match="Normalized SVG exceeds"):
        SvgArtwork.from_bytes(raw)
