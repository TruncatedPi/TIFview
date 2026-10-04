from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QSize
from PySide6.QtGui import QAction, QColor, QFont, QFontDatabase, QIcon, QImage, QPainter, QPixmap, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView, QHBoxLayout,
    QLabel, QMainWindow, QMessageBox, QPushButton, QSlider, QSplitter, QTextEdit,
    QToolBar, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .model import ImageDocument
from .reader import load_image
from .render import render


class ImageView(QGraphicsView):
    zoom_changed = Signal(float)
    pixel_hovered = Signal(int, int)

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.image_item = QGraphicsPixmapItem()
        self.image_item.setTransformationMode(Qt.TransformationMode.FastTransformation)
        self.scene().addItem(self.image_item)
        self.setBackgroundBrush(QColor("#34383d"))
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setMouseTracking(True)
        self.fitted = True
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)

    def set_image(self, array: np.ndarray, reset: bool = False):
        h, w, _ = array.shape
        image = QImage(array.data, w, h, array.strides[0], QImage.Format.Format_RGB888).copy()
        self.image_item.setPixmap(QPixmap.fromImage(image))
        self.scene().setSceneRect(0, 0, w, h)
        if reset or self.fitted:
            self.fit_image()

    def emit_zoom(self):
        self.zoom_changed.emit(self.transform().m11() * self.viewport().devicePixelRatioF())

    def fit_image(self):
        if not self.image_item.pixmap().isNull():
            self.fitInView(self.image_item, Qt.AspectRatioMode.KeepAspectRatio)
        self.fitted = True
        self.emit_zoom()

    def actual_pixels(self):
        self.resetTransform()
        ratio = self.viewport().devicePixelRatioF()
        self.scale(1 / ratio, 1 / ratio)  # One source sample per physical display pixel.
        self.fitted = False
        self.emit_zoom()

    def zoom_by(self, factor: float):
        physical = self.transform().m11() * self.viewport().devicePixelRatioF()
        target = min(64., max(.005, physical * factor))
        self.scale(target / physical, target / physical)
        self.fitted = False
        self.emit_zoom()

    def wheelEvent(self, event):
        self.zoom_by(1.2 ** (event.angleDelta().y() / 120))
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fitted:
            self.fit_image()

    def mouseMoveEvent(self, event):
        point = self.mapToScene(event.position().toPoint())
        # floor, since int(-0.5) would incorrectly inspect the first pixel.
        self.pixel_hovered.emit(int(np.floor(point.x())), int(np.floor(point.y())))
        super().mouseMoveEvent(event)


