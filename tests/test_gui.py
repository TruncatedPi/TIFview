import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtCore import Qt, QPoint, QPointF
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from tifview.app import ImageView, ViewerWindow
from tools.make_demo import make_demo


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
    finally:
        view.close()
        app.processEvents()
