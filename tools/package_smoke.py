"""Exercise a frozen build's actual Qt pixels, codecs and channel reader."""
import hashlib
import json
from pathlib import Path
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from tifview.app import ViewerWindow, configure_application


def run(image_path: str, report_path: str) -> int:
    source = Path(image_path).resolve()
    report = Path(report_path).resolve()
    if report == source or report.exists():
        return 2  # Never overwrite an existing file for a build check.
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    app = QApplication.instance() or QApplication([])
    configure_application(app)
    errors = []

    class CheckWindow(ViewerWindow):
        def load_failed(self, message):
            errors.append(message)  # No modal dialog in an unattended check.

    window = CheckWindow()
    window.show()
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
            # The build script supplies a known synthetic RGB + two spots + alpha TIFF.
            assert [c.name for c in doc.channels] == [
                "Red", "Green", "Blue", "White Ink", "Varnish", "Saved selection"]
            assert [c.kind for c in doc.channels] == ["Process"] * 3 + ["Spot", "Spot", "Alpha mask"]
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
            assert hashlib.sha256(source.read_bytes()).hexdigest() == before
            result.update(passed=True, channels=doc.report()["channels"],
                          checks=["LZW decoding", "Photoshop names and types", "Qt channel pixels",
                                  "actual pixels, zoom and fit", "read-only source"])
        except Exception as exc:
            result.update(passed=False, error=f"{type(exc).__name__}: {exc}")
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(result, indent=2), encoding="utf-8")
        window.close()
        app.exit(0 if result["passed"] else 1)

    timer = QTimer()
    timer.timeout.connect(check)
    timer.start(50)
    return app.exec()
