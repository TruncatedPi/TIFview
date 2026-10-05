"""Desktop layer actions display pixels and save changes without stale previews."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import hashlib
import threading
import time

import numpy as np
import pytest
import tifffile
from psdtags import PsdFormat, PsdKey, PsdUnknown
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

import tifview.app as gui
from tifview.layerpanel import LAYER_ID_ROLE
from tifview.layers import LayerStack
from tifview.reader import load_image
from tifview.render import render
from tifview.writer import layer_preservation_reason
from tools.make_demo import photoshop_resources
from test_layers import encode_layers, raster


def pump(app, condition, seconds=5):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    app.processEvents()
    assert condition(), "The Qt layer workers did not finish"


def idle(window):
    return (all(getattr(window, field) is None for field in
                ("loader", "saver", "layer_loader", "layer_editor", "previewer"))
            and window._pending_preview is None
            and getattr(window, "_pending_layer_change", None) is None)


def drain(app, window):
    pump(app, lambda: idle(window))


def layer_row(window, source_id):
    tree = window.layers_panel.tree
    return next(tree.topLevelItem(row) for row in range(tree.topLevelItemCount())
                if tree.topLevelItem(row).data(0, LAYER_ID_ROLE) == source_id)


def displayed_pixel(window, x, y):
    color = window.view.image_item.pixmap().toImage().pixelColor(x, y)
    return color.red(), color.green(), color.blue()


def make_source(tmp_path, empty_adjustment=False):
    background = np.full((12, 16, 3), [210, 20, 30], np.uint8)
    foreground = np.full((8, 10, 3), [20, 40, 220], np.uint8)
    layers = [raster(background, name="Red background"),
              raster(foreground, np.full((8, 10), 255, np.uint8),
                     bounds=(2, 4, 10, 14), name="Blue foreground")]
    if empty_adjustment:
        adjustment = PsdUnknown(PsdKey.COLOR_BALANCE, PsdFormat.LE32BIT, b"opaque adjustment")
        layers.append(raster(np.empty((0, 0, 3), np.uint8), name="Hidden adjustment",
                             visible=False, info=[adjustment]))
    source = encode_layers(layers)
    samples = np.empty((12, 16, 4), np.uint8)
    samples[..., :3] = background
    samples[2:10, 4:14, :3] = foreground
    samples[..., 3] = 255
    samples[6:9, 1:4, 3] = 0
    resources = photoshop_resources(["White Ink"], [2])
    path = tmp_path / "SYNTHETIC-layer-actions.tif"
    tifffile.imwrite(path, samples, photometric="rgb", extrasamples=[0], metadata=None,
                     compression="lzw", resolution=(360, 360),
                     extratags=[(37724, 7, len(source), source, False),
                                (34377, 7, len(resources), resources, False)])
    return path


def opened(tmp_path, empty_adjustment=False):
    app = QApplication.instance() or QApplication([])
    gui.configure_application(app)
    doc = load_image(make_source(tmp_path, empty_adjustment))
    window = gui.ViewerWindow()
    window.show()
    app.processEvents()
    window.accept_document(doc, render(doc))
    app.processEvents()
    return app, window


def cleanup(app, window):
    if window.previewer is not None:
        window.previewer.requestInterruption()
    drain(app, window)
    window.edits.mark_saved()
    window.close()
    app.processEvents()


@pytest.fixture(autouse=True)
def no_unexpected_modal(monkeypatch):
    messages = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, title, message:
                        messages.append(f"{title}: {message}"))
    yield
    assert not messages, "\n".join(messages)


def test_layer_inventory_loads_on_tab_request_and_does_not_dirty_source(tmp_path, monkeypatch):
    app, window = opened(tmp_path)
    release, started = threading.Event(), threading.Event()
    real_run = gui.LayerLoader.run

    def delayed_load(worker):
        started.set()
        assert release.wait(5)
        real_run(worker)

    monkeypatch.setattr(gui.LayerLoader, "run", delayed_load)
    before = hashlib.sha256(window.doc.path.read_bytes()).digest()
    original = window.doc.samples.copy()
    try:
        assert window.tabs.currentIndex() == 0
        assert window.layer_loader is None
        assert window.doc.layer_stack is None
        window.tabs.setCurrentIndex(1)
        pump(app, started.is_set)
        assert window.layer_loader is not None
        assert window.doc.layer_stack is None
        assert not window.layers_panel.tree.isEnabled()
        assert not window.edits.dirty
        release.set()
        drain(app, window)
        assert window.doc.layer_stack is not None
        assert window.layers_panel.tree.topLevelItem(1).text(0) == "Blue foreground"
        assert window.layers_panel.tree.topLevelItem(2).text(0) == "Red background"
        assert window.layers_panel.selected_index() is None
        assert displayed_pixel(window, 5, 5) == (20, 40, 220)
        assert displayed_pixel(window, 1, 1) == (210, 20, 30)
        assert not window.edits.dirty
        np.testing.assert_array_equal(window.doc.samples, original)
        assert hashlib.sha256(window.doc.path.read_bytes()).digest() == before
    finally:
        release.set()
        cleanup(app, window)


def test_hidden_layer_preview_and_visibility_edit_save_native_pixels_with_undo_redo(tmp_path):
    app, window = opened(tmp_path)
    original = window.doc.samples.copy()
    original_hash = hashlib.sha256(window.doc.path.read_bytes()).digest()
    try:
        window.tabs.setCurrentIndex(1)
        drain(app, window)
        layer_row(window, 0).setCheckState(0, Qt.CheckState.Unchecked)
        drain(app, window)
        assert window.doc.layer_state.visible == frozenset({1})
        assert window.edits.dirty and window.undo_action.isEnabled()
        assert len(window.doc.channels) == 5
        assert window.doc.channels[-1].kind == "Transparency"
        assert window.doc.samples[1, 1, -1] == 0
        assert window.doc.samples[5, 5, -1] == 255
        assert tuple(window.doc.samples[5, 5, :3]) == (20, 40, 220)
        np.testing.assert_array_equal(window.doc.samples[..., 3], original[..., 3])
        assert layer_preservation_reason(window.edits.original, window.doc) is None
        changed = window.doc.samples.copy()
        window.layers_panel.tree.setCurrentItem(layer_row(window, 0))
        drain(app, window)
        assert displayed_pixel(window, 5, 5) == (210, 20, 30)
        assert not layer_row(window, 0).checkState(0) == Qt.CheckState.Checked
        window.undo()
        drain(app, window)
        assert window.layers_panel.selected_index() == 0
        assert window.doc.layer_state.visible == frozenset({0, 1})
        assert not window.edits.dirty
        np.testing.assert_array_equal(window.doc.samples, original)
        window.redo()
        drain(app, window)
        np.testing.assert_array_equal(window.doc.samples, changed)
        assert window.doc.layer_state.visible == frozenset({1})
        output = tmp_path / "layer-visibility-copy.tif"
        window.save_copy(str(output))
        drain(app, window)
        assert output.exists()
        assert not window.edits.dirty
        reopened = load_image(output)
        np.testing.assert_array_equal(reopened.samples, changed)
        saved_stack = LayerStack.from_document(reopened)
        assert [layer.name for layer in saved_stack.layers] == ["Red background", "Blue foreground"]
        assert saved_stack.default_visible == frozenset({1})
        assert hashlib.sha256(window.doc.path.read_bytes()).digest() == original_hash
    finally:
        cleanup(app, window)


def test_reorder_changes_merged_pixels_and_keeps_layer_selection(tmp_path):
    app, window = opened(tmp_path)
    original = window.doc.samples.copy()
    try:
        window.tabs.setCurrentIndex(1)
        drain(app, window)
        window.layers_panel.tree.setCurrentItem(layer_row(window, 1))
        drain(app, window)
        QTest.mouseClick(window.layers_panel.down_button, Qt.MouseButton.LeftButton)
        drain(app, window)
        assert window.doc.layer_state.order == (0, 1)
        assert window.layers_panel.selected_index() == 1
        assert window.layers_panel.tree.topLevelItem(1).text(0) == "Red background"
        assert tuple(window.doc.samples[5, 5, :3]) == (210, 20, 30)
        # Selecting the blue layer still shows its own pixels below the red layer.
        assert displayed_pixel(window, 5, 5) == (20, 40, 220)
        window.layers_panel.tree.setCurrentItem(layer_row(window, None))
        drain(app, window)
        assert displayed_pixel(window, 5, 5) == (210, 20, 30)
        window.undo()
        drain(app, window)
        np.testing.assert_array_equal(window.doc.samples, original)
        assert displayed_pixel(window, 5, 5) == (20, 40, 220)
    finally:
        cleanup(app, window)


def test_layer_without_cached_pixels_clears_previous_artwork_and_reports_reason(tmp_path):
    app, window = opened(tmp_path, empty_adjustment=True)
    try:
        window.tabs.setCurrentIndex(1)
        drain(app, window)
        assert displayed_pixel(window, 5, 5) == (20, 40, 220)
        window.layers_panel.tree.setCurrentItem(layer_row(window, 2))
        drain(app, window)
        assert window.view.image_item.pixmap().isNull()
        assert window._displayed_preview is None
        assert "no cached raster pixels" in window.statusBar().currentMessage()
        assert not window.edits.dirty
        window.layers_panel.tree.setCurrentItem(layer_row(window, 1))
        drain(app, window)
        assert displayed_pixel(window, 5, 5) == (20, 40, 220)
    finally:
        cleanup(app, window)


def test_layers_are_pan_only_and_channel_painting_retains_loaded_stack(tmp_path):
    app, window = opened(tmp_path)
    try:
        window.tabs.setCurrentIndex(1)
        drain(app, window)
        stack = window.doc.layer_stack
        assert not window.edit_toolbar.isEnabled()
        assert not window.view.drawing_enabled
        window.apply_drawing("Box", (5., 5.), (9., 9.))
        assert not window.edits.dirty
        window.tabs.setCurrentIndex(0)
        window.channels.setCurrentItem(window.channels.topLevelItem(4))
        drain(app, window)
        assert window.edit_toolbar.isEnabled()
        window.fill.setChecked(True)
        window.tool_combo.setCurrentText("Box")
        window.apply_drawing("Box", (5., 5.), (9., 9.))
        assert window.doc.samples[7, 7, 3] == 0
        assert window.edits.dirty
        window.tabs.setCurrentIndex(1)
        drain(app, window)
        assert window.doc.layer_stack is stack
        assert window.tool_combo.currentText() == "Pan"
        assert window.view.tool == "Pan"
        assert not window.edit_toolbar.isEnabled()
        assert not window.view.drawing_enabled
        assert layer_preservation_reason(window.edits.original, window.doc) is None
    finally:
        cleanup(app, window)


@pytest.mark.parametrize("finish_on_channels", [False, True])
def test_quick_layer_switch_discards_stale_preview_and_runs_queued_visibility_edit(tmp_path, monkeypatch, finish_on_channels):
    app, window = opened(tmp_path)
    started, release = threading.Event(), threading.Event()
    real_render_layer = LayerStack.render_layer

    def delayed_layer(stack, index, stride=1):
        if index == 1:
            started.set()
            assert release.wait(5)
        return real_render_layer(stack, index, stride)

    try:
        window.tabs.setCurrentIndex(1)
        drain(app, window)
        monkeypatch.setattr(LayerStack, "render_layer", delayed_layer)
        window.layers_panel.tree.setCurrentItem(layer_row(window, 1))
        pump(app, started.is_set)
        window.layers_panel.tree.setCurrentItem(layer_row(window, 0))
        layer_row(window, 0).setCheckState(0, Qt.CheckState.Unchecked)
        assert getattr(window, "_pending_layer_change", None) is not None
        assert window.doc.layer_state.visible == frozenset({0, 1})
        if finish_on_channels:
            window.tabs.setCurrentIndex(0)
        release.set()
        drain(app, window)
        assert window.doc.layer_state.visible == frozenset({1})
        assert window.edits.dirty
        assert window.layers_panel.selected_index() == 0
        key, settings = window.preview_settings()
        assert window._displayed_preview == key
        if finish_on_channels:
            assert displayed_pixel(window, 5, 5) == (20, 40, 220)
            assert displayed_pixel(window, 1, 1) != (210, 20, 30)
        else:
            assert displayed_pixel(window, 5, 5) == (210, 20, 30)
        assert window.doc.samples[1, 1, -1] == 0
    finally:
        release.set()
        cleanup(app, window)


def test_queued_layer_edit_blocks_second_checkbox_and_direct_request(tmp_path, monkeypatch):
    app, window = opened(tmp_path)
    started, release = threading.Event(), threading.Event()
    real_render_layer = LayerStack.render_layer

    def delayed_layer(stack, index, stride=1):
        started.set()
        assert release.wait(5)
        return real_render_layer(stack, index, stride)

    try:
        window.tabs.setCurrentIndex(1)
        drain(app, window)
        monkeypatch.setattr(LayerStack, "render_layer", delayed_layer)
        window.layers_panel.tree.setCurrentItem(layer_row(window, 1))
        pump(app, started.is_set)
        layer_row(window, 0).setCheckState(0, Qt.CheckState.Unchecked)
        first_request = window._pending_layer_change
        assert first_request == ("set_layer_visibility", (0, False))
        assert window.layer_busy()
        assert not window.layers_panel.tree.isEnabled()
        assert not window.layers_panel.up_button.isEnabled()
        assert not window.layers_panel.down_button.isEnabled()
        assert not layer_row(window, 1).flags() & Qt.ItemFlag.ItemIsUserCheckable
        # A queued programmatic checkbox signal and direct action must not
        # replace the first accepted request while its preview is finishing.
        layer_row(window, 1).setCheckState(0, Qt.CheckState.Unchecked)
        window.layer_visibility_changed(1, False)
        window.move_layer(1, 1)
        assert window._pending_layer_change == first_request
        assert layer_row(window, 1).checkState(0) == Qt.CheckState.Checked
        release.set()
        drain(app, window)
        assert window.doc.layer_state.visible == frozenset({1})
        assert window.doc.layer_state.order == (1, 0)
        assert len(window.edits.history) == 1
        assert window.edits.dirty
        assert window.layers_panel.tree.isEnabled()
        assert layer_row(window, 1).checkState(0) == Qt.CheckState.Checked
        assert layer_row(window, 0).checkState(0) == Qt.CheckState.Unchecked
    finally:
        release.set()
        cleanup(app, window)
