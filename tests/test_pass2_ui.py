"""GUI and interaction tests for Pass 2 Photoshop-style layout, shortcuts, SVG drag, and layer ergonomics."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dataclasses import replace

import numpy as np
import pytest
import tifffile
from PySide6.QtCore import QEvent, QMimeData, QPoint, Qt
from PySide6.QtGui import QDropEvent, QKeyEvent, QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QGraphicsView, QMessageBox, QStyle, QStyleOptionViewItem

import tifview.app as gui
from tifview.editing import EditSession
from tifview.layers import LayerStack
from tifview.model import Channel
from tifview.reader import load_image
from tifview.render import render
from tifview.svg import Placement, SvgArtwork, canvas_fit
from test_app_layers import cleanup, displayed_pixel, drain, layer_row, opened


@pytest.fixture(autouse=True)
def no_unexpected_modal(monkeypatch):
    messages = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, title, message:
                        messages.append(f"{title}: {message}"))
    yield
    assert not messages, "\n".join(messages)
from test_layers import encode_layers, raster


SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="5mm" viewBox="0 0 100 50">'
    b'<rect x="0" y="0" width="100" height="50" fill="red" stroke="black" stroke-width="2"/></svg>'
)


def test_photoshop_layout_menus_shortcuts_and_synced_tool_strip(tmp_path):
    app, window = opened(tmp_path)
    try:
        # Right-hand panels dock, center/left image view
        assert window.splitter.widget(0) is window.view
        assert window.tabs.parentWidget() is window.splitter.widget(1)

        # Standard menu bar menus
        menu_titles = [act.text() for act in window.menuBar().actions()]
        assert menu_titles == ["File", "Edit", "Image", "Layer", "Spots", "Vectors", "View", "Help"]

        # Shortcuts
        assert QKeySequence(QKeySequence.StandardKey.Save) in window.save_action.shortcuts()
        assert QKeySequence("Ctrl+Shift+S") in window.save_action.shortcuts()
        assert QKeySequence("Ctrl+0") in window.fit_action.shortcuts()
        assert QKeySequence("Ctrl+1") in window.actual_action.shortcuts()
        assert QKeySequence("Ctrl+Alt+C") == window.canvas_size_action.shortcut()
        assert QKeySequence("Ctrl+I") == window.import_svg_action.shortcut()

        # Left vertical tool strip synced bi-directionally with tool_combo
        assert window.toolBarArea(window.tool_strip) == Qt.ToolBarArea.LeftToolBarArea
        assert window.tool_strip.orientation() == Qt.Orientation.Vertical
        window.tabs.setCurrentIndex(1)
        drain(app, window)
        assert window.tool_action_map["Box"].isEnabled()
        window.tool_action_map["Box"].trigger()
        assert window.tool_combo.currentText() == "Box"
        assert window.view.tool == "Box"
        window.tool_combo.setCurrentText("Ellipse")
        assert window.tool_action_map["Ellipse"].isChecked()
        assert window.view.tool == "Ellipse"

        # Normal Photoshop TIFF should not show the noisy yellow notice banner
        assert not window.notice.isVisible()
        # Unknown extra channel triggers actionable notice banner
        unknown_channels = list(window.doc.channels)
        unknown_channels[-1] = replace(unknown_channels[-1], kind="Unknown")
        doc_unknown = replace(window.doc, channels=unknown_channels)
        window.accept_document(doc_unknown, render(doc_unknown))
        assert window.notice.isVisible()
    finally:
        cleanup(app, window)


def test_spacebar_temporary_pan_while_drawing_tool_active(tmp_path):
    app, window = opened(tmp_path)
    try:
        window.tabs.setCurrentIndex(1)
        drain(app, window)
        window.tool_combo.setCurrentText("Box")
        assert window.view.dragMode() == QGraphicsView.DragMode.NoDrag

        press = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier, " ", False)
        release = QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier, " ", False)
        window.view.keyPressEvent(press)
        assert window.view._space_pan
        assert window.view.dragMode() == QGraphicsView.DragMode.ScrollHandDrag
        window.view.keyReleaseEvent(release)
        assert not window.view._space_pan
        assert window.view.dragMode() == QGraphicsView.DragMode.NoDrag
        assert window.view.tool == "Box"
    finally:
        cleanup(app, window)


def test_direct_svg_canvas_drag_center_fit_and_outline(tmp_path):
    app = QApplication.instance() or QApplication([])
    gui.configure_application(app)
    path = tmp_path / "canvas-20x10mm.tif"
    # 200x100 px at 254 dpi = 20 x 10 mm exactly (10 px per mm)
    tifffile.imwrite(path, np.full((100, 200, 3), 240, np.uint8), photometric="rgb",
                     metadata=None, resolution=(254, 254))
    doc = load_image(path)
    window = gui.ViewerWindow()
    window.resize(900, 700)
    window.show()
    window.accept_document(doc, render(doc))
    window.view.fit_image()
    app.processEvents()
    try:
        art = SvgArtwork.from_bytes(SVG, "cut.svg")
        window.install_svg(art, Placement(2, 1, 10, 5, angle=0))
        app.processEvents()
        assert window.view.svg_outline.isVisible()
        assert window.view.svg_outline.transform() == window.view.svg_item.transform()

        # Drag SVG from (50, 30) scene px to (80, 50) scene px (+30, +20 px = +3, +2 mm)
        start = window.view.mapFromScene(50, 30)
        end = window.view.mapFromScene(80, 50)
        QTest.mousePress(window.view.viewport(), Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(window.view.viewport(), end, 10)
        QTest.mouseRelease(window.view.viewport(), Qt.MouseButton.LeftButton, pos=end)
        app.processEvents()

        # Screen pixel mapping can shift by 1 viewport px -> tolerance <= 0.3 mm, single undo restores exact
        p = window.vectors_panel.placement
        assert p.x_mm > 3.5 and p.y_mm > 1.8
        window.undo()
        assert window.vectors_panel.placement.x_mm == pytest.approx(2.0)
        assert window.vectors_panel.placement.y_mm == pytest.approx(1.0)
        window.redo()
        assert window.vectors_panel.placement == p

        # Center on canvas (20x10 mm canvas, 10.2x5.2 mm full_rect including stroke)
        window.vectors_panel.center_button.click()
        app.processEvents()
        assert window.vectors_panel.placement.x_mm == pytest.approx(5.0)
        assert window.vectors_panel.placement.y_mm == pytest.approx(2.5)

        # Fit inside canvas (accounts for stroke bounds so complete SVG fits 200x100 canvas)
        window.vectors_panel.set_placement(Placement(0, 0, 40, 20))
        window.vectors_panel.fit_inside_button.click()
        app.processEvents()
        assert canvas_fit(doc, art, window.vectors_panel.placement) == (doc.width, doc.height, 0, 0)
        assert window.vectors_panel.placement.width_mm == pytest.approx(10 * min(20 / 10.2, 10 / 5.2))
        assert window.vectors_panel.placement.height_mm == pytest.approx(5 * min(20 / 10.2, 10 / 5.2))

        # Hiding SVG hides guide outline
        window.vectors_panel.visible.setChecked(False)
        assert not window.view.svg_outline.isVisible()
    finally:
        window.vectors_panel.mark_saved()
        cleanup(app, window)


def test_layers_panel_composite_checkbox_isolate_toggle_thumbnails_and_drag_reorder(tmp_path):
    app, window = opened(tmp_path)
    try:
        window.tabs.setCurrentIndex(1)
        drain(app, window)
        # Thumbnails populated cleanly
        tree = window.layers_panel.tree
        assert not tree.topLevelItem(1).icon(0).isNull()
        assert not tree.topLevelItem(2).icon(0).isNull()

        # Add a third layer so we can verify multi-step drag/move_layer_to reordering
        green = np.full((6, 6, 3), [20, 220, 40], np.uint8)
        raw = encode_layers([
            raster(np.full((12, 16, 3), [210, 20, 30], np.uint8), name="Red"),
            raster(np.full((8, 10, 3), [20, 40, 220], np.uint8), np.full((8, 10), 255, np.uint8), bounds=(2, 4, 10, 14), name="Blue"),
            raster(green, np.full((6, 6), 255, np.uint8), bounds=(2, 4, 8, 10), name="Green"),
        ])
        layered_path = tmp_path / "three-layers.tif"
        merged = np.empty((12, 16, 3), np.uint8)
        merged[..., :3] = [210, 20, 30]
        merged[2:10, 4:14, :3] = [20, 40, 220]
        merged[2:8, 4:10, :3] = [20, 220, 40]
        tifffile.imwrite(layered_path, merged, photometric="rgb",
                         metadata=None, resolution=(254, 254), extratags=[(37724, 7, len(raw), raw, False)])
        doc3 = load_image(layered_path)
        window.accept_document(doc3, render(doc3))
        window.tabs.setCurrentIndex(1)
        drain(app, window)
        assert [tree.topLevelItem(i).text(0) for i in range(1, 4)] == ["Green", "Blue", "Red"]

        # Uncheck Isolate selected layer: selecting "Red" keeps showing the composite ("Green" on top at 5,5)
        assert window.layers_panel.isolate_checkbox.isChecked()
        window.layers_panel.isolate_checkbox.setChecked(False)
        drain(app, window)
        tree.setCurrentItem(layer_row(window, 0))
        drain(app, window)
        assert displayed_pixel(window, 5, 5) == (20, 220, 40)

        # Clicking checkbox indicator on row 1 ("Green") keeps current selection unchanged
        tree.setCurrentItem(tree.topLevelItem(0))
        drain(app, window)
        green_item = tree.topLevelItem(1)
        option = QStyleOptionViewItem()
        tree.initViewItemOption(option)
        option.rect = tree.visualItemRect(green_item)
        option.features |= QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
        check_center = tree.style().subElementRect(
            QStyle.SubElement.SE_ItemViewItemCheckIndicator, option, tree).center()
        QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=check_center)
        drain(app, window)
        assert window.layers_panel.selected_index() is None
        assert 2 not in window.doc.layer_state.visible
        assert displayed_pixel(window, 5, 5) == (20, 40, 220)

        # move_layer_to via reorder_requested (e.g. context menu "Move to bottom") moves Green (2) to bottom (pos 2) in 1 undo step
        window.undo()
        drain(app, window)
        window.layers_panel.reorder_requested.emit(2, 2)
        drain(app, window)
        assert window.doc.layer_state.order == (1, 0, 2)
        window.undo()
        drain(app, window)
        assert window.doc.layer_state.order == (2, 1, 0)
    finally:
        cleanup(app, window)
