from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QSize, QPointF, QRectF
from PySide6.QtGui import QAction, QColor, QFont, QFontDatabase, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFontComboBox, QGraphicsPathItem, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView, QHBoxLayout, QInputDialog,
    QLabel, QMainWindow, QMessageBox, QPushButton, QSlider, QSplitter, QTextEdit,
    QSpinBox, QToolBar, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .model import ImageDocument
from .reader import load_image
from .render import render
from .editing import EditSession, raster_shape
from .writer import SaveOptions, layer_preservation_reason, save_tiff_copy


class ImageView(QGraphicsView):
    zoom_changed = Signal(float)
    pixel_hovered = Signal(int, int)
    draw_requested = Signal(str, object, object)

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
        self.tool = "Pan"
        self.stroke_width = 3
        self.filled = False
        self._drawing_start = None
        self._pan_position = None
        self.preview_item = QGraphicsPathItem()
        self.preview_item.setZValue(1)
        self.scene().addItem(self.preview_item)

    def set_tool(self, tool):
        self.tool = tool
        self._drawing_start = None
        self.preview_item.setPath(QPainterPath())
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag if tool == "Pan" else QGraphicsView.DragMode.NoDrag)
        self.viewport().setCursor(Qt.CursorShape.ArrowCursor if tool == "Pan" else Qt.CursorShape.CrossCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan_position = event.position().toPoint()
            event.accept()
            return
        if self.tool != "Pan" and event.button() == Qt.MouseButton.LeftButton:
            point = self.mapToScene(event.position().toPoint())
            if self.image_item.pixmap().isNull() or not self.sceneRect().contains(point):
                event.accept()
                return
            if self.tool == "Text":
                self.draw_requested.emit(self.tool, (point.x(), point.y()), (point.x(), point.y()))
            else:
                self._drawing_start = point
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan_position = None
            event.accept()
            return
        if self._drawing_start is not None and event.button() == Qt.MouseButton.LeftButton:
            start = self._drawing_start
            end = self.mapToScene(event.position().toPoint())
            self._drawing_start = None
            self.preview_item.setPath(QPainterPath())
            self.draw_requested.emit(self.tool, (start.x(), start.y()), (end.x(), end.y()))
            event.accept()
            return
        super().mouseReleaseEvent(event)

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
        if self._pan_position is not None:
            position = event.position().toPoint()
            delta = position - self._pan_position
            self._pan_position = position
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        if self._drawing_start is not None:
            path = QPainterPath()
            rect = QRectF(self._drawing_start, point).normalized()
            if self.tool == "Ellipse":
                path.addEllipse(rect)
            elif self.tool == "Box":
                path.addRect(rect)
            else:
                path.moveTo(self._drawing_start)
                path.lineTo(point)
            self.preview_item.setPath(path)
            self.preview_item.setPen(QPen(QColor("#00a6c4"), self.stroke_width))
            self.preview_item.setBrush(QColor(0, 166, 196, 80) if self.filled and self.tool != "Line" else Qt.BrushStyle.NoBrush)
            event.accept()
            return
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


class Saver(QThread):
    saved = Signal(str)
    failed = Signal(str)

    def __init__(self, original, edited, filename, options, overwrite):
        super().__init__()
        self.arguments = original, edited, filename, options, overwrite

    def run(self):
        try:
            path = save_tiff_copy(*self.arguments)
            self.saved.emit(str(path))
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class TiffSaveDialog(QDialog):
    def __init__(self, original, edited, parent=None):
        super().__init__(parent)
        self.setWindowTitle("TIFF save settings")
        layout = QVBoxLayout(self)
        label = QLabel("Photoshop TIFF preset\nLZW image compression · Interleaved samples\n"
                       "IBM-PC byte order · Classic TIFF (BigTIFF off)\n"
                       "Original bit depth, print resolution, channels, transparency and ICC profile")
        label.setWordWrap(True)
        layout.addWidget(label)
        self.pyramid = QCheckBox("Save image pyramid (rebuild from edited pixels)")
        self.pyramid.setChecked(True)
        layout.addWidget(self.pyramid)
        self.layers = QCheckBox("Retain original Photoshop layers")
        has_layers = bool(original.metadata.get("has_photoshop_layers"))
        reason = layer_preservation_reason(original, edited)
        self.layers.setChecked(has_layers and reason is None)
        self.layers.setEnabled(has_layers and reason is None)
        layout.addWidget(self.layers)
        if reason:
            explanation = reason + "\nThis copy will contain the merged image and all channels without Photoshop layers. "
            if original.metadata.get("byte_order") == "IBM PC (little endian)":
                explanation += "To retain layers, undo the process/transparency edits."
        elif has_layers:
            explanation = "Spot and saved-mask edits retain the original layer block, including its existing RLE/ZIP compression."
        else:
            explanation = "The source has no Photoshop layers. All image and extra channel pixels are saved."
        explanation += "\nThe source image stays untouched. Check the first edited copy in Photoshop and your RIP."
        notice = QLabel(explanation)
        notice.setWordWrap(True)
        layout.addWidget(notice)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(560, 300)

    def options(self):
        return SaveOptions(keep_layers=self.layers.isChecked(), pyramid=self.pyramid.isChecked())


class ViewerWindow(QMainWindow):
    def __init__(self, initial_path: str | None = None):
        super().__init__()
        self.doc: ImageDocument | None = None
        self.loader: Loader | None = None
        self.saver: Saver | None = None
        self.edits: EditSession | None = None
        self.colors: dict[int, tuple[int, int, int]] = {}
        self.setWindowTitle("TIFview — channel viewer and pixel editor")
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
        self.save_action = QAction("Save TIFF copy…", self)
        self.save_action.setShortcut("Ctrl+Shift+S")
        self.save_action.triggered.connect(self.choose_save)
        toolbar.addAction(self.save_action)
        self.undo_action = QAction("Undo", self)
        self.undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        self.undo_action.triggered.connect(self.undo)
        toolbar.addAction(self.undo_action)
        self.redo_action = QAction("Redo", self)
        self.redo_action.setShortcuts([QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")])
        self.redo_action.triggered.connect(self.redo)
        toolbar.addAction(self.redo_action)
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

        self.edit_toolbar = QToolBar("Pixel edits")
        self.edit_toolbar.setMovable(False)
        self.addToolBarBreak()
        self.addToolBar(self.edit_toolbar)
        self.tool_combo = QComboBox()
        self.tool_combo.addItems(["Pan", "Ellipse", "Box", "Line", "Text"])
        self.edit_toolbar.addWidget(QLabel("Tool "))
        self.edit_toolbar.addWidget(self.tool_combo)
        self.shade = QSpinBox()
        self.shade.setRange(0, 255)
        self.shade.setToolTip("Paint shade: 0 = black, maximum = white in the selected grayscale channel. Intermediate values paint gray.")
        self.edit_toolbar.addWidget(QLabel("  Shade "))
        self.edit_toolbar.addWidget(self.shade)
        self.stroke = QSpinBox()
        self.stroke.setRange(1, 300)
        self.stroke.setValue(3)
        self.stroke.setSuffix(" px")
        self.stroke.setToolTip("Stroke width in original image pixels")
        self.edit_toolbar.addWidget(QLabel("  Width "))
        self.edit_toolbar.addWidget(self.stroke)
        self.fill = QCheckBox("Filled")
        self.edit_toolbar.addWidget(self.fill)
        self.text_size = QSpinBox()
        self.text_size.setRange(1, 1000)
        self.text_size.setValue(72)
        self.text_size.setSuffix(" px")
        self.edit_toolbar.addWidget(QLabel("  Text "))
        self.edit_toolbar.addWidget(self.text_size)
        self.font_combo = QFontComboBox()
        self.font_combo.setMaximumWidth(150)
        self.edit_toolbar.addWidget(self.font_combo)
        self.edit_toolbar.addWidget(QLabel("  Select one channel to edit its pixels"))
        self.tool_combo.currentTextChanged.connect(self.change_tool)
        self.stroke.valueChanged.connect(self.drawing_style_changed)
        self.fill.toggled.connect(self.drawing_style_changed)

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
        self.style_combo.currentIndexChanged.connect(self.display_style_changed)
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
        self.notice = QLabel("Photoshop TIFF export needs Photoshop and RIP validation")
        self.notice.setWordWrap(True)
        self.notice.setStyleSheet("background: #fff0cb; color: #563a00; padding: 8px;")
        layout.addWidget(self.notice)
        self.view = ImageView()
        self.view.zoom_changed.connect(lambda value: self.zoom_label.setText(f" {value * 100:.1f}% "))
        self.view.pixel_hovered.connect(self.inspect_pixel)
        self.view.draw_requested.connect(self.apply_drawing)
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
        if self.loader is not None or self.saver is not None:
            return
        if not self.confirm_discard():
            return
        self.open_action.setEnabled(False)
        self.statusBar().showMessage(f"Reading {Path(path).name}…")
        self.loader = Loader(path)
        self.loader.loaded.connect(self.accept_document)
        self.loader.failed.connect(self.load_failed)
        self.loader.finished.connect(self.load_finished)
        self.loader.start()
        self.set_controls()

    def load_finished(self):
        loader, self.loader = self.loader, None
        if loader:
            loader.deleteLater()
        self.open_action.setEnabled(True)
        self.set_controls()

    def load_failed(self, message: str):
        QMessageBox.warning(self, "Could not open image", message)
        self.statusBar().showMessage("Image could not be opened")

    def accept_document(self, doc: ImageDocument, preview: np.ndarray):
        self.doc = doc
        self.edits = EditSession(doc)
        self.colors = {}
        self.file_label.setText(doc.path.name)
        self.file_label.setToolTip(str(doc.path))
        self.dimensions.setText(f"{doc.width:,} × {doc.height:,} px · {doc.bits}-bit {doc.color_mode}\n"
                                f"{len(doc.channels)} samples · {doc.samples.nbytes / 2**20:.1f} MiB raw")
        self.setWindowTitle(f"{doc.path.name}[*] — TIFview")
        self.setWindowModified(False)
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

    def change_tool(self, tool):
        self.view.set_tool(tool)
        if tool != "Pan":
            self.style_combo.setCurrentIndex(0)
        self.drawing_style_changed()

    def display_style_changed(self, index):
        if index == 1:
            self.tool_combo.setCurrentIndex(0)
        self.refresh()

    def drawing_style_changed(self, *_):
        self.view.stroke_width = self.stroke.value()
        self.view.filled = self.fill.isChecked()

    def apply_drawing(self, tool, start, end, text=None):
        index = self.selected_index()
        if self.edits is None or index is None or self.saver is not None or self.loader is not None:
            return
        if tool == "Text" and text is None:
            text, accepted = QInputDialog.getMultiLineText(self, "Paint text into channel", "Text:")
            if not accepted:
                return
        try:
            mask = raster_shape(tool, start, end, (self.doc.width, self.doc.height), self.stroke.value(),
                                self.fill.isChecked(), text or "", self.font_combo.currentFont().family(),
                                self.text_size.value())
            if mask and self.edits.apply(index, *mask, self.shade.value(), self.invert.isChecked()):
                self.edits_changed()
                self.statusBar().showMessage(f"Painted {tool.lower()} into {self.doc.channels[index].name} · Ctrl+Z: undo · Save TIFF copy to keep edits")
        except Exception as exc:
            QMessageBox.warning(self, "Could not paint channel", str(exc))

    def edits_changed(self):
        self.doc = self.edits.document
        self.setWindowModified(self.edits.dirty)
        self.set_controls()
        self.refresh()
        self.refresh_thumbnails()

    def refresh_thumbnails(self):
        self.channels.blockSignals(True)
        try:
            self._refresh_thumbnails()
        finally:
            self.channels.blockSignals(False)

    def _refresh_thumbnails(self):
        for channel in self.doc.channels:
            plane = self.doc.display_samples[::max(1, self.doc.height // 80), ::max(1, self.doc.width // 120), channel.index]
            gray = np.rint(plane.astype(np.float32) / self.doc.maximum * 255).astype(np.uint8)
            if (self.doc.color_mode == "CMYK" and channel.index < self.doc.base_count) or (self.doc.color_mode == "WhiteIsZero" and channel.index == 0):
                gray = 255 - gray
            gray = np.ascontiguousarray(gray)
            image = QImage(gray.data, gray.shape[1], gray.shape[0], gray.strides[0], QImage.Format.Format_Grayscale8).copy()
            self.channels.topLevelItem(channel.index + 1).setIcon(0, QIcon(QPixmap.fromImage(image).scaled(
                40, 24, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)))

    def undo(self):
        if self.edits and self.saver is None and self.loader is None:
            self.edits.undo()
            self.edits_changed()

    def redo(self):
        if self.edits and self.saver is None and self.loader is None:
            self.edits.redo()
            self.edits_changed()

    def confirm_discard(self):
        if not self.edits or not self.edits.dirty:
            return True
        answer = QMessageBox.question(self, "Unsaved channel edits", "Discard unsaved pixel edits? The original source file is unchanged.",
                                      QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                                      QMessageBox.StandardButton.Cancel)
        return answer == QMessageBox.StandardButton.Discard

    def choose_save(self):
        if self.edits is None or self.saver is not None or self.loader is not None:
            return
        dialog = TiffSaveDialog(self.edits.original, self.doc, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        initial = self.doc.path.with_name(self.doc.path.stem + ".edited.tif")
        filename, _ = QFileDialog.getSaveFileName(self, "Save edited TIFF copy", str(initial), "TIFF (*.tif *.tiff)")
        if filename:
            if not Path(filename).suffix:
                filename += ".tif"
            self.save_copy(filename, dialog.options(), overwrite=True)

    def save_copy(self, filename, options=SaveOptions(), overwrite=False):
        if self.edits is None or self.saver is not None or self.loader is not None:
            return
        self.tool_combo.setCurrentIndex(0)
        self.saver = Saver(self.edits.original, self.doc, filename, options, overwrite)
        self.saver.saved.connect(self.copy_saved)
        self.saver.failed.connect(self.save_failed)
        self.saver.finished.connect(self.save_finished)
        self.set_controls()
        self.statusBar().showMessage("Saving and checking TIFF copy…")
        self.saver.start()

    def copy_saved(self, filename):
        self.edits.mark_saved()
        self.setWindowModified(False)
        self.statusBar().showMessage(f"Saved and verified: {filename} · Original source unchanged")

    def save_failed(self, message):
        QMessageBox.warning(self, "Could not save TIFF copy", message)
        self.statusBar().showMessage("TIFF copy was not saved; edits are still in memory")

    def save_finished(self):
        saver, self.saver = self.saver, None
        if saver:
            saver.deleteLater()
        self.set_controls()

    def selected_index(self) -> int | None:
        item = self.channels.currentItem()
        value = item.data(0, Qt.ItemDataRole.UserRole) if item else -1
        return value if value is not None and value >= 0 else None

    def set_controls(self):
        selected = self.selected_index() if self.doc else None
        busy = self.loader is not None or self.saver is not None
        editable = self.doc is not None and self.doc.bits in (8, 16) and self.doc.color_mode != "Palette"
        self.open_action.setEnabled(not busy)
        self.save_action.setEnabled(editable and not busy)
        self.undo_action.setEnabled(bool(self.edits and self.edits.can_undo and not busy))
        self.redo_action.setEnabled(bool(self.edits and self.edits.can_redo and not busy))
        self.edit_toolbar.setEnabled(editable and selected is not None and not busy)
        if not editable or selected is None or busy:
            self.tool_combo.setCurrentIndex(0)
            self.view.set_tool("Pan")
        if self.doc:
            self.shade.setMaximum(self.doc.maximum)
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
        if (self.loader is not None and self.loader.isRunning()) or (self.saver is not None and self.saver.isRunning()):
            self.statusBar().showMessage("Please wait for the image read/save to finish before closing.")
            event.ignore()
        elif not self.confirm_discard():
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
