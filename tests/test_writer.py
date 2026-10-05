"""Saved TIFF copies must retain printing channels and important metadata."""
from fractions import Fraction
import hashlib
import os
import struct

import numpy as np
import pytest
from PIL import Image, ImageCms
from psdtags import (PsdChannel, PsdChannelId, PsdCompressionType, PsdFormat,
                     PsdKey, PsdLayer, PsdLayers, PsdRectangle, PsdUserMask, TiffImageSourceData)
import tifffile

from tifview.editing import EditSession
from tifview.reader import load_image
from tifview.photoshop import without_thumbnails
from tifview.writer import SaveOptions, layer_preservation_reason, reduce_half, save_tiff_copy
from tools.make_demo import photoshop_resources, resource


def layered_source(tmp_path, depth=8, byteorder="<"):
    maximum = 2**depth - 1
    samples = np.full((9, 13, 6), maximum, dtype=f"uint{depth}")
    samples[..., 0] = maximum // 3
    samples[..., 1] = maximum // 2
    samples[3:7, 2:8, 4] = 0
    resources = photoshop_resources(["White Ink", "Varnish", "Saved mask"], [2, 2, 0], solidity=5)
    channels = [PsdChannel(PsdChannelId(index), PsdCompressionType.RLE, samples[..., index].copy()) for index in range(3)]
    layer = PsdLayer("Original layer", channels, PsdRectangle(0, 0, 9, 13))
    isd = TiffImageSourceData(PsdFormat.LE32BIT if byteorder == "<" else PsdFormat.BE32BIT,
                              PsdLayers(PsdKey.LAYER if depth == 8 else PsdKey.LAYER_16, [layer]), PsdUserMask())
    layer_bytes = isd.tobytes(compression=PsdCompressionType.RLE)
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    xmp = b'<x:xmpmeta xmlns:x="adobe:ns:meta/"/>'
    path = tmp_path / "layered.tif"
    tifffile.imwrite(path, samples, photometric="rgb", extrasamples=[0, 0, 0], compression="lzw",
                     metadata=None, resolution=((3600000, 10000), (3600000, 10000)), resolutionunit="inch",
                     byteorder=byteorder, iccprofile=profile,
                     extratags=[(34377, 1, len(resources), resources, False),
                                (37724, 7, len(layer_bytes), layer_bytes, False), (700, 1, len(xmp), xmp, False)])
    return load_image(path), layer_bytes


@pytest.mark.parametrize("depth", [8, 16])
@pytest.mark.parametrize("pyramid", [False, True])
def test_spot_copy_roundtrips_pixels_names_layers_profile_resolution_and_settings(tmp_path, depth, pyramid):
    original, layer_bytes = layered_source(tmp_path, depth)
    before_hash = hashlib.sha256(original.path.read_bytes()).hexdigest()
    session = EditSession(original)
    session.apply(3, (1, 1, 4, 4), np.full((3, 3), 255, np.uint8), 0)
    target = tmp_path / "edited.tif"
    save_tiff_copy(original, session.document, target, SaveOptions(pyramid=pyramid))
    reopened = load_image(target)
    np.testing.assert_array_equal(reopened.samples, session.document.samples)
    np.testing.assert_array_equal(reopened.samples[..., [0, 1, 2, 4, 5]], original.samples[..., [0, 1, 2, 4, 5]])
    assert [c.name for c in reopened.channels] == ["Red", "Green", "Blue", "White Ink", "Varnish", "Saved mask"]
    assert [c.kind for c in reopened.channels] == ["Process"] * 3 + ["Spot", "Spot", "Alpha mask"]
    assert reopened.photoshop_resources == original.photoshop_resources
    assert reopened.icc_profile == original.icc_profile
    assert hashlib.sha256(original.path.read_bytes()).hexdigest() == before_hash
    with tifffile.TiffFile(target) as tif:
        page = tif.pages[0]
        assert tif.byteorder == "<" and not tif.is_bigtiff
        assert int(page.compression) == 5
        assert int(page.planarconfig) == 1
        assert page.tags[37724].value == layer_bytes
        assert page.tags[700].value == b'<x:xmpmeta xmlns:x="adobe:ns:meta/"/>'
        assert page.tags[282].value[0] / page.tags[282].value[1] == 360
        assert page.tags[283].value[0] / page.tags[283].value[1] == 360
        assert int(page.tags[296].value) == 2
        assert len(page.subifds or ()) == int(pyramid)
        if pyramid:
            assert page.pages[0].shape == (5, 7, 6)
            np.testing.assert_array_equal(page.pages[0].asarray(), reduce_half(session.document.samples))
    layers = TiffImageSourceData.fromtiff(target)
    assert layers.layers.layers[0].name == "Original layer"
    assert all(c.compression == PsdCompressionType.RLE for c in layers.layers.layers[0].channels)


