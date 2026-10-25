"""One SVG cut/artwork overlay, calibrated to the displayed image in mm."""
from dataclasses import replace
import math
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QDoubleSpinBox, QFormLayout,
                               QLabel, QPushButton, QVBoxLayout, QWidget)

from .svg import Placement
from .history import next_order


class VectorsPanel(QWidget):
    import_requested = Signal()
    load_requested = Signal()
    save_requested = Signal()
    export_requested = Signal()
    placement_changed = Signal(object)
    placement_edited = Signal()
    remove_requested = Signal()
    expand_requested = Signal()
    layer_requested = Signal()

    def __init__(self):
        super().__init__()
        self.artwork = None
        self.placement = None
        self._updating = False
        self.dirty = False
        self._history = []
        self._history_position = -1
        self._saved = None
        self._image_binding = self._saved_binding = None
        self._orders = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 0)
        self.name = QLabel("No SVG loaded")
        self.name.setWordWrap(True)
        layout.addWidget(self.name)
        self.calibration = QLabel()
        self.calibration.setWordWrap(True)
        layout.addWidget(self.calibration)
        self.import_button = QPushButton("Import SVG…")
        self.import_button.clicked.connect(self.import_requested)
        layout.addWidget(self.import_button)
        self.visible = QCheckBox("Show SVG overlay")
        self.visible.toggled.connect(self.change)
        layout.addWidget(self.visible)
        self.form = QWidget()
        form = QFormLayout(self.form)
        self.fields = {}
        for key, label, low, high, suffix in (
                ("x_mm", "X from left", -100000, 100000, " mm"),
                ("y_mm", "Y from top", -100000, 100000, " mm"),
                ("width_mm", "Width", .001, 100000, " mm"),
                ("height_mm", "Height", .001, 100000, " mm"),
                ("angle", "Rotation", -360, 360, "°")):
            field = QDoubleSpinBox()
            field.setDecimals(3)
            field.setRange(low, high)
            field.setSuffix(suffix)
            field.setKeyboardTracking(False)
            field.valueChanged.connect(lambda _value, name=key: self.change(name))
            form.addRow(label, field)
            self.fields[key] = field
        self.lock = QCheckBox("Keep SVG proportions")
        self.lock.setChecked(True)
        form.addRow(self.lock)
        layout.addWidget(self.form)
        self.expand_button = QPushButton("Expand canvas to SVG…")
        self.expand_button.clicked.connect(self.expand_requested)
        layout.addWidget(self.expand_button)
        self.layer_button = QPushButton("Add SVG as layer…")
        self.layer_button.clicked.connect(self.layer_requested)
        layout.addWidget(self.layer_button)
        self.save_button = QPushButton("Save alignment job…")
        self.save_button.clicked.connect(self.save_requested)
        self.load_button = QPushButton("Open alignment job…")
        self.load_button.clicked.connect(self.load_requested)
        self.export_button = QPushButton("Export aligned SVG…")
        self.export_button.clicked.connect(self.export_requested)
        self.remove_button = QPushButton("Remove SVG")
        self.remove_button.clicked.connect(self.remove_requested)
        for button in (self.save_button, self.load_button, self.export_button, self.remove_button):
            layout.addWidget(button)
        self.notice = QLabel("Open an image, then import a self-contained SVG. Text must be converted to paths. "
                             "Position and size use the image's print resolution. Add SVG as layer renders a new "
                             "Photoshop pixel layer for TIFF saving. The original SVG can still be exported separately.")
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        layout.addStretch()
        self.update_controls(False)

    def update_controls(self, enabled):
        self.import_button.setEnabled(enabled)
        self.load_button.setEnabled(enabled)
        present = enabled and self.artwork is not None
        for control in (self.form, self.visible, self.save_button, self.export_button, self.remove_button, self.expand_button, self.layer_button):
            control.setEnabled(present)

    def set_artwork(self, artwork=None, placement=None, dirty=False):
        self.artwork = artwork
        self._history = [placement] if placement is not None else []
        self._orders = [0] if placement is not None else []
        self._history_position = 0 if placement is not None else -1
        self._saved = None if dirty else placement
        self._saved_binding = self._image_binding
        self._display(placement)

    def _display(self, placement):
        self._updating = True
        self.placement = placement
        matches_saved = (placement is not None and self._saved is not None
                         and placement.visible == self._saved.visible
                         and all(math.isclose(getattr(placement, key), getattr(self._saved, key),
                                              rel_tol=0, abs_tol=1e-9) for key in self.fields))
        self.dirty = placement is not None and (not matches_saved or self._image_binding != self._saved_binding)
        self.name.setText(self.artwork.name if self.artwork is not None else "No SVG loaded")
        self.visible.setChecked(placement.visible if placement is not None else False)
        if placement is not None:
            for key, field in self.fields.items():
                field.setValue(getattr(placement, key))
        self._updating = False

    def translate_canvas(self, x_mm, y_mm):
        if self.placement is None or not (x_mm or y_mm):
            return
        def moved(placement):
            return replace(placement, x_mm=placement.x_mm + x_mm, y_mm=placement.y_mm + y_mm)
        self._history = [moved(p) for p in self._history]
        self._display(moved(self.placement))

    def bind_image(self, state):
        """A saved job binds image pixels/size as well as placement coordinates."""
        self._image_binding = state
        self._display(self.placement)

    def discard_redo(self):
        del self._history[self._history_position + 1:]
        del self._orders[self._history_position + 1:]

    def history_order(self, forward=False):
        if (self.can_redo if forward else self.can_undo):
            return self._orders[self._history_position + (1 if forward else 0)]
        return None

    @property
    def can_undo(self):
        return self._history_position > 0

    @property
    def can_redo(self):
        return 0 <= self._history_position < len(self._history) - 1

    def undo(self):
        if self.can_undo:
            self._history_position -= 1
            self._display(self._history[self._history_position])
            self.placement_changed.emit(self.placement)

    def redo(self):
        if self.can_redo:
            self._history_position += 1
            self._display(self._history[self._history_position])
            self.placement_changed.emit(self.placement)

    def mark_saved(self):
        self._saved = self.placement
        self._saved_binding = self._image_binding
        self.dirty = False

    def change(self, changed=None):
        if self._updating or self.placement is None:
            return
        values = {key: getattr(self.placement, key) for key in self.fields}
        if isinstance(changed, str):
            values[changed] = self.fields[changed].value()
        if self.lock.isChecked() and changed in ("width_mm", "height_mm"):
            ratio = self.placement.width_mm / self.placement.height_mm
            if changed == "width_mm":
                values["height_mm"] = values["width_mm"] / ratio
            else:
                values["width_mm"] = values["height_mm"] * ratio
        try:
            placement = Placement(**values, visible=self.visible.isChecked())
        except ValueError:
            self._display(self.placement)
            return
        if placement == self.placement:
            return
        self.discard_redo()
        self._history.append(placement)
        self._orders.append(next_order())
        # Alignment records contain five numbers and a boolean, not pixels.
        self._history = self._history[-256:]
        self._orders = self._orders[-256:]
        self._history_position = len(self._history) - 1
        self._display(placement)
        self.placement_edited.emit()
        self.placement_changed.emit(placement)
