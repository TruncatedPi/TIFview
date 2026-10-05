"""Layer controls request accepted edits and retain stable source selection."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from tifview.layerpanel import LAYER_ID_ROLE, LayersPanel


def example_stack():
    return SimpleNamespace(
        layers=[SimpleNamespace(index=index, name=name, kind=kind, visible=visible,
                                opacity=255, blend_mode="normal", issues=issues)
                for index, name, kind, visible, issues in (
                    (0, "Background", "Pixel", True, []),
                    (4, "Hidden label", "Text", False, ["Text uses its saved raster preview"]),
                    (9, "Foreground", "Pixel", True, []))],
        default_order=(9, 4, 0), default_visible={0, 9})


@pytest.fixture
def panel():
    app = QApplication.instance() or QApplication([])
    widget = LayersPanel()
    widget.resize(360, 400)
    widget.set_layers(example_stack())
    widget.show()
    app.processEvents()
    yield widget
    widget.close()
    app.processEvents()


def row_ids(panel):
    return [panel.tree.topLevelItem(row).data(0, LAYER_ID_ROLE)
            for row in range(panel.tree.topLevelItemCount())]


def test_top_to_bottom_rows_use_source_ids_and_hidden_layers_remain_selectable(panel):
    selected = []
    panel.selection_changed.connect(selected.append)
    assert row_ids(panel) == [None, 9, 4, 0]
    assert panel.tree.topLevelItem(0).text(0) == "Layer stack"
    hidden = panel.tree.topLevelItem(2)
    assert hidden.text(0) == "Hidden label"
    assert hidden.text(1) == "Text"
    assert hidden.checkState(0) == Qt.CheckState.Unchecked
    assert "saved raster preview" in hidden.toolTip(0)
    panel.tree.setCurrentItem(hidden)
    assert panel.selected_index() == 4
    assert selected == [4]
    panel.tree.setCurrentItem(panel.tree.topLevelItem(0))
    assert selected == [4, None]
    assert not panel.up_button.isEnabled()
    assert not panel.down_button.isEnabled()
    assert "saved TIFF copy" in panel.notice.text()


def test_checkbox_requests_visibility_without_committing_state_before_owner_accepts(panel):
    requests = []
    panel.visibility_requested.connect(lambda index, visible: requests.append((index, visible)))
    hidden = panel.tree.topLevelItem(2)
    hidden.setCheckState(0, Qt.CheckState.Checked)
    assert requests == [(4, True)]
    assert hidden.checkState(0) == Qt.CheckState.Unchecked
    panel.set_layers(example_stack(), visible={0, 4, 9}, selected=4)
    assert requests == [(4, True)]
    assert panel.tree.currentItem().checkState(0) == Qt.CheckState.Checked
    panel.tree.currentItem().setCheckState(0, Qt.CheckState.Unchecked)
    assert requests == [(4, True), (4, False)]
    assert panel.tree.currentItem().checkState(0) == Qt.CheckState.Checked


def test_move_requests_use_stable_id_and_selected_id_survives_accepted_reorder(panel):
    requests = []
    selected = []
    panel.move_requested.connect(lambda index, delta: requests.append((index, delta)))
    panel.selection_changed.connect(selected.append)
    panel.tree.setCurrentItem(panel.tree.topLevelItem(1))
    assert not panel.up_button.isEnabled()
    assert panel.down_button.isEnabled()
    QTest.mouseClick(panel.down_button, Qt.MouseButton.LeftButton)
    assert requests == [(9, 1)]
    assert row_ids(panel) == [None, 9, 4, 0]
    panel.set_layers(example_stack(), order=(4, 9, 0))
    assert panel.selected_index() == 9
    assert row_ids(panel) == [None, 4, 9, 0]
    assert selected == [9]
    QTest.mouseClick(panel.up_button, Qt.MouseButton.LeftButton)
    assert requests == [(9, 1), (9, -1)]
    panel.set_layers(example_stack(), order=(4, 0, 9))
    assert not panel.down_button.isEnabled()
    assert panel.up_button.isEnabled()
    panel.set_layers(example_stack(), selected=None)
    assert panel.selected_index() is None


def test_busy_blocks_checkboxes_order_and_selection_requests(panel):
    requests = []
    panel.visibility_requested.connect(lambda *args: requests.append(("visible", args)))
    panel.move_requested.connect(lambda *args: requests.append(("move", args)))
    panel.selection_changed.connect(lambda *args: requests.append(("select", args)))
    panel.tree.setCurrentItem(panel.tree.topLevelItem(2))
    requests.clear()
    panel.set_busy(True)
    assert not panel.tree.isEnabled()
    assert not panel.up_button.isEnabled() and not panel.down_button.isEnabled()
    item = panel.tree.currentItem()
    assert not item.flags() & Qt.ItemFlag.ItemIsUserCheckable
    item.setCheckState(0, Qt.CheckState.Checked)
    QTest.mouseClick(panel.up_button, Qt.MouseButton.LeftButton)
    panel.tree.setCurrentItem(panel.tree.topLevelItem(1))
    assert requests == []
    assert item.checkState(0) == Qt.CheckState.Unchecked
    panel.set_busy(False)
    assert panel.tree.isEnabled()
    assert panel.tree.currentItem().flags() & Qt.ItemFlag.ItemIsUserCheckable
    assert panel.down_button.isEnabled()


def test_edit_reason_blocks_saved_changes_but_keeps_layer_inspection_available(panel):
    requests = []
    selection = []
    panel.visibility_requested.connect(lambda *args: requests.append(args))
    panel.move_requested.connect(lambda *args: requests.append(args))
    panel.selection_changed.connect(selection.append)
    panel.set_edit_reason("Unsupported adjustment-layer blend")
    assert panel.tree.isEnabled()
    panel.tree.setCurrentItem(panel.tree.topLevelItem(2))
    assert selection == [4]
    assert not panel.up_button.isEnabled() and not panel.down_button.isEnabled()
    panel.tree.currentItem().setCheckState(0, Qt.CheckState.Checked)
    assert requests == []
    assert panel.tree.currentItem().checkState(0) == Qt.CheckState.Unchecked
    assert "Unsupported adjustment-layer blend" in panel.notice.text()
    panel.set_edit_reason(None)
    assert panel.up_button.isEnabled() and panel.down_button.isEnabled()


def test_rebuild_and_empty_stack_emit_no_edits_or_selection(panel):
    requests = []
    panel.visibility_requested.connect(lambda *args: requests.append(args))
    panel.move_requested.connect(lambda *args: requests.append(args))
    panel.selection_changed.connect(lambda *args: requests.append(args))
    panel.set_layers(example_stack(), order=(0, 9, 4), visible={4}, selected=4)
    assert panel.selected_index() == 4
    assert requests == []
    panel.set_layers(None)
    assert row_ids(panel) == [None]
    assert panel.selected_index() is None
    assert "No Photoshop layer records" in panel.notice.text()
    assert not panel.up_button.isEnabled() and not panel.down_button.isEnabled()
    assert requests == []


@pytest.mark.parametrize("kwargs", [
    {"order": (9, 9, 0)}, {"order": (9, 0)}, {"visible": {123}},
])
def test_invalid_layout_does_not_replace_current_state(panel, kwargs):
    before = row_ids(panel)
    with pytest.raises(ValueError):
        panel.set_layers(example_stack(), **kwargs)
    assert row_ids(panel) == before