class Loader(QThread):
    loaded = Signal(object, object)
    failed = Signal(str)

    def __init__(self, path: str):
        super().__init__()
        self.path = path

    def run(self):
        try:
            doc = load_image(self.path)
            visible = {c.index for c in doc.channels[:doc.base_count]}
            visible |= {c.index for c in doc.channels if c.kind == "Transparency"}
            self.loaded.emit(doc, render(doc, visible=visible))
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class ViewerWindow(QMainWindow):
    def __init__(self, initial_path: str | None = None):
        super().__init__()
        self.doc: ImageDocument | None = None
        self.loader: Loader | None = None
        self.colors: dict[int, tuple[int, int, int]] = {}
        self.setWindowTitle("TIFview — read-only channel inspector")
        self.resize(1200, 800)
        self.setMinimumSize(820, 540)
        self.setAcceptDrops(True)
        toolbar = QToolBar("Viewer")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        self.open_action = QAction("Open…", self)
        self.open_action.setShortcut(QKeySequence.StandardKey.Open)
        self.open_action.triggered.connect(self.choose_file)
        toolbar.addAction(self.open_action)
        toolbar.addSeparator()
        for label, shortcut, callback in [
            ("Fit", "F", self.fit), ("100%", "1", self.actual),
            ("−", "-", lambda: self.view.zoom_by(1 / 1.25)),
            ("+", "+", lambda: self.view.zoom_by(1.25)),
        ]:
            action = QAction(label, self)
            action.setShortcut(shortcut)
            action.triggered.connect(callback)
            toolbar.addAction(action)
        self.zoom_label = QLabel(" 100% ")
        toolbar.addWidget(self.zoom_label)
        toolbar.addSeparator()
        self.metadata_action = QAction("File details", self)
        self.metadata_action.triggered.connect(self.show_details)
        self.metadata_action.setEnabled(False)
        toolbar.addAction(self.metadata_action)

        sidebar = QWidget()
        layout = QVBoxLayout(sidebar)
        self.file_label = QLabel("Open a TIFF to inspect its channels")
        self.file_label.setWordWrap(True)
        self.file_label.setStyleSheet("font-weight: 600; font-size: 14px;")
        layout.addWidget(self.file_label)
        self.dimensions = QLabel("TIFF · PNG · JPEG · BMP · WebP")
        self.dimensions.setWordWrap(True)
        layout.addWidget(self.dimensions)
        layout.addWidget(QLabel("Channels"))
        self.channels = QTreeWidget()
        self.channels.setHeaderLabels(["Channel", "Type"])
        self.channels.setRootIsDecorated(False)
        self.channels.setIconSize(QSize(40, 24))
        self.channels.setColumnWidth(0, 185)
        self.channels.currentItemChanged.connect(self.selection_changed)
        self.channels.itemChanged.connect(lambda *_: self.refresh())
        layout.addWidget(self.channels, 1)
        self.style_combo = QComboBox()
        self.style_combo.addItems(["Grayscale channel", "Coloured mask"])
        self.style_combo.currentIndexChanged.connect(lambda *_: self.refresh())
        layout.addWidget(self.style_combo)
        self.invert = QCheckBox("Invert selected channel display")
        self.invert.toggled.connect(lambda *_: self.refresh())
        layout.addWidget(self.invert)
        self.overlays = QCheckBox("Show extra-channel overlays")
        self.overlays.toggled.connect(lambda *_: self.refresh())
        layout.addWidget(self.overlays)
        self.color_button = QPushButton("Choose display colour…")
        self.color_button.clicked.connect(self.choose_color)
        layout.addWidget(self.color_button)
        opacity_row = QHBoxLayout()
        opacity_row.addWidget(QLabel("Overlay opacity"))
        self.opacity_label = QLabel("65%")
        opacity_row.addWidget(self.opacity_label)
        layout.addLayout(opacity_row)
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(0, 100)
        self.opacity.setValue(65)
        self.opacity.valueChanged.connect(self.opacity_changed)
        layout.addWidget(self.opacity)
        self.channel_info = QLabel("Select a channel for grayscale. Tick channels to change the composite.")
        self.channel_info.setWordWrap(True)
        layout.addWidget(self.channel_info)
        self.notice = QLabel("Read-only prototype\nPhotoshop spot-channel validation pending")
        self.notice.setWordWrap(True)
        self.notice.setStyleSheet("background: #fff0cb; color: #563a00; padding: 8px;")
        layout.addWidget(self.notice)
        self.view = ImageView()
        self.view.zoom_changed.connect(lambda value: self.zoom_label.setText(f" {value * 100:.1f}% "))
        self.view.pixel_hovered.connect(self.inspect_pixel)
        self.splitter = QSplitter()
        self.splitter.addWidget(sidebar)
        self.splitter.addWidget(self.view)
        self.splitter.setSizes([330, 870])
        self.setCentralWidget(self.splitter)
        self.statusBar().showMessage("Open or drop an image. Wheel: zoom · Drag: pan · F: fit · 1: actual pixels")
        self.set_controls()
        if initial_path:
            QTimer.singleShot(0, lambda: self.open_path(initial_path))

    def fit(self):
        self.view.fit_image()

    def actual(self):
        self.view.actual_pixels()

    def choose_file(self):
        filename, _ = QFileDialog.getOpenFileName(
            self, "Open printing image", str(self.doc.path.parent) if self.doc else "",
            "Images (*.tif *.tiff *.png *.jpg *.jpeg *.bmp *.webp *.gif);;All files (*)")
        if filename:
            self.open_path(filename)

    def open_path(self, path: str):
        if self.loader is not None:
            return
        self.open_action.setEnabled(False)
        self.statusBar().showMessage(f"Reading {Path(path).name}…")
        self.loader = Loader(path)
        self.loader.loaded.connect(self.accept_document)
        self.loader.failed.connect(self.load_failed)
        self.loader.finished.connect(self.load_finished)
        self.loader.start()

    def load_finished(self):
        loader, self.loader = self.loader, None
        if loader:
            loader.deleteLater()
        self.open_action.setEnabled(True)

    def load_failed(self, message: str):
        QMessageBox.warning(self, "Could not open image", message)
        self.statusBar().showMessage("Image could not be opened")

    def accept_document(self, doc: ImageDocument, preview: np.ndarray):
        self.doc = doc
        self.colors = {}
        self.file_label.setText(doc.path.name)
        self.file_label.setToolTip(str(doc.path))
        self.dimensions.setText(f"{doc.width:,} × {doc.height:,} px · {doc.bits}-bit {doc.color_mode}\n"
                                f"{len(doc.channels)} samples · {doc.samples.nbytes / 2**20:.1f} MiB raw")
        self.setWindowTitle(f"{doc.path.name} — TIFview (read-only)")
        self.channels.blockSignals(True)
        self.channels.clear()
        composite = QTreeWidgetItem(["Composite", doc.color_mode])
        composite.setData(0, Qt.ItemDataRole.UserRole, -1)
        self.channels.addTopLevelItem(composite)
        for channel in doc.channels:
            item = QTreeWidgetItem([channel.name, channel.kind])
            item.setData(0, Qt.ItemDataRole.UserRole, channel.index)
            item.setCheckState(0, Qt.CheckState.Checked if channel.index < doc.base_count or
                               channel.kind == "Transparency" else Qt.CheckState.Unchecked)
            item.setToolTip(0, f"Stored sample {channel.index}\n{channel.evidence}")
            item.setToolTip(1, channel.evidence)
            plane = doc.display_samples[::max(1, doc.height // 80), ::max(1, doc.width // 120), channel.index]
            gray = np.rint(plane.astype(np.float32) / doc.maximum * 255).astype(np.uint8)
            if (doc.color_mode == "CMYK" and channel.index < doc.base_count) or (
                    doc.color_mode == "WhiteIsZero" and channel.index == 0):
                gray = 255 - gray
            gray = np.ascontiguousarray(gray)
            thumbnail = QImage(gray.data, gray.shape[1], gray.shape[0], gray.strides[0],
                               QImage.Format.Format_Grayscale8).copy()
            item.setIcon(0, QIcon(QPixmap.fromImage(thumbnail).scaled(
                40, 24, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)))
            self.channels.addTopLevelItem(item)
        self.channels.setCurrentItem(composite)
        self.channels.blockSignals(False)
        self.invert.blockSignals(True)
        self.invert.setChecked(False)
        self.invert.blockSignals(False)
        self.overlays.blockSignals(True)
        self.overlays.setChecked(False)
        self.overlays.blockSignals(False)
        self.metadata_action.setEnabled(True)
        messages = list(doc.warnings)
        if any(c.kind == "Unknown" for c in doc.channels):
            messages.insert(0, "Some extra samples have unknown types. File details explains the evidence.")
        summary = []
        if doc.photoshop_resources:
            summary.append("Photoshop channel interpretation is experimental.")
        if any(c.kind == "Unknown" for c in doc.channels):
            summary.append("Some channel types are unknown.")
        if doc.metadata.get("has_photoshop_layers"):
            summary.append("Viewing saved composite and channels.")
        if messages:
            summary.append("See File details for reading and colour notes.")
        self.notice.setText("\n".join(summary))
        self.notice.setToolTip("\n\n".join(messages))
        self.notice.setVisible(bool(messages))
        self.set_controls()
        self.view.set_image(preview, reset=True)
        self.statusBar().showMessage("Source untouched · Wheel: zoom · Drag: pan · F: fit · 1: actual pixels")

    def selected_index(self) -> int | None:
        item = self.channels.currentItem()
        value = item.data(0, Qt.ItemDataRole.UserRole) if item else -1
        return value if value is not None and value >= 0 else None

    def set_controls(self):
        selected = self.selected_index() if self.doc else None
        self.style_combo.setEnabled(selected is not None)
        self.invert.setEnabled(selected is not None)
        self.color_button.setEnabled(selected is not None)
        self.overlays.setEnabled(self.doc is not None and selected is None)
        self.opacity.setEnabled(self.doc is not None)
        if self.doc and selected is not None:
            c = self.doc.channels[selected]
            text = f"Sample {c.index}: {c.evidence}\n"
            if c.display:
                label = "Solidity" if c.kind == "Spot" else "Saved opacity"
                text += f"{label}: {c.display.opacity}% · colour space {c.display.color_space}\n"
            if c.kind == "Spot":
                text += "Black = ink in grayscale. Overlay is an approximate mask preview."
            elif c.kind == "Unknown":
                text += "Raw grayscale; type and ink polarity are unknown."
            elif self.doc.color_mode == "CMYK" and selected < self.doc.base_count:
                text += "Grayscale shows ink as dark. Pixel inspector reports stored TIFF values."
            else:
                text += "Pixel inspector reports stored values."
            self.channel_info.setText(text)
        else:
            self.channel_info.setText("Tick process channels to change the composite. Tick extra channels "
                                      "and enable overlays to compare masks. Select a row for grayscale.")

    def selection_changed(self, *_):
        self.invert.blockSignals(True)
        self.invert.setChecked(False)
        self.invert.blockSignals(False)
        self.set_controls()
        self.refresh()

    def opacity_changed(self, value: int):
        self.opacity_label.setText(f"{value}%")
        self.refresh()

    def visible_indices(self) -> set[int]:
        return {self.channels.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
                for i in range(1, self.channels.topLevelItemCount())
                if self.channels.topLevelItem(i).checkState(0) == Qt.CheckState.Checked}

    def refresh(self):
        if self.doc is None:
            return
        try:
            preview = render(self.doc, self.selected_index(), self.style_combo.currentIndex() == 1,
                             self.visible_indices(), self.overlays.isChecked(), self.opacity.value() / 100,
                             self.invert.isChecked(), self.colors)
            self.view.set_image(preview)
        except Exception as exc:
            self.statusBar().showMessage(f"Preview error: {exc}")

    def choose_color(self):
        index = self.selected_index()
        if self.doc is None or index is None:
            return
        initial = self.colors.get(index, self.doc.channels[index].color)
        chosen = QColorDialog.getColor(QColor(*initial), self, "Display colour (source unchanged)")
        if chosen.isValid():
            self.colors[index] = (chosen.red(), chosen.green(), chosen.blue())
            self.refresh()

    def inspect_pixel(self, x: int, y: int):
        if not self.doc or not (0 <= x < self.doc.width and 0 <= y < self.doc.height):
            return
        index = self.selected_index()
        if index is not None:
            value = int(self.doc.display_samples[y, x, index])
            name = self.doc.channels[index].name
            text = f"{name}: {value} / {self.doc.maximum}"
            if self.doc.channels[index].kind == "Spot":
                text += f" · ink {(1 - value / self.doc.maximum) * 100:.1f}% (assumed polarity)"
        else:
            text = " · ".join(f"{c.name}: {int(self.doc.display_samples[y, x, c.index])}"
                              for c in self.doc.channels[:self.doc.base_count])
        self.statusBar().showMessage(f"Display pixel ({x}, {y}) · stored values · {text}")

    def show_details(self):
        if not self.doc:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("File details — diagnostic inventory")
        dialog.resize(720, 600)
        layout = QVBoxLayout(dialog)
        editor = QTextEdit()
        editor.setReadOnly(True)
        editor.setPlainText(json.dumps(self.doc.report(), indent=2, ensure_ascii=False))
        layout.addWidget(editor)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and any(u.isLocalFile() for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            if url.isLocalFile():
                self.open_path(url.toLocalFile())
                event.acceptProposedAction()
                break

    def closeEvent(self, event):
        # A decoder cannot safely be terminated midway through a codec operation.
        if self.loader is not None and self.loader.isRunning():
            self.statusBar().showMessage("Please wait for the image read to finish before closing.")
            event.ignore()
        else:
            event.accept()


def configure_application(app: QApplication):
    app.setApplicationName("TIFview")
    app.setStyle("Fusion")
    # The offscreen Qt platform on Windows has no system font discovery.
    # Loading a real font also makes reproducible UI previews readable.
    font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/segoeui.ttf"
    if font_path.exists():
        font_id = QFontDatabase.addApplicationFont(str(font_path))
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            app.setFont(QFont(families[0], 10))
    app.setStyleSheet("QWidget { font-size: 12px; } QToolBar { spacing: 6px; padding: 5px; } "
                      "QTreeWidget::item { padding: 2px 0px; }")


def launch(path: str | None = None) -> int:
    app = QApplication.instance() or QApplication([])
    configure_application(app)
    window = ViewerWindow(path)
    window.show()
    return app.exec()