def test_process_edit_requires_explicit_merged_copy_instead_of_stale_layers(tmp_path):
    original, _ = layered_source(tmp_path)
    session = EditSession(original)
    session.apply(0, (1, 1, 4, 4), np.full((3, 3), 255, np.uint8), 0)
    assert "old image" in layer_preservation_reason(original, session.document)
    target = tmp_path / "edited.tif"
    with pytest.raises(ValueError, match="layers unchecked"):
        save_tiff_copy(original, session.document, target)
    assert not target.exists()
    save_tiff_copy(original, session.document, target, SaveOptions(keep_layers=False))
    reopened = load_image(target)
    assert not reopened.metadata["has_photoshop_layers"]
    np.testing.assert_array_equal(reopened.samples, session.document.samples)
    assert reopened.photoshop_resources == original.photoshop_resources


def test_big_endian_layer_copy_must_be_explicitly_merged_for_little_endian_preset(tmp_path):
    original, _ = layered_source(tmp_path, byteorder=">")
    target = tmp_path / "pc.tif"
    with pytest.raises(ValueError, match="Macintosh"):
        save_tiff_copy(original, original, target)
    save_tiff_copy(original, original, target, SaveOptions(keep_layers=False))
    assert load_image(target).metadata["byte_order"] == "IBM PC (little endian)"


def test_original_alias_existing_destination_and_external_source_change_are_protected(tmp_path):
    original, _ = layered_source(tmp_path)
    before = original.path.read_bytes()
    with pytest.raises(ValueError, match="original"):
        save_tiff_copy(original, original, original.path)
    alias = tmp_path / "alias.tif"
    os.link(original.path, alias)
    with pytest.raises(ValueError, match="original"):
        save_tiff_copy(original, original, alias, overwrite=True)
    existing = tmp_path / "existing.tif"
    existing.write_bytes(b"keep existing output")
    with pytest.raises(FileExistsError):
        save_tiff_copy(original, original, existing)
    assert existing.read_bytes() == b"keep existing output"
    assert original.path.read_bytes() == before
    original.path.write_bytes(before + b"external change")
    with pytest.raises(ValueError, match="changed after opening"):
        save_tiff_copy(original, original, tmp_path / "changed-source.tif")


def test_readback_failure_never_publishes_partial_output_or_overwrites_previous_copy(tmp_path, monkeypatch):
    original, _ = layered_source(tmp_path)
    target = tmp_path / "existing.tif"
    target.write_bytes(b"previous output")
    session = EditSession(original)
    session.apply(3, (1, 1, 4, 4), np.full((3, 3), 255, np.uint8), 0)
    monkeypatch.setattr("tifview.writer.load_image", lambda _: original)
    with pytest.raises(ValueError, match="read-back"):
        save_tiff_copy(original, session.document, target, overwrite=True)
    assert target.read_bytes() == b"previous output"
    assert not list(tmp_path.glob(".existing-*.tif"))


def test_odd_edge_pyramid_averages_native_uint16_without_overflow():
    samples = np.full((3, 3, 1), 65535, np.uint16)
    samples[0, 0, 0] = 0
    actual = reduce_half(samples)
    np.testing.assert_array_equal(actual[..., 0], [[49151, 65535], [65535, 65535]])


