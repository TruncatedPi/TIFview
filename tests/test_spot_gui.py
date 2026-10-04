"""Spot management must move native masks and keep view choices with a channel."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

import tifview.app as gui
from tifview.reader import load_image
from tifview.render import render
from tools.make_demo import make_demo


@pytest.fixture(autouse=True)
def fail_on_unexpected_modal(monkeypatch):
    def warning(_parent, title, message):
        raise AssertionError(f"{title}: {message}")
    monkeypatch.setattr(QMessageBox, "warning", warning)


def opened(tmp_path):
    app = QApplication.instance() or QApplication([])
    gui.configure_application(app)
    path = tmp_path / "spots.tif"
    make_demo(path)
    doc = load_image(path)
    window = gui.ViewerWindow()
    window.show()
    app.processEvents()
    window.accept_document(doc, render(doc))
    app.processEvents()
    return app, window


def cleanup(app, window):
    window.edits.mark_saved()
    window.close()
    app.processEvents()


def dialog_reply(monkeypatch, values):
    monkeypatch.setattr(gui.SpotDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(gui.SpotDialog, "values", lambda self: values)


def test_spot_actions_enable_only_safe_targets_and_disable_while_busy(tmp_path):
    app, window = opened(tmp_path)
    try:
        assert window.new_spot_action.isEnabled()
        assert not window.duplicate_spot_action.isEnabled()
        for row in (1, 2, 3, 6):
            window.channels.setCurrentItem(window.channels.topLevelItem(row))
            assert not window.delete_spot_action.isEnabled()
            assert not window.spot_properties_action.isEnabled()
        window.channels.setCurrentItem(window.channels.topLevelItem(4))
        assert window.duplicate_spot_action.isEnabled()
        assert not window.move_spot_up_action.isEnabled()
        assert window.move_spot_down_action.isEnabled()
        window.channels.setCurrentItem(window.channels.topLevelItem(5))
        assert window.move_spot_up_action.isEnabled()
        assert not window.move_spot_down_action.isEnabled()
        for field in ("loader", "saver", "previewer"):
            setattr(window, field, object())
            window.set_controls()
            for action in (window.new_spot_action, window.duplicate_spot_action, window.spot_properties_action,
                           window.delete_spot_action, window.move_spot_up_action, window.move_spot_down_action):
                assert not action.isEnabled()
            assert window.perform_spot_change("add_spot", "Blocked") is False
            assert not window.edits.dirty
            setattr(window, field, None)
            window.set_controls()
    finally:
        cleanup(app, window)


def test_new_spot_starts_empty_can_paint_and_undo_redo_structure(tmp_path, monkeypatch):
    app, window = opened(tmp_path)
    original = window.doc.samples.copy()
    dialog_reply(monkeypatch, ("White back", (255, 255, 255), 100))
    try:
        window.channels.setCurrentItem(window.channels.topLevelItem(4))
        window.invert.setChecked(True)
        window.new_spot_action.trigger()
        index = window.selected_index()
        assert window.doc.channels[index].name == "White back"
        assert window.doc.channel_label(index) == "3. White back"
        assert window.channels.currentItem().text(0) == "3. White back"
        assert window.channels.topLevelItemCount() == 8
        assert "7 samples" in window.dimensions.text()
        assert np.all(window.doc.samples[..., index] == 255)
        assert not window.invert.isChecked()
        assert window.view.image_item.pixmap().toImage().pixelColor(370, 85).red() == 255
        window.fill.setChecked(True)
        window.apply_drawing("Box", (330., 50.), (410., 120.))
        assert window.view.image_item.pixmap().toImage().pixelColor(370, 85).red() == 0
        window.undo()
        assert np.all(window.doc.samples[..., window.selected_index()] == 255)
        window.undo()
        assert window.channels.topLevelItemCount() == 7
        np.testing.assert_array_equal(window.doc.samples, original)
        window.redo()
        assert window.channels.topLevelItemCount() == 8
        window.redo()
        index = next(c.index for c in window.doc.channels if c.name == "White back")
        assert window.doc.samples[85, 370, index] == 0
        np.testing.assert_array_equal(window.edits.original.samples, original)
    finally:
        cleanup(app, window)


def test_reorder_keeps_masks_selection_visibility_and_display_colors_by_identity(tmp_path):
    app, window = opened(tmp_path)
    original = window.doc.samples.copy()
    try:
        window.channels.topLevelItem(4).setCheckState(0, Qt.CheckState.Checked)
        window.channels.topLevelItem(6).setCheckState(0, Qt.CheckState.Checked)
        window.colors = {3: (10, 20, 30), 5: (90, 80, 70)}
        window.channels.setCurrentItem(window.channels.topLevelItem(4))
        selected_id = window.selected_channel_id()
        window.move_spot_down_action.trigger()
        assert window.selected_index() == 4
        assert window.selected_channel_id() == selected_id
        assert [c.name for c in window.doc.channels[3:]] == ["Varnish", "White Ink", "Saved selection"]
        assert [window.channels.topLevelItem(i).text(0) for i in (4, 5, 6)] == [
            "1. Varnish", "2. White Ink", "Saved selection"]
        assert window.visible_indices() == {0, 1, 2, 4, 5}
        assert window.colors == {4: (10, 20, 30), 5: (90, 80, 70)}
        np.testing.assert_array_equal(window.doc.samples[..., 4], original[..., 3])
        np.testing.assert_array_equal(window.doc.samples[..., 3], original[..., 4])
        np.testing.assert_array_equal(window.doc.samples[..., 5], original[..., 5])
        window.undo()
        assert window.selected_index() == 3
        assert window.selected_channel_id() == selected_id
        assert window.visible_indices() == {0, 1, 2, 3, 5}
        assert window.colors == {3: (10, 20, 30), 5: (90, 80, 70)}
        np.testing.assert_array_equal(window.doc.samples, original)
        window.redo()
        assert window.selected_index() == 4
        assert window.selected_channel_id() == selected_id
        np.testing.assert_array_equal(window.edits.original.samples, original)
    finally:
        cleanup(app, window)


def test_delete_confirmation_cancel_and_undo_preserve_saved_alpha_preferences(tmp_path, monkeypatch):
    app, window = opened(tmp_path)
    original = window.doc.samples.copy()
    prompts = []

    def cancel(*args):
        prompts.append(args)
        return QMessageBox.StandardButton.Cancel

    try:
        window.channels.topLevelItem(4).setCheckState(0, Qt.CheckState.Checked)
        window.channels.topLevelItem(6).setCheckState(0, Qt.CheckState.Checked)
        window.colors = {3: (1, 2, 3), 5: (4, 5, 6)}
        window.channels.setCurrentItem(window.channels.topLevelItem(4))
        monkeypatch.setattr(QMessageBox, "question", cancel)
        window.delete_spot_action.trigger()
        assert prompts[0][-1] == QMessageBox.StandardButton.Cancel
        assert not window.edits.dirty
        np.testing.assert_array_equal(window.doc.samples, original)
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
        window.delete_spot_action.trigger()
        assert window.selected_index() is None
        assert window.channels.topLevelItemCount() == 6
        assert window.doc.channels[4].name == "Saved selection"
        assert window.doc.channels[4].kind == "Alpha mask"
        assert window.colors == {4: (4, 5, 6)}
        np.testing.assert_array_equal(window.doc.samples[..., 4], original[..., 5])
        window.channels.setCurrentItem(window.channels.topLevelItem(5))
        saved_alpha_id = window.selected_channel_id()
        window.undo()
        assert window.selected_channel_id() == saved_alpha_id
        assert window.selected_index() == 5
        assert window.visible_indices() == {0, 1, 2, 3, 5}
        assert window.colors == {3: (1, 2, 3), 5: (4, 5, 6)}
        np.testing.assert_array_equal(window.doc.samples, original)
        window.redo()
        assert window.selected_channel_id() == saved_alpha_id
        assert window.selected_index() == 4
    finally:
        cleanup(app, window)


def test_properties_are_saved_metadata_and_display_override_is_separate(tmp_path, monkeypatch):
    app, window = opened(tmp_path)
    original = window.doc.samples.copy()
    dialog_reply(monkeypatch, ("White back", (32, 64, 128), 55))
    try:
        window.channels.setCurrentItem(window.channels.topLevelItem(4))
        window.colors[3] = (250, 200, 100)
        window.spot_properties_action.trigger()
        assert window.doc.channels[3].name == "White back"
        assert window.doc.channels[3].color == (32, 64, 128)
        assert window.doc.channels[3].display.opacity == 55
        assert window.channels.currentItem().text(0) == "1. White back"
        assert "Solidity: 55%" in window.channel_info.text()
        assert window.colors[3] == (250, 200, 100)
        np.testing.assert_array_equal(window.doc.samples, original)
        window.undo()
        assert window.channels.currentItem().text(0) == "1. White Ink"
        assert window.colors[3] == (250, 200, 100)
        window.redo()
        assert window.channels.currentItem().text(0) == "1. White back"
        np.testing.assert_array_equal(window.edits.original.samples, original)
    finally:
        cleanup(app, window)


def test_duplicate_copies_full_mask_and_preserves_unchanged_saved_properties(tmp_path, monkeypatch):
    app, window = opened(tmp_path)
    source = window.doc.channels[3]
    original = window.doc.samples.copy()
    dialog_reply(monkeypatch, ("White Ink copy", source.color, source.display.opacity))
    try:
        window.channels.setCurrentItem(window.channels.topLevelItem(4))
        window.duplicate_spot_action.trigger()
        index = window.selected_index()
        duplicate = window.doc.channels[index]
        assert duplicate.name == "White Ink copy"
        assert duplicate.display == source.display
        assert duplicate.color == source.color
        np.testing.assert_array_equal(window.doc.samples[..., index], original[..., 3])
        assert window.channels.currentItem().text(0) == "3. White Ink copy"
        window.fill.setChecked(True)
        window.apply_drawing("Box", (330., 50.), (410., 120.))
        assert window.doc.samples[85, 370, index] == 0
        np.testing.assert_array_equal(window.doc.samples[..., 3], original[..., 3])
        np.testing.assert_array_equal(window.edits.original.samples, original)
    finally:
        cleanup(app, window)


def test_spot_dialog_rejects_empty_name_and_describes_saved_properties():
    app = QApplication.instance() or QApplication([])
    gui.configure_application(app)
    dialog = gui.SpotDialog("New spot", "White", (255, 255, 255), 100, "Empty mask: no ink.")
    try:
        assert dialog.values() == ("White", (255, 255, 255), 100)
        assert dialog.solidity.minimum() == 0 and dialog.solidity.maximum() == 100
        buttons = dialog.findChild(gui.QDialogButtonBox)
        assert buttons.button(gui.QDialogButtonBox.StandardButton.Ok).isEnabled()
        dialog.name.setText("   ")
        assert not buttons.button(gui.QDialogButtonBox.StandardButton.Ok).isEnabled()
        labels = " ".join(label.text() for label in dialog.findChildren(gui.QLabel))
        assert "do not change mask pixels" in labels
        assert "printer ink density" in labels
    finally:
        dialog.close()
