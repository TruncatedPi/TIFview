"""Structural spot saves must keep each ink and its Photoshop records together."""
import hashlib
import struct

import numpy as np
import pytest
import tifffile
from PIL import ImageCms
from psdtags import (PsdChannel, PsdChannelId, PsdCompressionType, PsdFormat,
                     PsdKey, PsdLayer, PsdLayers, PsdRectangle, PsdUserMask, TiffImageSourceData)

from tifview.editing import EditSession
from tifview.photoshop import _resource_blocks, read_resources
from tifview.reader import load_image
from tifview.writer import SaveOptions, save_tiff_copy
from tools.make_demo import photoshop_resources, resource


SCREEN = bytes.fromhex("000000000001000000000000000000000000")


def source_file(tmp_path, bits=8, extra_resource=b""):
    samples = np.full((9, 13, 7), [70, 40, 90, 15, 128, 210, 37], dtype=f"uint{bits}")
    if bits == 16:
        samples *= 257
    ps = photoshop_resources(["White A", "Transparency", "Varnish B", "Selection"], [2, 0, 2, 0], solidity=5)
    ids = resource(1053, struct.pack(">4I", 10, 0, 21, 27)) + resource(1044, struct.pack(">I", 41))
    screens = resource(1043, struct.pack(">HH", 6, 2) + SCREEN + struct.pack(">I", 65536) + SCREEN[4:])
    alternate = resource(1067, struct.pack(">HHI5HI5H", 1, 2, 10, 0, 65535, 65535, 65535, 0,
                                         21, 7, 5000, 0, 0, 0))
    opaque = b"8BIM\x75\x30\x03abc\x00\x00\x00\x03xyz\x00"
    ps += ids + screens + alternate + opaque + extra_resource
    layer = PsdLayer("Original process layer", [PsdChannel(PsdChannelId(i), PsdCompressionType.RLE,
                    samples[..., i].copy()) for i in range(3)], PsdRectangle(0, 0, 9, 13))
    layers = TiffImageSourceData(PsdFormat.LE32BIT,
             PsdLayers(PsdKey.LAYER if bits == 8 else PsdKey.LAYER_16, [layer]), PsdUserMask()).tobytes()
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    path = tmp_path / "source.tif"
    tifffile.imwrite(path, samples, photometric="rgb", extrasamples=[0, 1, 0, 0], metadata=None,
                     compression="lzw", iccprofile=profile, resolution=(360, 360),
                     extratags=[(34377, 1, len(ps), ps, False), (37724, 7, len(layers), layers, False)])
    return load_image(path), layers, opaque


def payloads(doc):
    return {resource_id: payload for resource_id, _, payload, _ in _resource_blocks(doc.photoshop_resources)}


@pytest.mark.parametrize("bits", [8, 16])
def test_create_duplicate_reorder_delete_save_retains_layers_alpha_and_metadata(tmp_path, bits):
    original, layer_bytes, opaque = source_file(tmp_path, bits)
    source_hash = hashlib.sha256(original.path.read_bytes()).hexdigest()
    edits = EditSession(original)
    added = edits.add_spot("White 新 🦁", color=(200, 210, 220), solidity=8)
    assert np.all(edits.document.samples[..., added] == original.maximum)
    duplicate = edits.add_spot("Varnish copy", source_index=5)
    np.testing.assert_array_equal(edits.document.samples[..., duplicate], original.samples[..., 5])
    edits.move_spot(duplicate, 1)
    edits.delete_spot(5)  # White A now sits in the second spot slot, after transparency.
    expected = edits.document
    target = tmp_path / "spots.tif"
    save_tiff_copy(original, expected, target)
    reopened = load_image(target)
    np.testing.assert_array_equal(reopened.samples, expected.samples)
    assert [c.name for c in reopened.channels] == [c.name for c in expected.channels]
    assert [c.display for c in reopened.channels] == [c.display for c in expected.channels]
    assert reopened.icc_profile == original.icc_profile
    assert hashlib.sha256(original.path.read_bytes()).hexdigest() == source_hash
    assert opaque in reopened.photoshop_resources
    with tifffile.TiffFile(target) as saved:
        page = saved.pages[0]
        assert page.tags[37724].value == layer_bytes
        assert [int(x) for x in page.extrasamples] == expected.metadata["extra_samples"]
        assert not saved.is_bigtiff and saved.byteorder == "<"
        assert int(page.compression) == 5 and int(page.planarconfig) == 1
    resources = payloads(reopened)
    ids = struct.unpack(f">{len(reopened.channels) - 3}I", resources[1053])
    assert ids == (42, 0, 27, 21, 41)
    assert struct.unpack(">I", resources[1044]) == (43,)
    assert resources[1043] == struct.pack(">HH", 6, 3) + struct.pack(">I", 65536) + SCREEN[4:] + struct.pack(">I", 65536) + SCREEN[4:] + SCREEN
    assert struct.unpack_from(">HH", resources[1067]) == (1, 2)
    assert struct.unpack_from(">I", resources[1067], 4)[0] == 42
    assert struct.unpack_from(">I", resources[1067], 18)[0] == 21
    assert not read_resources(reopened.photoshop_resources).warnings