def test_edited_tiff_drops_stale_thumbnails_without_reserializing_unknown_or_channel_blocks(tmp_path):
    original, _ = layered_source(tmp_path)
    # Include odd-length payloads and a named unknown resource to test framing.
    unknown = b"8BIM\x75\x30\x03abc\x00\x00\x00\x03xyz\x00"
    thumbnail = struct.pack(">6I2H", 0, 1, 1, 4, 4, 4, 24, 1) + b"\x00\x00\x00\x00"
    resources = resource(1033, thumbnail) + original.photoshop_resources + unknown + resource(1036, thumbnail)
    path = tmp_path / "with-previews.tif"
    tifffile.imwrite(path, original.samples, photometric="rgb", extrasamples=[0, 0, 0], metadata=None,
                     extratags=[(34377, 1, len(resources), resources, False)])
    original = load_image(path)
    session = EditSession(original)
    session.apply(3, (1, 1, 4, 4), np.full((3, 3), 255, np.uint8), 0)
    target = save_tiff_copy(original, session.document, tmp_path / "new-preview.tif")
    assert load_image(target).photoshop_resources == without_thumbnails(resources)
    assert without_thumbnails(resources) == original.photoshop_resources[len(resource(1033, thumbnail)):-len(resource(1036, thumbnail))]
    assert unknown in load_image(target).photoshop_resources


def test_thumbnail_only_resource_tag_can_be_removed_after_process_edit(tmp_path):
    thumbnail = struct.pack(">6I2H", 0, 1, 1, 4, 4, 4, 24, 1) + b"\x00\x00\x00\x00"
    resources = resource(1036, thumbnail)
    path = tmp_path / "thumbnail-only.tif"
    tifffile.imwrite(path, np.full((5, 7, 3), 255, np.uint8), photometric="rgb", metadata=None,
                     extratags=[(34377, 1, len(resources), resources, False)])
    original = load_image(path)
    session = EditSession(original)
    session.apply(0, (1, 1, 4, 4), np.full((3, 3), 255, np.uint8), 0)
    target = save_tiff_copy(original, session.document, tmp_path / "no-stale-preview.tif")
    reopened = load_image(target)
    assert reopened.photoshop_resources is None
    np.testing.assert_array_equal(reopened.samples, session.document.samples)


