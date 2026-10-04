"""Pixel edits must change intended samples and remain exactly reversible."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
import tifffile
from PySide6.QtWidgets import QApplication

from tifview.app import configure_application
from tifview.editing import EditSession, raster_shape
from tifview.reader import load_image
from tools.make_demo import photoshop_resources


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    configure_application(app)
    return app


def document(tmp_path, depth=8, orientation=1, mode="rgb", extra=0):
    maximum = 2**depth - 1
    count = 5 if mode == "separated" else 4
    samples = np.full((30, 40, count), maximum, dtype=f"uint{depth}")
    samples[..., 0] = maximum // 3
    samples[..., 1] = maximum // 2
    ps = photoshop_resources(["Test mask"], [2 if extra == 0 else 1])
    path = tmp_path / "original.tif"
    tifffile.imwrite(path, samples, photometric=mode, extrasamples=[extra], metadata=None,
                     extratags=[(274, 3, 1, orientation, False), (34377, 1, len(ps), ps, False)])
    return load_image(path)


@pytest.mark.parametrize("depth", [8, 16])
@pytest.mark.parametrize("orientation", range(1, 9))
def test_single_channel_edits_preserve_source_other_planes_and_orientation(tmp_path, depth, orientation):
    original = document(tmp_path, depth, orientation)
    before = original.samples.copy()
    session = EditSession(original)
    mask = np.full((6, 7), 255, np.uint8)
    mask[0] = 0
    mask[1] = 128
    assert session.apply(3, (2, 3, 9, 9), mask, 0)
    expected = original.display_samples.copy()
    expected[3:9, 2:9, 3] = ((original.maximum * (255 - mask.astype(np.int64)) + 127) // 255)
    np.testing.assert_array_equal(session.document.display_samples, expected)
    np.testing.assert_array_equal(original.samples, before)
    assert session.changed_channels() == {3}
    assert not session.document.samples.flags.writeable
    session.undo()
    np.testing.assert_array_equal(session.document.samples, before)
    assert not session.dirty
    session.redo()
    np.testing.assert_array_equal(session.document.display_samples, expected)


@pytest.mark.parametrize("tool", ["Ellipse", "Box", "Line", "Text"])
def test_raster_shapes_and_text_have_real_pixels(qapp, tmp_path, tool):
    doc = document(tmp_path)
    start, end = (5., 5.), (25., 24.)
    result = raster_shape(tool, start, end, (doc.width, doc.height), width=2, text="UV", text_size=14)
    bounds, mask = result
    assert mask.max() == 255
    assert np.count_nonzero(mask) > 0
    session = EditSession(doc)
    assert session.apply(3, bounds, mask, 0)
    assert np.any(session.document.samples[..., 3] < 255)
    np.testing.assert_array_equal(session.document.samples[..., :3], doc.samples[..., :3])
    session.undo()
    np.testing.assert_array_equal(session.document.samples, doc.samples)


def test_filled_box_ellipse_and_clipping(qapp):
    _, outline = raster_shape("Box", (4., 4.), (24., 24.), (30, 30), width=2)
    bounds, filled = raster_shape("Box", (4., 4.), (24., 24.), (30, 30), width=2, filled=True)
    assert filled[14 - bounds[1], 14 - bounds[0]] == 255
    assert np.count_nonzero(filled) > np.count_nonzero(outline)
    bounds, ellipse = raster_shape("Ellipse", (-10., -10.), (20., 20.), (30, 30), filled=True)
    assert bounds[0:2] == (0, 0)
    assert ellipse[5, 5] == 255
    assert raster_shape("Line", (-20., -20.), (-10., -10.), (30, 30)) is None
    assert raster_shape("Text", (0., 0.), (0., 0.), (30, 30), text="") is None


def test_cmyk_black_means_high_native_ink_and_invert_is_respected(tmp_path):
    doc = document(tmp_path, mode="separated")
    session = EditSession(doc)
    mask = np.full((2, 2), 255, np.uint8)
    session.apply(0, (1, 1, 3, 3), mask, 0)
    assert np.all(session.document.samples[1:3, 1:3, 0] == 255)
    session.apply(0, (1, 1, 3, 3), mask, 0, invert=True)
    assert np.all(session.document.samples[1:3, 1:3, 0] == 0)
    np.testing.assert_array_equal(session.document.samples[..., 1:], doc.samples[..., 1:])


def test_associated_transparency_rescales_native_colors_and_undoes_exactly(tmp_path):
    samples = np.full((4, 5, 4), [0, 64, 128, 128], np.uint8)
    path = tmp_path / "associated.tif"
    tifffile.imwrite(path, samples, photometric="rgb", extrasamples=[1], metadata=None)
    doc = load_image(path)
    session = EditSession(doc)
    session.apply(3, (1, 1, 3, 3), np.full((2, 2), 255, np.uint8), 255)
    np.testing.assert_array_equal(session.document.samples[1, 1], [0, 128, 255, 255])
    np.testing.assert_array_equal(session.document.samples[0, 0], samples[0, 0])
    session.undo()
    np.testing.assert_array_equal(session.document.samples, samples)
    session.apply(0, (1, 1, 3, 3), np.full((2, 2), 255, np.uint8), 255)
    assert session.document.samples[1, 1, 0] == 128  # Premultiplied values cannot exceed alpha.
    assert session.changed_channels() == {0}


def test_undo_redo_save_checkpoint_branching_and_history_limit(tmp_path):
    doc = document(tmp_path)
    session = EditSession(doc, history_limit=20)
    mask = np.full((2, 2), 255, np.uint8)
    bounds = (1, 1, 3, 3)
    session.apply(3, bounds, mask, 0)
    session.mark_saved()
    session.apply(3, bounds, mask, 128)
    assert session.dirty
    session.undo()
    assert not session.dirty
    session.apply(3, bounds, mask, 64)
    assert not session.can_redo
    session.undo()
    assert not session.dirty
    session.redo()
    session.apply(3, bounds, mask, 32)
    assert sum(p.bytes for p in session.history) <= 20
    before = session.document.samples.copy()
    with pytest.raises(ValueError, match="undo limit"):
        session.apply(3, (0, 0, 10, 10), np.full((10, 10), 255, np.uint8), 0)
    np.testing.assert_array_equal(session.document.samples, before)


def test_noop_does_not_allocate_edit_copy(tmp_path):
    session = EditSession(document(tmp_path))
    assert not session.apply(3, (0, 0, 2, 2), np.full((2, 2), 255, np.uint8), 255)
    assert session.document is session.original
    assert not session.can_undo
    assert not session.dirty
