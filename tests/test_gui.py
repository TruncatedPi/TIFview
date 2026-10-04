import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtCore import Qt, QPoint, QPointF
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from tifview.app import ImageView, TiffSaveDialog, ViewerWindow, configure_application
from tifview.reader import load_image
from tools.make_demo import make_demo


def test_tiff_save_explains_content_credentials_omission(tmp_path):
    app = QApplication.instance() or QApplication([])
    path = tmp_path / "demo.tif"
    make_demo(path)
    doc = load_image(path)
    doc.metadata["has_content_credentials"] = True
    dialog = TiffSaveDialog(doc, doc)
    try:
        assert any("Content Credentials are omitted" in label.text()
                   for label in dialog.findChildren(QLabel))
    finally:
        dialog.close()
        app.processEvents()


def test_async_open_selection_visibility_and_zoom(tmp_path):
    app = QApplication.instance() or QApplication([])
    path = tmp_path / "synthetic.tif"
    make_demo(path)
    window = ViewerWindow()
    window.show()
    window.open_path(str(path))
    deadline = time.monotonic() + 15
    while window.loader is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert window.doc is not None
    assert window.loader is None
    assert window.channels.topLevelItemCount() == 7
    window.channels.setCurrentItem(window.channels.topLevelItem(4))
    assert window.selected_index() == 3
    assert window.style_combo.isEnabled()
    assert not window.overlays.isEnabled()
    window.inspect_pixel(110, 80)
    assert "White Ink: 0" in window.statusBar().currentMessage()
    pixels = window.view.image_item.pixmap().toImage()
    assert pixels.pixelColor(110, 80).red() == 0
    window.style_combo.setCurrentIndex(1)
    window.opacity.setValue(100)
    assert window.view.image_item.pixmap().toImage().pixelColor(110, 80).red() == 255
    window.channels.setCurrentItem(window.channels.topLevelItem(0))
    window.overlays.setChecked(True)
    window.channels.topLevelItem(4).setCheckState(0, Qt.CheckState.Checked)
    window.actual()
    assert abs(window.view.transform().m11() * window.view.viewport().devicePixelRatioF() - 1) < .0001
    window.view.zoom_by(2)
    assert abs(window.view.transform().m11() * window.view.viewport().devicePixelRatioF() - 2) < .0001
    # Selecting a different channel must retain the inspection magnification/position.
    window.view.centerOn(480, 240)
    app.processEvents()
    viewport_center = window.view.viewport().rect().center()
    center_before = window.view.mapToScene(viewport_center)
    window.channels.setCurrentItem(window.channels.topLevelItem(5))
    app.processEvents()
    assert window.selected_index() == 4
    assert abs(window.view.transform().m11() * window.view.viewport().devicePixelRatioF() - 2) < .0001
    center_after = window.view.mapToScene(viewport_center)
    assert abs(center_after.x() - center_before.x()) < 1
    assert abs(center_after.y() - center_before.y()) < 1
    window.fit()
    assert window.view.fitted
    assert not window.doc.samples.flags.writeable
    window.close()
    app.processEvents()


def test_mouse_wheel_zoom_and_drag_pan():
    app = QApplication.instance() or QApplication([])
    view = ImageView()
    view.resize(700, 500)
    view.show()
    view.set_image(np.zeros((1000, 1400, 3), np.uint8))
    view.actual_pixels()
    app.processEvents()
    try:
        position = QPoint(170, 120)
        QTest.mouseMove(view.viewport(), position)
        app.processEvents()
        before = view.mapToScene(position)
        wheel = QWheelEvent(QPointF(position), QPointF(view.viewport().mapToGlobal(position)),
                            QPoint(), QPoint(0, 120), Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(view.viewport(), wheel)
        app.processEvents()
        assert abs(view.transform().m11() * view.viewport().devicePixelRatioF() - 1.2) < .0001
        after = view.mapToScene(position)
        # Scrollbar positioning rounds to whole logical pixels.
        assert abs(after.x() - before.x()) < 2
        assert abs(after.y() - before.y()) < 2
        start, end = QPoint(300, 230), QPoint(380, 290)
        before = view.mapToScene(start)
        scroll_before = (view.horizontalScrollBar().value(), view.verticalScrollBar().value())
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(view.viewport(), end, 20)
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
        app.processEvents()
        assert (view.horizontalScrollBar().value(), view.verticalScrollBar().value()) != scroll_before
        after = view.mapToScene(end)
        assert abs(after.x() - before.x()) < 2
        assert abs(after.y() - before.y()) < 2
        view.set_tool("Line")
        before = view.mapToScene(start)
        QTest.mousePress(view.viewport(), Qt.MouseButton.MiddleButton, pos=start)
        QTest.mouseMove(view.viewport(), end, 20)
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.MiddleButton, pos=end)
        app.processEvents()
        after = view.mapToScene(end)
        assert abs(after.x() - before.x()) < 2
        assert abs(after.y() - before.y()) < 2
        assert view._drawing_start is None and view._pan_position is None
    finally:
        view.close()
        app.processEvents()


