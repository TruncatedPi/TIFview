"""Optional regression against the supplied Photoshop sample; never bundles it."""
import hashlib
import os
from pathlib import Path

import numpy as np
import pytest
import tifffile

from tifview.editing import EditSession
from tifview.photoshop import without_thumbnails
from tifview.reader import load_image
from tifview.render import render
from tifview.writer import save_tiff_copy


def test_supplied_photoshop_sample_matches_channel_panel():
    sample = os.environ.get("TIFVIEW_SAMPLE")
    if not sample:
        pytest.skip("Set TIFVIEW_SAMPLE to the supplied Bailey TIFF to run real-file validation")
    path = Path(sample)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    doc = load_image(path)
    assert doc.samples.shape == (781, 1228, 9)
    assert doc.bits == 8
    assert [c.name for c in doc.channels] == [
        "Cyan", "Magenta", "Yellow", "Black", "Transparency", "w-front", "v-front", "w-back", "v-all"]
    assert [c.kind for c in doc.channels] == ["Process"] * 4 + ["Transparency"] + ["Spot"] * 4
    assert doc.metadata["compression"] == "LZW"
    assert doc.metadata["planar_configuration"] == "CONTIG"
    assert doc.metadata["byte_order"] == "IBM PC (little endian)"
    assert doc.metadata["pyramid_subifds"] == 1
    assert doc.metadata["has_photoshop_layers"]
    assert doc.icc_transform is not None
    original = doc.samples.copy()
    assert render(doc).shape == (781, 1228, 3)
    for index in range(9):
        pixels = render(doc, selected=index)
        expected = 255 - doc.samples[..., index] if index < 4 else doc.samples[..., index]
        np.testing.assert_array_equal(pixels[..., 0], expected)
    assert [c.display.opacity for c in doc.channels[5:]] == [2, 2, 2, 5]
    np.testing.assert_array_equal(doc.samples, original)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_supplied_photoshop_spot_edit_retains_layers_and_print_settings(tmp_path):
    sample = os.environ.get("TIFVIEW_SAMPLE")
    if not sample:
        pytest.skip("Set TIFVIEW_SAMPLE for real-file TIFF save validation")
    source = Path(sample)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    original = load_image(source)
    session = EditSession(original)
    shade = original.maximum - int(original.samples[10, 10, 7])
    session.apply(7, (10, 10, 30, 25), np.full((15, 20), 255, np.uint8), shade)
    assert session.changed_channels() == {7}
    exported = save_tiff_copy(original, session.document, tmp_path / "spot-edited-copy.tif")
    reopened = load_image(exported)
    np.testing.assert_array_equal(reopened.samples, session.document.samples)
    np.testing.assert_array_equal(reopened.samples[..., [0, 1, 2, 3, 4, 5, 6, 8]],
                                  original.samples[..., [0, 1, 2, 3, 4, 5, 6, 8]])
    assert reopened.photoshop_resources == without_thumbnails(original.photoshop_resources)
    assert reopened.icc_profile == original.icc_profile
    assert [c.name for c in reopened.channels] == [c.name for c in original.channels]
    with tifffile.TiffFile(source) as src, tifffile.TiffFile(exported) as dst:
        a, b = src.pages[0], dst.pages[0]
        assert b.tags[37724].value == a.tags[37724].value
        assert dst.byteorder == "<" and not dst.is_bigtiff
        assert int(b.compression) == 5 and int(b.planarconfig) == 1
        assert b.extrasamples == a.extrasamples
        assert b.tags[282].value[0] / b.tags[282].value[1] == 360
        assert b.tags[283].value[0] / b.tags[283].value[1] == 360
        assert int(b.tags[296].value) == 2
        assert len(b.subifds) == 1
        assert b.pages[0].asarray().shape == (391, 614, 9)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
