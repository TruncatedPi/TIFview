"""Explicit canvas dimensions and edge growth, without resampling."""
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFormLayout, QGridLayout, QLabel,
                               QPushButton, QVBoxLayout, QWidget)


class CanvasSizeDialog(QDialog):
    def __init__(self, doc, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.setWindowTitle("Canvas size")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Current canvas: {doc.width:,} × {doc.height:,} pixels"))
        form = QFormLayout()
        self.mode = QComboBox()
        self.mode.addItems(["Set new width and height", "Add to selected edges"])
        self.units = QComboBox()
        self.units.addItem("Pixels")
        if doc.metadata.get("dpi"):
            self.units.addItem("Millimetres")
        form.addRow("Change", self.mode)
        form.addRow("Units", self.units)
        layout.addLayout(form)
        self.size_form = QWidget()
        sizes = QFormLayout(self.size_form)
        self.fields = {}
        for name, label, value in (("width", "New width", doc.width), ("height", "New height", doc.height)):
            field = self.make_field(value)
            self.fields[name] = field
            sizes.addRow(label, field)
        layout.addWidget(self.size_form)
        self.anchor_widget = QWidget()
        anchors = QVBoxLayout(self.anchor_widget)
        anchors.addWidget(QLabel("Anchor: where the existing image stays"))
        grid = QGridLayout()
        self.anchor = QButtonGroup(self)
        for index, text in enumerate(("↖", "↑", "↗", "←", "●", "→", "↙", "↓", "↘")):
            button = QPushButton(text)
            button.setCheckable(True)
            button.setToolTip(("Top left", "Top centre", "Top right", "Centre left", "Centre", "Centre right",
                               "Bottom left", "Bottom centre", "Bottom right")[index])
            self.anchor.addButton(button, index)
            grid.addWidget(button, index // 3, index % 3)
        self.anchor.button(4).setChecked(True)
        anchors.addLayout(grid)
        layout.addWidget(self.anchor_widget)
        self.edge_form = QWidget()
        edges = QFormLayout(self.edge_form)
        for name in ("left", "top", "right", "bottom"):
            field = self.make_field(0)
            self.fields[name] = field
            edges.addRow(name.title(), field)
        layout.addWidget(self.edge_form)
        self.background = QComboBox()
        self.background.addItems(["Transparent", "White"])
        layout.addWidget(QLabel("New canvas area"))
        layout.addWidget(self.background)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        note = QLabel("Canvas growth keeps the original pixel size and print resolution. "
                      "Use the anchor or edge amounts to choose where space is added. "
                      "Cropping is not supported yet. Ctrl+Z undoes the change.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._unit = 0
        self.units.currentIndexChanged.connect(self.change_units)
        self.mode.currentIndexChanged.connect(self.update_summary)
        self.anchor.idClicked.connect(self.update_summary)
        for field in self.fields.values():
            field.valueChanged.connect(self.update_summary)
        self.update_summary()
        self.resize(460, 520)

    @staticmethod
    def make_field(value):
        field = QDoubleSpinBox()
        field.setDecimals(0)
        field.setRange(0, 300000)
        field.setValue(value)
        field.setKeyboardTracking(False)
        return field

    def scale(self, name, unit):
        if unit == 0:
            return 1
        axis = 0 if name in ("width", "left", "right") else 1
        return self.doc.metadata["dpi"][axis] / 25.4

    def change_units(self, unit):
        values = {name: round(field.value() * self.scale(name, self._unit)) for name, field in self.fields.items()}
        self._unit = unit
        for name, field in self.fields.items():
            field.blockSignals(True)
            field.setDecimals(3 if unit else 0)
            field.setMaximum(300000 / self.scale(name, unit))
            field.setValue(values[name] / self.scale(name, unit))
            field.blockSignals(False)
        self.update_summary()

    def canvas_geometry(self):
        values = {name: round(field.value() * self.scale(name, self._unit)) for name, field in self.fields.items()}
        if self.mode.currentIndex() == 1:
            return (self.doc.width + values["left"] + values["right"],
                    self.doc.height + values["top"] + values["bottom"], values["left"], values["top"])
        width, height = values["width"], values["height"]
        row, column = divmod(self.anchor.checkedId(), 3)
        return width, height, (width - self.doc.width) * column // 2, (height - self.doc.height) * row // 2

    def update_summary(self, *_):
        edges = self.mode.currentIndex() == 1
        self.edge_form.setVisible(edges)
        self.size_form.setVisible(not edges)
        self.anchor_widget.setVisible(not edges)
        width, height, left, top = self.canvas_geometry()
        valid = (self.doc.width <= width <= 300000 and self.doc.height <= height <= 300000
                 and (width, height) != (self.doc.width, self.doc.height))
        if valid:
            text = (f"New canvas: {width:,} × {height:,} pixels\n"
                    f"Added pixels — left {left:,}, top {top:,}, "
                    f"right {width - self.doc.width - left:,}, bottom {height - self.doc.height - top:,}")
            if self.doc.metadata.get("dpi"):
                dx, dy = self.doc.metadata["dpi"]
                text += f"\n{width * 25.4 / dx:.3f} × {height * 25.4 / dy:.3f} mm"
        else:
            text = "Increase at least one dimension; the new canvas must contain the whole original image."
        self.summary.setText(text)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(valid)
