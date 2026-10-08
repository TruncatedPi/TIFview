"""Exercise a frozen build's actual Qt pixels, codecs and channel reader."""
import hashlib
import json
from pathlib import Path
import time
import traceback

import numpy as np
import tifffile
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QLineEdit, QLabel

import tifview.app as gui
from tifview import __version__
from tifview.app import ViewerWindow, configure_application
from tifview.reader import load_image
from tifview.render import render
from tifview.writer import save_tiff_copy
from tifview.layers import LayerStack
from tifview.svg import SvgArtwork, export_svg, job_data, load_job
from tifview.psvectors import read_shape


def run(image_path: str, report_path: str) -> int:
    source = Path(image_path).resolve()
    report = Path(report_path).resolve()
    edited_path = report.with_name(report.stem + "-edited.tif")
    if report == source or report.exists() or edited_path == source or edited_path.exists():
        return 2  # Never overwrite an existing file for a build check.
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    app = QApplication.instance() or QApplication([])
    configure_application(app)
    errors = []

    class CheckWindow(ViewerWindow):
        def load_failed(self, message):
            errors.append(message)  # No modal dialog in an unattended check.

        def preview_failed(self, key, message):
            errors.append(message)
            super().preview_failed(key, message)

    window = CheckWindow()
    window.show()
    assert app.applicationVersion() == __version__
    assert __version__ in window.windowTitle()
    assert window.version_label.text() == f"TIFview {__version__}"
    about = window.about_dialog()
    assert any(label.text() == f"TIFview {__version__}" for label in about.findChildren(QLabel))
    assert about.findChild(QLineEdit).text() == str(window.application_location())
    about.deleteLater()
    window.open_path(str(source))
    deadline = time.monotonic() + 30
    result = {}

    def check():
        if window.loader is not None:
            if time.monotonic() > deadline:
                errors.append("Image load exceeded 30 seconds")
                app.exit(1)
            return
        timer.stop()
        try:
            if errors:
                raise RuntimeError("; ".join(errors))
            doc = window.doc
            if doc is None:
                raise AssertionError("No document loaded")
            assert __version__ in window.windowTitle() and window.version_label.isVisible()
            # The build script supplies a known synthetic RGB + two spots + alpha TIFF.
            assert [c.name for c in doc.channels] == [
                "Red", "Green", "Blue", "White Ink", "Varnish", "Saved selection"]
            assert [c.kind for c in doc.channels] == ["Process"] * 3 + ["Spot", "Spot", "Alpha mask"]
            assert [window.channels.topLevelItem(i).text(0) for i in (4, 5, 6)] == [
                "1. White Ink", "2. Varnish", "Saved selection"]
            for index, x, y, value in [(3, 110, 80, 0), (3, 10, 10, 255),
                                        (4, 480, 240, 0), (5, 620, 200, 100)]:
                window.channels.setCurrentItem(window.channels.topLevelItem(index + 1))
                pixels = window.view.image_item.pixmap().toImage()
                assert (pixels.width(), pixels.height()) == (720, 500)
                color = pixels.pixelColor(x, y)
                assert (color.red(), color.green(), color.blue()) == (value,) * 3
            window.actual()
            assert abs(window.view.transform().m11() * window.view.viewport().devicePixelRatioF() - 1) < .0001
            window.view.zoom_by(2)
            assert abs(window.view.transform().m11() * window.view.viewport().devicePixelRatioF() - 2) < .0001
            window.fit()
            assert window.view.fitted
            assert not doc.samples.flags.writeable
            assert doc.metadata["has_photoshop_layers"] and doc.icc_profile
            window.channels.setCurrentItem(window.channels.topLevelItem(4))
            for tool, start, end in [
                ("Ellipse", (350., 40.), (440., 120.)),
                ("Box", (350., 150.), (440., 230.)),
                ("Line", (350., 260.), (440., 300.)),
                ("Text", (350., 330.), (350., 330.)),
            ]:
                before_stroke = window.doc.samples.copy()
                window.tool_combo.setCurrentText(tool)
                window.apply_drawing(tool, start, end, text="UV" if tool == "Text" else None)
                painted = window.doc.samples.copy()
                assert not np.array_equal(painted[..., 3], before_stroke[..., 3]), tool
                window.undo()
                np.testing.assert_array_equal(window.doc.samples, before_stroke)
                window.redo()
                np.testing.assert_array_equal(window.doc.samples, painted)
            pixels = window.view.image_item.pixmap().toImage().convertToFormat(QImage.Format.Format_RGB888)
            rows = np.frombuffer(pixels.constBits(), np.uint8).reshape(pixels.height(), pixels.bytesPerLine())
            displayed = rows[:, :pixels.width() * 3].reshape(pixels.height(), pixels.width(), 3)
            np.testing.assert_array_equal(displayed[..., 0], window.doc.samples[..., 3])
            np.testing.assert_array_equal(window.doc.samples[..., [0, 1, 2, 4, 5]], doc.samples[..., [0, 1, 2, 4, 5]])
            assert window.edits.dirty
            painted_samples = window.doc.samples.copy()
            assert window.perform_spot_change("add_spot", "Empty ink", (255, 255, 255), 100, select_result=True) == 6
            assert window.view.image_item.pixmap().toImage().pixelColor(370, 85).red() == 255
            assert window.perform_spot_change("add_spot", "White copy", None, None, 3, select_result=True) == 7
            np.testing.assert_array_equal(window.doc.samples[..., 7], painted_samples[..., 3])
            assert window.perform_spot_change("move_spot", 7, 1, select_result=True) == 3
            window.perform_spot_change("update_spot", 3, "Proof white", None, 12)
            window.perform_spot_change("delete_spot", 6)  # Varnish after the reorder.
            changed_samples = window.doc.samples.copy()
            changed_channels = window.doc.channels.copy()
            changed_resources = window.doc.photoshop_resources
            assert [c.name for c in changed_channels[3:]] == [
                "Proof white", "White Ink", "Saved selection", "Empty ink"]
            assert window.channels.topLevelItem(4).text(0) == "1. Proof white"
            for _ in range(5):
                window.undo()
            np.testing.assert_array_equal(window.doc.samples, painted_samples)
            assert window.doc.channels == doc.channels
            for _ in range(5):
                window.redo()
            np.testing.assert_array_equal(window.doc.samples, changed_samples)
            assert window.doc.channels == changed_channels
            assert window.doc.photoshop_resources == changed_resources
            save_tiff_copy(doc, window.doc, edited_path)
            reopened = load_image(edited_path)
            np.testing.assert_array_equal(reopened.samples, window.doc.samples)
            assert reopened.icc_profile == doc.icc_profile
            assert reopened.photoshop_resources == window.doc.photoshop_resources
            with tifffile.TiffFile(source) as original, tifffile.TiffFile(edited_path) as saved:
                assert saved.pages[0].tags[37724].value == original.pages[0].tags[37724].value
                assert len(saved.pages[0].subifds) == 1
                assert saved.byteorder == "<" and not saved.is_bigtiff
                assert int(saved.pages[0].compression) == 5
                assert int(saved.pages[0].planarconfig) == 1
            # Exercise the same thread/cancellation path as a large production
            # file without making every portable build decode a huge fixture.
            gui.ASYNC_PREVIEW_PIXELS = 0
            window.preview_cache.clear()
            window._displayed_preview = None
            window.channels.setCurrentItem(window.channels.topLevelItem(0))
            window.refresh()
            assert window.previewer is not None
            assert not window.save_action.isEnabled()
            window.channels.topLevelItem(1).setCheckState(0, Qt.CheckState.Unchecked)
            window.overlays.setChecked(True)
            window.channels.topLevelItem(4).setCheckState(0, Qt.CheckState.Checked)
            preview_deadline = time.monotonic() + 15
            while window.previewer is not None and time.monotonic() < preview_deadline:
                app.processEvents()
                time.sleep(.005)
            app.processEvents()
            assert window.previewer is None and window._pending_preview is None
            assert not errors, "; ".join(errors)
            key, settings = window.preview_settings()
            assert window._displayed_preview == key
            pixels = window.view.image_item.pixmap().toImage().convertToFormat(QImage.Format.Format_RGB888)
            rows = np.frombuffer(pixels.constBits(), np.uint8).reshape(pixels.height(), pixels.bytesPerLine())
            displayed = rows[:, :pixels.width() * 3].reshape(pixels.height(), pixels.width(), 3)
            np.testing.assert_array_equal(displayed, render(window.doc, **settings))
            assert window.save_action.isEnabled()
            np.testing.assert_array_equal(reopened.samples, window.doc.samples)
            # Exercise lazy layer decoding and the same native merge/save path
            # used for Photoshop visibility/order changes in the portable app.
            def drain_layers():
                deadline = time.monotonic() + 20
                while (window.layer_busy() or window.previewer is not None) and time.monotonic() < deadline:
                    app.processEvents()
                    time.sleep(.005)
                app.processEvents()
                assert not window.layer_busy() and window.previewer is None
                assert not errors, "; ".join(errors)

            window.tabs.setCurrentIndex(1)
            drain_layers()
            assert [layer.name for layer in window.doc.layer_stack.layers] == [
                "SYNTHETIC original base", "SYNTHETIC overlay"]
            assert window.doc.layer_state.order == (1, 0)
            assert window.doc.layer_state.visible == frozenset({0})
            assert not window.view.drawing_enabled
            layer_start = window.doc.samples.copy()
            window.layers_panel.tree.setCurrentItem(window.layers_panel.tree.topLevelItem(1))
            drain_layers()
            preview = window.view.image_item.pixmap().toImage()
            assert preview.pixelColor(390, 250).green() > 150
            window.layers_panel.tree.topLevelItem(1).setCheckState(0, Qt.CheckState.Checked)
            drain_layers()
            np.testing.assert_array_equal(window.doc.samples[250, 390, :3], [30, 200, 70])
            window.move_layer(1, 1)
            drain_layers()
            np.testing.assert_array_equal(window.doc.samples[..., :3], layer_start[..., :3])
            window.layer_visibility_changed(0, False)
            drain_layers()
            assert window.doc.channels[-1].kind == "Transparency"
            assert window.doc.samples[10, 10, -1] == 0 and window.doc.samples[250, 390, -1] == 255
            np.testing.assert_array_equal(window.doc.samples[..., 3:-1], layer_start[..., 3:])
            after_layers = window.doc.samples.copy()
            for _ in range(3):
                window.undo()
                drain_layers()
            np.testing.assert_array_equal(window.doc.samples, layer_start)
            for _ in range(3):
                window.redo()
                drain_layers()
            np.testing.assert_array_equal(window.doc.samples, after_layers)
            layer_path = report.with_name(report.stem + "-layers.tif")
            if layer_path.exists():
                raise FileExistsError(layer_path)
            save_tiff_copy(window.edits.original, window.doc, layer_path)
            saved_doc = load_image(layer_path)
            np.testing.assert_array_equal(saved_doc.samples, after_layers)
            saved_stack = LayerStack.from_document(saved_doc)
            assert [layer.name for layer in saved_stack.layers] == ["SYNTHETIC overlay", "SYNTHETIC original base"]
            assert saved_stack.default_visible == frozenset({0})
            np.testing.assert_array_equal(saved_stack.decode_layer(0).samples,
                                          window.doc.layer_stack.decode_layer(1).samples)
            # Exercise QtSvg from the frozen runtime, rather than assuming the
            # desktop module and its DLL were collected by the packager.
            artwork = SvgArtwork.from_bytes(b'<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="10mm" viewBox="0 0 20 20"><rect x="2" y="2" width="16" height="16" fill="none" stroke="red" stroke-width="0.05mm"/></svg>')
            half_stroke = .05 * 96 / 25.4 / 2
            np.testing.assert_allclose(artwork.geometry_bounds,
                                       (2-half_stroke, 2-half_stroke, 16+2*half_stroke, 16+2*half_stroke))
            before_svg = window.doc.samples.copy()
            window.install_svg(artwork)
            window.vectors_panel.fields["x_mm"].setValue(5)
            window.undo()
            assert window.vectors_panel.placement.x_mm == 0
            window.redo()
            assert window.vectors_panel.placement.x_mm == 5
            assert window.view.svg_item.renderer.isValid()
            window.vectors_panel.visible.setChecked(False)
            assert not window.view.svg_item.isVisible()
            svg_file = report.with_name(report.stem + "-aligned.svg")
            job_file = report.with_name(report.stem + ".tifview.json")
            if svg_file.exists() or job_file.exists():
                raise FileExistsError("Vector check outputs already exist")
            svg_file.write_bytes(export_svg(window.doc, artwork, window.vectors_panel.placement))
            job_file.write_text(json.dumps(job_data(window.doc, artwork, window.vectors_panel.placement)), encoding="utf-8")
            loaded_svg, loaded_placement = load_job(job_file, window.doc)
            assert loaded_svg == artwork and loaded_placement == window.vectors_panel.placement
            np.testing.assert_array_equal(window.doc.samples, before_svg)
            window.vectors_panel.mark_saved()
            drain_layers()
            before_canvas = window.doc.samples.copy()
            window.edits.expand_canvas(window.doc.width + 40, window.doc.height + 40, 20, 20)
            window.edits_changed()
            drain_layers()
            expanded_pixels = window.doc.samples.copy()
            assert window.doc.canvas.layers_preserved
            np.testing.assert_array_equal(window.doc.display_samples[20:520, 20:740], before_canvas)
            canvas_path = report.with_name(report.stem + "-canvas.tif")
            if canvas_path.exists():
                raise FileExistsError(canvas_path)
            save_tiff_copy(window.edits.original, window.doc, canvas_path)
            np.testing.assert_array_equal(load_image(canvas_path).samples, expanded_pixels)
            window.edits.undo(); window.edits_changed(); drain_layers()
            np.testing.assert_array_equal(window.doc.samples, before_canvas)
            window.edits.redo(); window.edits_changed(); drain_layers()
            np.testing.assert_array_equal(window.doc.samples, expanded_pixels)
            window.vectors_panel.mark_saved()
            # Synthetic 25%-75% rectangle and solid RGB-red descriptor, with
            # no cached raster pixels (no private/customer data in the bundle).
            shape = read_shape({
                b"vsms": bytes.fromhex(
                    "03000000000000000600000000000000000000000000000000000000000000000000"
                    "08000000000000000000000000000000000000000000000000000000040001000100"
                    "0000000000000000000000000000000000000100000040000000400000004000000040"
                    "0000004000000040000100000040000000c000000040000000c000000040000000c000"
                    "01000000c0000000c0000000c0000000c0000000c0000000c00001000000c000000040"
                    "000000c000000040000000c00000004000"),
                b"vscg": bytes.fromhex(
                    "6f436f531000000000000000000000006c6c756e010000000000000020726c43636a624f"
                    "00000000000000004342475203000000000000002020645262756f640000000000e06f40"
                    "00000000206e724762756f6400000000000000000000000020206c4262756f640000000000000000")}, "<", "RGB")
            shape_samples, shape_alpha = shape.pixels(20, 20, 16, 3)
            np.testing.assert_array_equal(shape_samples[10, 10], [65535, 0, 0])
            assert shape_alpha[10, 10] == 65535 and shape_alpha[1, 1] == 0
            assert hashlib.sha256(source.read_bytes()).hexdigest() == before
            result.update(passed=True, channels=doc.report()["channels"],
                          version=__version__,
                          checks=["visible application version and About location", "LZW decoding", "Photoshop names and types", "Qt channel pixels",
                                  "spot sequence labels", "background previews and stale-result cancellation",
                                  "actual pixels, zoom and fit", "ellipse, box, line and text pixels",
                                  "create, duplicate, reorder, properties and delete spots", "exact structural undo and redo",
                                  "exact undo and redo", "TIFF save and reopen", "opaque RLE layers and ICC",
                                  "rebuilt image pyramid", "lazy Qt layer pixels", "layer visibility and order",
                                  "synchronized native layer composite and transparency", "saved compressed layer pixels",
                                  "mixed layer undo and redo", "frozen Qt SVG renderer and alignment undo/redo",
                                  "physical SVG export and alignment job round trip", "absolute SVG shape and stroke units", "native solid vector shape without cached pixels", "transparent canvas growth, raster layers, copy save and exact undo", "untouched source"])
        except Exception as exc:
            result.update(passed=False, error=traceback.format_exc())
        if window.previewer is not None:
            window._pending_preview = None
            window.previewer.requestInterruption()
            # Finish the worker before destroying its Qt owner, including on a
            # failed check. Band cancellation makes this wait short.
            window.previewer.wait(5000)
            app.processEvents()
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(result, indent=2), encoding="utf-8")
        if window.edits:
            window.edits.mark_saved()  # An unattended check must not ask to discard.
        window.vectors_panel.mark_saved()
        window.close()
        app.exit(0 if result["passed"] else 1)

    timer = QTimer()
    timer.timeout.connect(check)
    timer.start(50)
    return app.exec()