def test_deleting_spot_before_transparency_keeps_original_alpha_and_layer_pixels(tmp_path):
    original, layers, _ = source_file(tmp_path)
    edits = EditSession(original)
    edits.delete_spot(3)
    assert edits.document.channels[3].kind == "Transparency"
    target = tmp_path / "deleted.tif"
    save_tiff_copy(original, edits.document, target)
    reopened = load_image(target)
    np.testing.assert_array_equal(reopened.samples[..., :3], original.samples[..., :3])
    np.testing.assert_array_equal(reopened.samples[..., 3], original.samples[..., 4])
    with tifffile.TiffFile(target) as saved:
        assert saved.pages[0].tags[37724].value == layers


def test_unsupported_channel_dependencies_fail_without_changing_session(tmp_path):
    original, _, _ = source_file(tmp_path, extra_resource=resource(1022, b"\0\x06\0"))
    edits = EditSession(original)
    with pytest.raises(ValueError, match="Quick Mask"):
        edits.move_spot(3, 2)
    assert edits.document is original and not edits.dirty


def test_saved_preview_colour_change_removes_only_its_stale_alternate(tmp_path):
    original, _, _ = source_file(tmp_path)
    edits = EditSession(original)
    edits.update_spot(3, "White renamed", color=(40, 60, 80), solidity=14)
    alternate = payloads(edits.document)[1067]
    assert struct.unpack_from(">HHI", alternate) == (1, 1, 21)
    edits.undo()
    assert edits.document.photoshop_resources == original.photoshop_resources


def test_layer_channel_dependencies_require_explicit_merged_copy(tmp_path):
    original, layer_bytes, _ = source_file(tmp_path)
    unsafe_layers = layer_bytes + b"MIB8hplA" + struct.pack("<I", 4) + b"\0" * 4
    path = tmp_path / "extra-layer-data.tif"
    ps = original.photoshop_resources
    tifffile.imwrite(path, original.samples, photometric="rgb", extrasamples=[0, 1, 0, 0], metadata=None,
                     extratags=[(34377, 1, len(ps), ps, False),
                                (37724, 7, len(unsafe_layers), unsafe_layers, False)])
    original = load_image(path)
    edits = EditSession(original)
    edits.add_spot("New ink")
    target = tmp_path / "unsafe.tif"
    with pytest.raises(ValueError, match="Alph"):
        save_tiff_copy(original, edits.document, target)
    assert not target.exists()
    save_tiff_copy(original, edits.document, target, SaveOptions(keep_layers=False))
    reopened = load_image(target)
    assert not reopened.metadata["has_photoshop_layers"]
    np.testing.assert_array_equal(reopened.samples, edits.document.samples)


@pytest.mark.parametrize("mode,count,bits", [("minisblack", 1, 8), ("miniswhite", 1, 16),
                                           ("rgb", 3, 8), ("rgb", 4, 8)])
def test_plain_image_add_spot_and_delete_last_spot_roundtrip(tmp_path, mode, count, bits):
    samples = np.full((7, 11, count), 50, dtype=f"uint{bits}")
    source = tmp_path / "plain.tif"
    tifffile.imwrite(source, samples[..., 0] if count == 1 else samples, photometric=mode, metadata=None,
                     extrasamples=[2] if count == 4 else None)
    original = load_image(source)
    edits = EditSession(original)
    index = edits.add_spot("White 新 🦁")
    added = tmp_path / "added.tif"
    save_tiff_copy(original, edits.document, added)
    reopened = load_image(added)
    assert reopened.channels[index].kind == "Spot"
    assert reopened.channels[index].name == "White 新 🦁"
    np.testing.assert_array_equal(reopened.samples[..., :index], original.samples)
    assert np.all(reopened.samples[..., index] == original.maximum)
    edits.delete_spot(index)
    target = tmp_path / "deleted-last.tif"
    save_tiff_copy(original, edits.document, target)
    reopened = load_image(target)
    assert reopened.channels == original.channels
    np.testing.assert_array_equal(reopened.samples, original.samples)
