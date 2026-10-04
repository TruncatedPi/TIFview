from pathlib import Path

import numpy as np
import pytest
import tifffile

from tifview.model import Channel, ImageDocument
from tifview.reader import load_image
from tools.make_demo import photoshop_resources


def test_spot_sequence_counts_only_spots_in_stored_order():
    names_and_kinds = [
        ("Cyan", "Process"),
        ("Transparency", "Transparency"),
        ("w-front", "Spot"),
        ("Selection", "Alpha mask"),
        ("Extra", "Unknown"),
        ("v-front", "Spot"),
        ("Image", "Image"),
        ("w-back", "Spot"),
        ("v-all", "Spot"),
    ]
    channels = [Channel(index, name, kind, "test", (255, 255, 255))
                for index, (name, kind) in enumerate(names_and_kinds)]
    samples = np.zeros((2, 3, len(channels)), np.uint8)
    doc = ImageDocument(Path("test.tif"), samples, channels, "CMYK", 1, 8)
    original_report = doc.report()

    assert [doc.spot_sequence(i) for i in range(len(channels))] == [
        None, None, 1, None, None, 2, None, 3, 4]
    assert [doc.channel_label(i) for i in range(len(channels))] == [
        "Cyan", "Transparency", "1. w-front", "Selection", "Extra",
        "2. v-front", "Image", "3. w-back", "4. v-all"]
    assert [(channel.index, channel.name) for channel in doc.channels] == [
        (i, name) for i, (name, _) in enumerate(names_and_kinds)]
    assert doc.report() == original_report
    np.testing.assert_array_equal(doc.samples, samples)


def test_spot_labels_keep_duplicate_unicode_and_numbered_names(tmp_path):
    names = ["2. 白", "2. 白", "Varnish"]
    resources = photoshop_resources(names, [2, 2, 2])
    path = tmp_path / "spots.tif"
    tifffile.imwrite(path, np.zeros((2, 3, 6), np.uint8), photometric="rgb", planarconfig="contig",
                     extrasamples=[0, 0, 0], metadata=None,
                     extratags=[(34377, 7, len(resources), resources, False)])
    original_bytes = path.read_bytes()
    doc = load_image(path)

    assert [doc.channel_label(i) for i in range(3, 6)] == [
        "1. 2. 白", "2. 2. 白", "3. Varnish"]
    assert [channel.name for channel in doc.channels[3:]] == names
    assert [channel["name"] for channel in doc.report()["channels"][3:]] == names
    assert doc.photoshop_resources == resources
    assert path.read_bytes() == original_bytes


@pytest.mark.parametrize("index", [-1, 2])
def test_spot_label_rejects_invalid_channel_index(index):
    channels = [Channel(0, "Gray", "Image", "test", (255, 255, 255)),
                Channel(1, "White", "Spot", "test", (255, 255, 255))]
    doc = ImageDocument(Path("test.tif"), np.zeros((2, 3, 2), np.uint8),
                        channels, "Gray", 1, 8)
    with pytest.raises(IndexError):
        doc.channel_label(index)
    with pytest.raises(IndexError):
        doc.spot_sequence(index)
