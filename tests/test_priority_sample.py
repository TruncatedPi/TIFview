"""Optional regression against the supplied Photoshop sample; never bundles it."""
import hashlib
import os
from pathlib import Path

import numpy as np
import pytest

from tifview.reader import load_image
from tifview.render import render


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
