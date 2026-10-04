"""Read a local sample and export reproducible inspection records, never its TIFF.

The channel contact sheet is for comparing with Photoshop. This tool cannot
assert that a Photoshop screenshot not supplied to it matches every pixel.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from tifview.reader import load_image
from tifview.render import render


def validate(path: Path, output: Path):
    output = output.resolve()
    if path.resolve() == output or path.resolve() in output.parents or output == path.resolve().parent:
        raise ValueError("Output must be a directory separate from the source image.")
    output.mkdir(parents=True, exist_ok=True)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    start = time.perf_counter()
    doc = load_image(path)
    seconds = time.perf_counter() - start
    all_raw = doc.samples.copy()
    visible = set(range(doc.base_count)) | {c.index for c in doc.channels if c.kind == "Transparency"}
    preview = render(doc, visible=visible)
    Image.fromarray(preview).save(output / "composite.png")
    cell_w, cell_h = 410, 310
    cols = 3
    rows = (len(doc.channels) + 1 + cols - 1) // cols
    sheet = Image.new("RGB", (cell_w * cols, cell_h * rows), "#e4e7eb")
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 18)
    except OSError:
        font = ImageFont.load_default()
    cells = [("Composite (ICC to sRGB / inspection)", preview)]
    for channel in doc.channels:
        pixels = render(doc, selected=channel.index)
        cells.append((f"{channel.index}: {channel.name} / {channel.kind}", pixels))
        # These PNGs preserve display orientation, not original TIFF sample semantics.
        Image.fromarray(pixels).save(output / f"sample-{channel.index}.png")
    for i, (label, pixels) in enumerate(cells):
        x, y = (i % cols) * cell_w, (i // cols) * cell_h
        thumb = Image.fromarray(pixels)
        thumb.thumbnail((cell_w - 20, cell_h - 50))
        sheet.paste(thumb, (x + (cell_w - thumb.width) // 2, y + 40))
        draw.text((x + 10, y + 8), label, fill="#17212d", font=font)
    sheet.save(output / "channels.png")

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from tifview.app import ViewerWindow, configure_application
    app = QApplication.instance() or QApplication([])
    configure_application(app)
    window = ViewerWindow()
    window.show()
    app.processEvents()
    window.accept_document(doc, preview)
    app.processEvents()
    window.grab().save(str(output / "viewer-composite.png"))
    for channel in doc.channels:
        window.channels.setCurrentItem(window.channels.topLevelItem(channel.index + 1))
        app.processEvents()
        pixmap = window.view.image_item.pixmap()
        assert (pixmap.width(), pixmap.height()) == (doc.width, doc.height)
        if channel.name == "w-back":
            window.grab().save(str(output / "viewer-w-back.png"))
    window.close()
    app.processEvents()
    np.testing.assert_array_equal(doc.samples, all_raw)
    after = hashlib.sha256(path.read_bytes()).hexdigest()
    assert before == after, "Source file changed during inspection"
    report = doc.report()
    report["validation"] = {
        "sha256_before": before, "sha256_after": after, "source_unchanged": before == after,
        "raw_samples_unchanged_after_all_previews": True,
        "load_seconds": round(seconds, 3),
        "gui_selected_all_samples": True,
        "photoshop_pixel_comparison": "Pending; contact sheet provided for comparison",
    }
    (output / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"output": str(output), "channels": [(c.name, c.kind) for c in doc.channels],
                      "load_seconds": round(seconds, 3), "source_unchanged": True}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--output", type=Path, default=Path("validation/local/sample"))
    args = parser.parse_args()
    validate(args.image, args.output)