def test_common_format_export_preserves_dpi(tmp_path):
    # 1. JPEG export with 360 DPI
    jpg_path = tmp_path / "photo360.jpg"
    Image.new("RGB", (8, 12), (10, 50, 90)).save(jpg_path, dpi=(360, 360))
    doc_jpg = load_image(jpg_path)
    assert doc_jpg.metadata["dpi"] == (360.0, 360.0)
    target_jpg = tmp_path / "exported_jpg.tif"
    save_tiff_copy(doc_jpg, doc_jpg, target_jpg)
    reopened_jpg = load_image(target_jpg)
    np.testing.assert_array_equal(reopened_jpg.samples, doc_jpg.samples)
    with tifffile.TiffFile(target_jpg) as tif:
        page = tif.pages[0]
        res = (page.tags.valueof(282), page.tags.valueof(283))
        assert (Fraction(*res[0]), Fraction(*res[1])) == (Fraction(360, 1), Fraction(360, 1))
        assert int(page.tags.valueof(296)) == 2

    # 2. PNG export with 360 DPI
    png_path = tmp_path / "photo360.png"
    Image.new("RGB", (8, 12), (10, 50, 90)).save(png_path, dpi=(360, 360))
    doc_png = load_image(png_path)
    target_png = tmp_path / "exported_png.tif"
    save_tiff_copy(doc_png, doc_png, target_png)
    with tifffile.TiffFile(target_png) as tif:
        page = tif.pages[0]
        res = (page.tags.valueof(282), page.tags.valueof(283))
        expected = Fraction(str(doc_png.metadata["dpi"][0])).limit_denominator(1_000_000)
        assert Fraction(*res[0]) == expected
        assert abs(float(Fraction(*res[0])) - 360) < 0.01

    # 3. Fractional DPI export
    frac_path = tmp_path / "frac.png"
    Image.new("RGB", (6, 6), (10, 50, 90)).save(frac_path)
    doc_frac = load_image(frac_path)
    doc_frac.metadata["dpi"] = (144.5, 289.0)
    target_frac = tmp_path / "frac.tif"
    save_tiff_copy(doc_frac, doc_frac, target_frac)
    with tifffile.TiffFile(target_frac) as tif:
        page = tif.pages[0]
        res = (page.tags.valueof(282), page.tags.valueof(283))
        assert (Fraction(*res[0]), Fraction(*res[1])) == (Fraction(289, 2), Fraction(289, 1))

    # 4. Rotated non-square DPI export: EXIF orientation 6 (90 CW)
    exif = Image.Exif()
    exif[274] = 6
    rot_path = tmp_path / "rotated.jpg"
    Image.new("RGB", (20, 10), (10, 50, 90)).save(rot_path, dpi=(300, 150), exif=exif)
    doc_rot = load_image(rot_path)
    assert doc_rot.metadata["dpi"] == (150.0, 300.0)
    target_rot = tmp_path / "rotated.tif"
    save_tiff_copy(doc_rot, doc_rot, target_rot)
    with tifffile.TiffFile(target_rot) as tif:
        page = tif.pages[0]
        res = (page.tags.valueof(282), page.tags.valueof(283))
        assert (Fraction(*res[0]), Fraction(*res[1])) == (Fraction(150, 1), Fraction(300, 1))

    # 5. Image with no DPI defaults to 72 DPI
    nodpi_path = tmp_path / "nodpi.png"
    Image.new("RGB", (4, 4), (10, 50, 90)).save(nodpi_path)
    doc_nodpi = load_image(nodpi_path)
    assert doc_nodpi.metadata["dpi"] is None
    target_nodpi = tmp_path / "nodpi.tif"
    save_tiff_copy(doc_nodpi, doc_nodpi, target_nodpi)
    with tifffile.TiffFile(target_nodpi) as tif:
        page = tif.pages[0]
        res = (page.tags.valueof(282), page.tags.valueof(283))
        assert (Fraction(*res[0]), Fraction(*res[1])) == (Fraction(72, 1), Fraction(72, 1))

    # 6. Edited common-format image can be saved and preserves DPI
    session = EditSession(doc_jpg)
    session.apply(0, (1, 1, 4, 4), np.full((3, 3), 200, np.uint8), 200)
    target_edited = tmp_path / "edited_jpg.tif"
    save_tiff_copy(doc_jpg, session.document, target_edited)
    reopened_edited = load_image(target_edited)
    np.testing.assert_array_equal(reopened_edited.samples, session.document.samples)
    with tifffile.TiffFile(target_edited) as tif:
        res = (tif.pages[0].tags.valueof(282), tif.pages[0].tags.valueof(283))
        assert (Fraction(*res[0]), Fraction(*res[1])) == (Fraction(360, 1), Fraction(360, 1))


def test_common_format_export_preserves_png_transparency(tmp_path):
    # RGB PNG with colour-key transparency
    im_rgb = Image.new("RGB", (4, 4), (10, 20, 30))
    im_rgb.putpixel((0, 0), (255, 0, 0))
    png_path = tmp_path / "trans.png"
    im_rgb.save(png_path, transparency=(255, 0, 0), dpi=(300, 300))
    doc_rgb = load_image(png_path)
    target_rgb = tmp_path / "trans.tif"
    save_tiff_copy(doc_rgb, doc_rgb, target_rgb)
    reopened_rgb = load_image(target_rgb)
    np.testing.assert_array_equal(reopened_rgb.samples, doc_rgb.samples)
    assert reopened_rgb.channels[3].kind == "Transparency"
    assert not reopened_rgb.channels[3].associated

    # 16-bit Grayscale PNG with colour-key transparency
    arr16 = np.array([[1000, 2000], [3000, 4000]], dtype=np.uint16)
    png16_path = tmp_path / "trans16.png"
    Image.fromarray(arr16).save(png16_path, format="PNG", transparency=2000, dpi=(300, 300))
    doc_16 = load_image(png16_path)
    target_16 = tmp_path / "trans16.tif"
    save_tiff_copy(doc_16, doc_16, target_16)
    reopened_16 = load_image(target_16)
    assert reopened_16.bits == 16
    np.testing.assert_array_equal(reopened_16.samples, doc_16.samples)
    assert reopened_16.channels[1].kind == "Transparency"
