"""Layer selection and saveable visibility/order requests for the viewer."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)


LAYER_ID_ROLE = int(Qt.ItemDataRole.UserRole)
_KEEP_SELECTION = object()


class LayersPanel(QWidget):
    """Use stable source layer IDs, leaving mutations to the edit session.

    A checkbox is restored to its accepted value before requesting a change.
    The owner calls ``set_layers`` when the operation succeeds, so a failed
    operation cannot leave the panel showing an unsaved visibility change.
    """

    selection_changed = Signal(object)  # Source layer ID, or None for the stack.
    visibility_requested = Signal(int, bool)
    move_requested = Signal(int, int)  # Source layer ID and top-to-bottom delta.

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layers = {}
        self._order = []
        self._visible = set()
        self._busy = False
        self._edit_reason = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Layer", "Type"])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setColumnWidth(0, 185)
        self.tree.currentItemChanged.connect(self._selection_changed)
        self.tree.itemChanged.connect(self._visibility_changed)
        layout.addWidget(self.tree, 1)

        controls = QHBoxLayout()
        self.up_button = QPushButton("Move up")
        self.down_button = QPushButton("Move down")
        self.up_button.setToolTip("Move the selected layer toward the top of the saved stack")
        self.down_button.setToolTip("Move the selected layer toward the bottom of the saved stack")
        self.up_button.clicked.connect(lambda: self._request_move(-1))
        self.down_button.clicked.connect(lambda: self._request_move(1))
        controls.addWidget(self.up_button)
        controls.addWidget(self.down_button)
        layout.addLayout(controls)

        self.notice = QLabel()
        self.notice.setWordWrap(True)
        self.notice.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.notice)
        self.set_layers(None)

    def selected_index(self) -> int | None:
        item = self.tree.currentItem()
        return item.data(0, LAYER_ID_ROLE) if item is not None else None

    def set_layers(self, stack, order=None, visible=None, selected=_KEEP_SELECTION):
        """Display accepted state without emitting requests during a rebuild.

        ``order`` is top-to-bottom and contains source IDs. Omit ``selected``
        to keep the current source ID; pass None to select the layer stack.
        """
        selected_id = self.selected_index() if selected is _KEEP_SELECTION else selected
        layers = {layer.index: layer for layer in stack.layers} if stack is not None else {}
        if stack is not None and len(layers) != len(stack.layers):
            raise ValueError("Layer source IDs must be unique")
        displayed_order = list(stack.default_order if order is None else order) if stack is not None else []
        if len(displayed_order) != len(layers) or set(displayed_order) != set(layers):
            raise ValueError("Layer order must contain each source layer ID exactly once")
        displayed_visible = set(stack.default_visible if visible is None else visible) if stack is not None else set()
        if not displayed_visible <= set(layers):
            raise ValueError("Layer visibility contains an unknown source layer ID")

        self._layers = layers
        self._order = displayed_order
        self._visible = displayed_visible
        was_blocked = self.tree.blockSignals(True)
        try:
            self.tree.clear()
            stack_item = QTreeWidgetItem(["Layer stack", "Composite"])
            stack_item.setData(0, LAYER_ID_ROLE, None)
            stack_item.setToolTip(0, "Show the combined visible layer pixels")
            self.tree.addTopLevelItem(stack_item)
            chosen = stack_item
            for source_id in self._order:
                layer = layers[source_id]
                item = QTreeWidgetItem([layer.name, layer.kind])
                item.setData(0, LAYER_ID_ROLE, source_id)
                item.setCheckState(0, Qt.CheckState.Checked if source_id in self._visible else Qt.CheckState.Unchecked)
                notes = ["Select to inspect this layer even when it is hidden.",
                         f"Blend mode: {layer.blend_mode}"]
                notes.extend(str(issue) for issue in layer.issues)
                tooltip = "\n".join(notes)
                item.setToolTip(0, tooltip)
                item.setToolTip(1, tooltip)
                self.tree.addTopLevelItem(item)
                if source_id == selected_id:
                    chosen = item
            self.tree.setCurrentItem(chosen)
        finally:
            self.tree.blockSignals(was_blocked)
        self._update_controls()

    def set_busy(self, busy: bool):
        self._busy = bool(busy)
        self._update_controls()

    def set_edit_reason(self, reason: str | None):
        """Block save-affecting controls while leaving layer inspection usable."""
        self._edit_reason = reason
        self._update_controls()

    def _update_controls(self):
        editable = bool(self._order) and not self._busy and self._edit_reason is None
        self.tree.setEnabled(not self._busy)
        for row in range(1, self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(row)
            flags = item.flags()
            if editable:
                flags |= Qt.ItemFlag.ItemIsUserCheckable
            else:
                flags &= ~Qt.ItemFlag.ItemIsUserCheckable
            item.setFlags(flags)
        selected = self.selected_index()
        position = self._order.index(selected) if selected in self._order else -1
        self.up_button.setEnabled(editable and position > 0)
        self.down_button.setEnabled(editable and 0 <= position < len(self._order) - 1)
        if self._edit_reason:
            self.notice.setText(f"Layer changes cannot be saved: {self._edit_reason}")
        elif not self._order:
            self.notice.setText("No Photoshop layer records are available in this file.")
        else:
            self.notice.setText("Select any layer to inspect its pixels. Checkboxes and order affect the layer stack and saved TIFF copy. The original stays untouched.")

    def _selection_changed(self, _current, _previous):
        self._update_controls()
        if not self._busy:
            self.selection_changed.emit(self.selected_index())

    def _visibility_changed(self, item, column):
        source_id = item.data(0, LAYER_ID_ROLE)
        if column != 0 or source_id not in self._layers:
            return
        requested = item.checkState(0) == Qt.CheckState.Checked
        accepted = source_id in self._visible
        if requested == accepted:
            return
        was_blocked = self.tree.blockSignals(True)
        try:
            item.setCheckState(0, Qt.CheckState.Checked if accepted else Qt.CheckState.Unchecked)
        finally:
            self.tree.blockSignals(was_blocked)
        if not self._busy and self._edit_reason is None:
            self.visibility_requested.emit(source_id, requested)

    def _request_move(self, delta: int):
        source_id = self.selected_index()
        if self._busy or self._edit_reason is not None or source_id not in self._layers:
            return
        position = self._order.index(source_id)
        if 0 <= position + delta < len(self._order):
            self.move_requested.emit(source_id, delta)