def test_mouse_shape_paints_selected_spot_undo_redo_and_async_tiff_copy(tmp_path):
    app = QApplication.instance() or QApplication([])
    configure_application(app)
    path = tmp_path / "layered-demo.tif"
    make_demo(path, layers=True)
    window = ViewerWindow()
    window.show()
    errors = []
    window.save_failed = errors.append
    try:
        window.open_path(str(path))
        deadline = time.monotonic() + 15
        while window.loader is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.01)
        assert window.doc is not None and window.loader is None
        window.channels.setCurrentItem(window.channels.topLevelItem(4))
        window.actual()
        window.tool_combo.setCurrentText("Box")
        window.fill.setChecked(True)
        app.processEvents()
        start = window.view.mapFromScene(330, 50)
        end = window.view.mapFromScene(410, 120)
        QTest.mousePress(window.view.viewport(), Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(window.view.viewport(), end, 20)
        QTest.mouseRelease(window.view.viewport(), Qt.MouseButton.LeftButton, pos=end)
        app.processEvents()
        assert window.edits.dirty
        assert window.doc.samples[85, 370, 3] == 0
        assert window.edits.original.samples[85, 370, 3] == 255
        assert window.view.image_item.pixmap().toImage().pixelColor(370, 85).red() == 0
        assert window.undo_action.isEnabled()
        painted = window.doc.samples.copy()
        window.style_combo.setCurrentIndex(1)
        assert window.tool_combo.currentText() == "Pan"
        np.testing.assert_array_equal(window.doc.samples, painted)
        window.tool_combo.setCurrentText("Box")
        assert window.style_combo.currentIndex() == 0
        QTest.keyClick(window.view.viewport(), Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        app.processEvents()
        assert window.doc.samples[85, 370, 3] == 255
        assert not window.edits.dirty
        QTest.keyClick(window.view.viewport(), Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
        app.processEvents()
        assert window.doc.samples[85, 370, 3] == 0
        settings = TiffSaveDialog(window.edits.original, window.doc, window)
        assert settings.layers.isChecked() and settings.layers.isEnabled()
        target = tmp_path / "edited-copy.tif"
        window.save_copy(str(target), settings.options())
        assert not window.edit_toolbar.isEnabled()
        deadline = time.monotonic() + 15
        while window.saver is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.01)
        assert window.saver is None
        assert not errors
        assert not window.edits.dirty
        saved = load_image(target)
        assert saved.samples[85, 370, 3] == 0
        assert saved.metadata["has_photoshop_layers"]
        assert saved.metadata["pyramid_subifds"] == 1
        np.testing.assert_array_equal(saved.samples[..., :3], window.edits.original.samples[..., :3])
        assert load_image(path).samples[85, 370, 3] == 255
        window.channels.setCurrentItem(window.channels.topLevelItem(1))
        window.apply_drawing("Box", (330., 50.), (410., 120.))
        merged = TiffSaveDialog(window.edits.original, window.doc, window)
        assert not merged.layers.isEnabled() and not merged.options().keep_layers
        window.undo()
        assert not window.edits.dirty
        window.channels.setCurrentItem(window.channels.topLevelItem(0))
        assert window.view.tool == "Pan" and not window.edit_toolbar.isEnabled()
    finally:
        if window.saver is not None:
            window.saver.wait(15000)
            app.processEvents()
        if window.edits:
            window.edits.mark_saved()
        window.close()
        app.processEvents()
