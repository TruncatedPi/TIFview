import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from tifview.app import ViewerWindow
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
    window.fit()
    assert window.view.fitted
    assert not window.doc.samples.flags.writeable
    window.close()
    app.processEvents()
