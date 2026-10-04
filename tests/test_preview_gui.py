"""Preview caching and worker races must not show another file or stale pixels."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading
import time
import numpy as np
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

import tifview.app as gui
from tifview.reader import load_image
from tifview.render import render
from tools.make_demo import make_demo


def pump(app, condition, seconds=5):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    app.processEvents()
    assert condition()


def opened(tmp_path):
    app = QApplication.instance() or QApplication([])
    gui.configure_application(app)
    path = tmp_path / "channels.tif"
    make_demo(path)
    doc = load_image(path)
    window = gui.ViewerWindow()
    window.show()
    app.processEvents()
    window.accept_document(doc, render(doc))
    app.processEvents()
    return app, window


def cleanup(app, window):
    if window.previewer:
        window.previewer.requestInterruption()
        pump(app, lambda: window.previewer is None)
    if window.loader:
        pump(app, lambda: window.loader is None)
    window.edits.mark_saved()
    window.close()
    app.processEvents()


def test_channel_cache_ignores_irrelevant_controls_and_invalidates_after_edits(tmp_path, monkeypatch):
    app, window = opened(tmp_path)
    calls = []
    real_grayscale = gui.grayscale
    monkeypatch.setattr(gui, "grayscale", lambda *args: (calls.append(args[1]), real_grayscale(*args))[1])
    try:
        assert window.channels.topLevelItem(4).text(0) == "1. White Ink"
        assert window.channels.topLevelItem(5).text(0) == "2. Varnish"
        assert window.channels.topLevelItem(6).text(0) == "Saved selection"
        window.channels.setCurrentItem(window.channels.topLevelItem(4))
        original_pixmap = window.view.image_item.pixmap().cacheKey()
        window.channels.topLevelItem(1).setCheckState(0, Qt.CheckState.Unchecked)
        window.opacity.setValue(30)
        window.colors[3] = (1, 2, 3)
        window.refresh()
        assert calls == [3]
        assert window.view.image_item.pixmap().cacheKey() == original_pixmap
        window.channels.setCurrentItem(window.channels.topLevelItem(5))
        window.channels.setCurrentItem(window.channels.topLevelItem(4))
        assert calls == [3, 4]
        window.fill.setChecked(True)
        window.apply_drawing("Box", (330., 50.), (410., 120.))
        assert window.view.image_item.pixmap().toImage().pixelColor(370, 85).red() == 0
        assert calls == [3, 4, 3]
        window.undo()
        assert window.view.image_item.pixmap().toImage().pixelColor(370, 85).red() == 255
        assert window.edits.original.samples[85, 370, 3] == 255
        assert window.doc.channels[3].name == "White Ink"
    finally:
        cleanup(app, window)


def test_background_preview_cancels_stale_result_and_coalesces_latest_view(tmp_path, monkeypatch):
    app, window = opened(tmp_path)
    monkeypatch.setattr(gui, "ASYNC_PREVIEW_PIXELS", 0)
    started, release = threading.Event(), threading.Event()
    real_render = gui.render

    def controlled_render(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return real_render(*args, **kwargs)

    monkeypatch.setattr(gui, "render", controlled_render)
    try:
        window.channels.topLevelItem(1).setCheckState(0, Qt.CheckState.Unchecked)
        pump(app, started.is_set)
        window.channels.setCurrentItem(window.channels.topLevelItem(4))
        assert window.view.image_item.pixmap().toImage().pixelColor(110, 80).red() == 0
        assert not window.edit_toolbar.isEnabled() and not window.save_action.isEnabled()
        window.apply_drawing("Box", (330., 50.), (410., 120.))
        assert not window.edits.dirty
        window.channels.setCurrentItem(window.channels.topLevelItem(0))
        window.overlays.setChecked(True)
        window.channels.topLevelItem(4).setCheckState(0, Qt.CheckState.Checked)
        release.set()
        pump(app, lambda: window.previewer is None and window._pending_preview is None)
        key, settings = window.preview_settings()
        assert window._displayed_preview == key
        expected = real_render(window.doc, **settings)
        color = window.view.image_item.pixmap().toImage().pixelColor(110, 80)
        assert (color.red(), color.green(), color.blue()) == tuple(expected[80, 110])
        assert window.save_action.isEnabled() and not window.edits.dirty
    finally:
        release.set()
        cleanup(app, window)


def test_old_file_controls_cannot_start_preview_during_next_load(tmp_path, monkeypatch):
    app, window = opened(tmp_path)
    monkeypatch.setattr(gui, "ASYNC_PREVIEW_PIXELS", 0)
    release = threading.Event()
    real_run = gui.Loader.run
    monkeypatch.setattr(gui.Loader, "run", lambda worker: (release.wait(5), real_run(worker)))
    next_file = tmp_path / "next.png"
    Image.new("RGB", (130, 200), (20, 40, 60)).save(next_file)
    try:
        window.channels.setCurrentItem(window.channels.topLevelItem(4))
        window.open_path(str(next_file))
        assert not window.channels.isEnabled()
        # Programmatic changes emulate queued control signals from the old file.
        window.channels.setCurrentItem(window.channels.topLevelItem(0))
        window.style_combo.setCurrentIndex(1)
        window.opacity.setValue(10)
        assert window.previewer is None
        release.set()
        pump(app, lambda: window.loader is None and window.previewer is None and window.doc.path == next_file)
        assert window.view.image_item.pixmap().size().width() == 130
        color = window.view.image_item.pixmap().toImage().pixelColor(0, 0)
        assert (color.red(), color.green(), color.blue()) == (20, 40, 60)
    finally:
        release.set()
        cleanup(app, window)


def test_cancel_close_during_preview_restores_edit_controls_and_keeps_pixels(tmp_path, monkeypatch):
    app, window = opened(tmp_path)
    monkeypatch.setattr(gui, "ASYNC_PREVIEW_PIXELS", 0)
    release = threading.Event()
    real_render = gui.render
    monkeypatch.setattr(gui, "render", lambda *a, **k: (release.wait(5), real_render(*a, **k))[1])
    try:
        window.channels.setCurrentItem(window.channels.topLevelItem(4))
        window.fill.setChecked(True)
        window.apply_drawing("Box", (330., 50.), (410., 120.))
        window.channels.setCurrentItem(window.channels.topLevelItem(0))
        assert window.previewer is not None
        window.confirm_discard = lambda: False
        window.close()
        release.set()
        pump(app, lambda: window.previewer is None)
        assert window.isVisible() and window.edits.dirty
        assert window.save_action.isEnabled() and window.undo_action.isEnabled()
        assert window.doc.samples[85, 370, 3] == 0
    finally:
        release.set()
        window.confirm_discard = lambda: True
        cleanup(app, window)


def test_fitted_preview_preserves_source_coordinates_and_actual_pixel_view(tmp_path):
    app = QApplication.instance() or QApplication([])
    gui.configure_application(app)
    # Large shape triggers fitted colour preview; source array is small enough for CI.
    from tifview.model import Channel, ImageDocument
    samples = np.full((1600, 1600, 3), [10, 20, 30], np.uint8)
    doc = ImageDocument(tmp_path / "large.tif", samples,
                        [Channel(i, name, "Process", "test", (0, 0, 0))
                         for i, name in enumerate(("Red", "Green", "Blue"))], "RGB", 3, 8)
    window = gui.ViewerWindow()
    window.show()
    app.processEvents()
    window.accept_document(doc, None)
    try:
        pump(app, lambda: window.previewer is None and window._displayed_preview is not None)
        assert window.view.sceneRect().width() == doc.width
        assert window.view.sceneRect().height() == doc.height
        assert window.view.image_item.pixmap().width() < doc.width
        assert window.view.image_item.sceneBoundingRect().width() == doc.width
        window.actual()
        pump(app, lambda: window.previewer is None)
        assert window.view.image_item.pixmap().size().width() == doc.width
        assert window.view.image_item.transform().m11() == 1
        assert window.view.image_item.pixmap().toImage().pixelColor(1000, 1000).red() == 10
        assert abs(window.view.transform().m11() * window.view.viewport().devicePixelRatioF() - 1) < .0001
        assert not doc.samples.flags.writeable
    finally:
        cleanup(app, window)
