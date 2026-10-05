import hashlib
import struct

import numpy as np
import pytest
import tifffile
from PIL import Image, ImageCms

from tifview.photoshop import DisplayInfo, read_resources
from tifview.reader import UnsupportedImageError, load_image
from tifview.render import mask_coverage, render
from tools.make_demo import photoshop_resources, resource


def write_tiff(tmp_path, data, mode="rgb", extras=(), resources=None, **kwargs):
    path = tmp_path / "test.tif"
    tags = kwargs.pop("extratags", [])
    if resources:
        tags.append((34377, 7, len(resources), resources, False))
    if data.ndim == 3:
        kwargs.setdefault("planarconfig", "contig")
    tifffile.imwrite(path, data, photometric=mode, extrasamples=extras,
                     metadata=None, extratags=tags, **kwargs)
    return path


@pytest.mark.parametrize("depth", [8, 16])
@pytest.mark.parametrize("compression", [None, "lzw", "deflate", "adobe_deflate", "packbits"])
@pytest.mark.parametrize("byteorder", ["<", ">"])
@pytest.mark.parametrize("planar", ["contig", "separate"])
def test_raw_process_spot_and_alpha_survive_storage_options(tmp_path, depth, compression, byteorder, planar):
    rng = np.random.default_rng(12)
    samples = rng.integers(0, 2**depth, (7, 11, 7), dtype=f"uint{depth}")
    encoded = np.moveaxis(samples, -1, 0) if planar == "separate" else samples
    ps = photoshop_resources(["Transparency", "W白", "Varnish"], [1, 2, 2])
    path = write_tiff(tmp_path, encoded, "separated", [1, 0, 0], ps,
                      compression=compression, byteorder=byteorder, planarconfig=planar)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    doc = load_image(path)
    assert [c.name for c in doc.channels] == ["Cyan", "Magenta", "Yellow", "Black", "Transparency", "W白", "Varnish"]
    assert [c.kind for c in doc.channels] == ["Process"] * 4 + ["Transparency", "Spot", "Spot"]
    np.testing.assert_array_equal(doc.samples, samples)
    assert not doc.samples.flags.writeable
    for index in range(7):
        assert render(doc, index).shape == (7, 11, 3)
    render(doc, overlays=True, colored=True)
    np.testing.assert_array_equal(doc.samples, samples)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_transparency_excluded_from_resources_does_not_shift_spot_names(tmp_path):
    data = np.full((4, 5, 6), 255, np.uint8)
    ps = photoshop_resources(["White", "Varnish"], [2, 2])
    doc = load_image(write_tiff(tmp_path, data, extras=[2, 0, 0], resources=ps))
    assert doc.channels[3].kind == "Transparency"
    assert [c.name for c in doc.channels[4:]] == ["White", "Varnish"]


def test_metadata_count_mismatch_is_withheld_not_guessed(tmp_path):
    data = np.zeros((4, 5, 6), np.uint8)
    ps = photoshop_resources(["Varnish"], [2])
    doc = load_image(write_tiff(tmp_path, data, extras=[0, 0, 0], resources=ps))
    assert [c.kind for c in doc.channels[3:]] == ["Unknown"] * 3
    assert [c.name for c in doc.channels[3:]] == ["Extra 1", "Extra 2", "Extra 3"]
    assert any("mapping withheld" in x for x in doc.warnings)


def test_name_alone_cannot_identify_a_spot(tmp_path):
    data = np.zeros((4, 5, 4), np.uint8)
    ps = resource(1006, b"\x05White")
    doc = load_image(write_tiff(tmp_path, data, extras=[0], resources=ps))
    assert doc.channels[3].name == "White"
    assert doc.channels[3].kind == "Unknown"


def test_transparency_spot_conflict_is_visible_and_not_used_as_alpha(tmp_path):
    ps = photoshop_resources(["White"], [2])
    data = np.full((4, 5, 4), 255, np.uint8)
    data[..., 3] = 0
    doc = load_image(write_tiff(tmp_path, data, extras=[2], resources=ps))
    assert doc.channels[3].kind == "Unknown"
    assert any("Conflicting" in x for x in doc.warnings)
    assert np.all(render(doc) == 255)


@pytest.mark.parametrize("legacy", [False, True])
def test_display_modes_and_ink_polarity(tmp_path, legacy):
    ps = photoshop_resources(["White", "Alpha", "Inverse"], [2, 0, 1], legacy=legacy)
    data = np.full((4, 5, 6), 255, np.uint8)
    data[0, 0, 3:] = 0
    doc = load_image(write_tiff(tmp_path, data, extras=[0, 0, 0], resources=ps))
    assert [c.kind for c in doc.channels[3:]] == ["Spot", "Alpha mask", "Alpha mask"]
    assert mask_coverage(doc, doc.channels[3])[0, 0] == 1
    assert mask_coverage(doc, doc.channels[4])[0, 0] == 1
    assert mask_coverage(doc, doc.channels[5])[0, 0] == 0
    assert np.all(render(doc, selected=3)[0, 0] == 0)
    assert np.all(render(doc, selected=3)[0, 1] == 255)
    colored = render(doc, selected=3, colored=True, opacity=1, colors={3: (255, 0, 255)})
    np.testing.assert_array_equal(colored[0, 0], [255, 0, 255])


@pytest.mark.parametrize("data", [b"8BIM", resource(1077, b"\0\0\0\x02"),
                                      resource(1077, b"\0\0\0\x01broken")])
def test_corrupt_or_unsupported_resources_produce_warnings(data):
    parsed = read_resources(data)
    assert parsed.warnings
    assert not parsed.displays


def test_unknown_resource_ids_are_retained_and_unrelated_duplicates_allowed():
    ps = resource(1096, b"test") + resource(1097, b"other") + resource(1097, b"repeat")
    assert read_resources(ps).resource_ids == [1096, 1097, 1097]


def test_spot_metadata_duplicates_are_not_silently_accepted():
    parsed = read_resources(resource(1006, b"\x01A") * 2)
    assert parsed.warnings
    assert not parsed.names


def test_rgb_visibility_and_alpha_keep_raw_samples(tmp_path):
    data = np.zeros((4, 5, 4), np.uint8)
    data[..., :3] = [255, 100, 40]
    data[..., 3] = 255
    doc = load_image(write_tiff(tmp_path, data, extras=[2]))
    np.testing.assert_array_equal(render(doc)[0, 0], [255, 100, 40])
    np.testing.assert_array_equal(render(doc, visible={1, 2})[0, 0], [0, 100, 40])
    assert doc.channels[3].kind == "Transparency"


def test_premultiplied_rgb_is_unassociated_before_preview(tmp_path):
    data = np.full((4, 5, 4), 128, np.uint8)
    data[..., 1:3] = 0
    doc = load_image(write_tiff(tmp_path, data, extras=[1]))
    np.testing.assert_array_equal(render(doc, visible={0, 1, 2})[0, 0], [255, 0, 0])
    assert render(doc)[0, 0, 0] > 240
    np.testing.assert_array_equal(doc.samples, data)


def test_premultiplied_cmyk_unassociates_ink_before_conversion(tmp_path):
    data = np.zeros((4, 5, 5), np.uint8)
    data[..., 0] = data[..., 4] = 128
    doc = load_image(write_tiff(tmp_path, data, "separated", [1]))
    np.testing.assert_array_equal(render(doc, visible={0, 1, 2, 3})[0, 0], [0, 255, 255])
    assert render(doc)[0, 0, 1] > 240


def test_cmyk_grayscale_uses_black_for_ink_and_preserves_raw_value(tmp_path):
    data = np.zeros((4, 5, 4), np.uint8)
    data[0, 0, 0] = 255
    doc = load_image(write_tiff(tmp_path, data, "separated"))
    np.testing.assert_array_equal(render(doc, selected=0)[0, 0], [0, 0, 0])
    np.testing.assert_array_equal(render(doc)[0, 0], [0, 255, 255])
    assert doc.samples[0, 0, 0] == 255


def test_uint16_grayscale_uses_fixed_full_range_without_auto_contrast(tmp_path):
    data = np.full((4, 5), 32768, np.uint16)
    data[0, :2] = [0, 65535]
    doc = load_image(write_tiff(tmp_path, data, "minisblack"))
    np.testing.assert_array_equal(render(doc)[0, :3, 0], [0, 255, 128])
    assert doc.bits == 16
    np.testing.assert_array_equal(doc.samples[..., 0], data)


@pytest.mark.parametrize("orientation,expected", [
    (1, [[1, 2, 3], [4, 5, 6]]), (2, [[3, 2, 1], [6, 5, 4]]),
    (3, [[6, 5, 4], [3, 2, 1]]), (4, [[4, 5, 6], [1, 2, 3]]),
    (5, [[1, 4], [2, 5], [3, 6]]), (6, [[4, 1], [5, 2], [6, 3]]),
    (7, [[6, 3], [5, 2], [4, 1]]), (8, [[3, 6], [2, 5], [1, 4]]),
])
def test_orientation_changes_display_only(tmp_path, orientation, expected):
    data = np.array([[1, 2, 3], [4, 5, 6]], np.uint8)
    path = write_tiff(tmp_path, data, "minisblack", extratags=[(274, 3, 1, orientation, False)])
    doc = load_image(path)
    np.testing.assert_array_equal(doc.display_samples[..., 0], expected)
    np.testing.assert_array_equal(doc.samples[..., 0], data)


def test_palette_and_white_is_zero(tmp_path):
    data = np.zeros((4, 5), np.uint8)
    cmap = np.zeros((3, 256), np.uint16)
    cmap[0, 0] = 65535
    doc = load_image(write_tiff(tmp_path, data, "palette", colormap=cmap))
    np.testing.assert_array_equal(render(doc)[0, 0], [255, 0, 0])
    doc = load_image(write_tiff(tmp_path, data, "miniswhite"))
    assert np.all(render(doc) == 255)


def test_tiff_pyramid_is_not_misidentified_as_channels(tmp_path):
    path = tmp_path / "pyramid.tif"
    with tifffile.TiffWriter(path) as writer:
        writer.write(np.zeros((8, 12, 3), np.uint8), photometric="rgb", subifds=1, metadata=None)
        writer.write(np.zeros((4, 6, 3), np.uint8), photometric="rgb", subfiletype=1, metadata=None)
    doc = load_image(path)
    assert len(doc.channels) == 3
    assert doc.width == 12
    assert doc.metadata["pyramid_subifds"] == 1


@pytest.mark.parametrize("header", [
    b"II*\0\0\0\0\0", b"MM\0*\0\0\0\0",
    b"II+\0\x08\0\0\0" + b"\0" * 8,
])
def test_header_only_tiff_has_actionable_error_instead_of_index_error(tmp_path, header):
    path = tmp_path / "incomplete.tif"
    path.write_bytes(header)
    with pytest.raises(UnsupportedImageError, match="contains no image data"):
        load_image(path)


def test_float_and_unimplemented_photometric_are_rejected(tmp_path):
    with pytest.raises(UnsupportedImageError):
        load_image(write_tiff(tmp_path, np.zeros((4, 5), np.float32), "minisblack"))
    with pytest.raises(UnsupportedImageError):
        load_image(write_tiff(tmp_path, np.zeros((4, 5, 3), np.uint8), "cielab"))


def test_embedded_icc_is_used_only_for_composite(tmp_path):
    data = np.full((4, 5, 3), 120, np.uint8)
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    path = write_tiff(tmp_path, data, iccprofile=profile)
    doc = load_image(path)
    assert doc.icc_profile == profile
    assert doc.icc_transform is not None
    np.testing.assert_array_equal(render(doc), data)
    np.testing.assert_array_equal(render(doc, selected=0)[..., 0], data[..., 0])


@pytest.mark.parametrize("format", ["PNG", "JPEG", "BMP", "WEBP"])
def test_common_formats(tmp_path, format):
    path = tmp_path / f"test.{format.lower()}"
    data = np.full((10, 12, 3), 100, np.uint8)
    Image.fromarray(data).save(path, format=format)
    doc = load_image(path)
    assert doc.samples.shape == (10, 12, 3)
    assert len(doc.channels) == 3
    assert render(doc).shape == (10, 12, 3)


def test_photoshop_resource_preview_cmyk_polarity_is_inverse_tiff():
    assert DisplayInfo(2, (0, 65535, 65535, 65535), 100, 2, 1077).rgb == (0, 255, 255)
    assert DisplayInfo(7, (10000, 0, 0, 0), 100, 2, 1077).rgb == (255, 255, 255)


def test_common_format_dpi_preservation_and_rotation(tmp_path, monkeypatch):
    # Preserves DPI on JPEG and PNG
    jpg_path = tmp_path / "photo.jpg"
    Image.new("RGB", (10, 10)).save(jpg_path, dpi=(300, 300))
    doc_jpg = load_image(jpg_path)
    assert doc_jpg.metadata["dpi"] == (300.0, 300.0)

    # Fractional DPI from PNG pHYs chunk
    png_frac = tmp_path / "frac.png"
    Image.new("RGB", (10, 10)).save(png_frac, dpi=(144.5, 288.75))
    doc_png = load_image(png_frac)
    assert abs(doc_png.metadata["dpi"][0] - 144.5) < 0.01
    assert abs(doc_png.metadata["dpi"][1] - 288.75) < 0.01

    # Exact fractional float tuple preservation
    orig_open = Image.open

    def mock_open(*args, **kwargs):
        im = orig_open(*args, **kwargs)
        im.info["dpi"] = (144.5, 288.75)
        return im

    monkeypatch.setattr("PIL.Image.open", mock_open)
    doc_frac = load_image(png_frac)
    assert doc_frac.metadata["dpi"] == (144.5, 288.75)
    monkeypatch.undo()

    # Rotated non-square DPI: EXIF orientation 6 (90 CW) swaps axes and DPI
    rot_path = tmp_path / "rotated.jpg"
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (20, 10)).save(rot_path, exif=exif, dpi=(300, 150))
    doc_rot = load_image(rot_path)
    assert doc_rot.width == 10 and doc_rot.height == 20
    assert doc_rot.metadata["dpi"] == (150.0, 300.0)

    # Non-swapping EXIF orientation 3 (180 deg) preserves axis ordering
    rot3_path = tmp_path / "rot3.jpg"
    exif[274] = 3
    Image.new("RGB", (20, 10)).save(rot3_path, exif=exif, dpi=(300, 150))
    doc_rot3 = load_image(rot3_path)
    assert doc_rot3.metadata["dpi"] == (300.0, 150.0)

    # Missing DPI yields None and no warnings
    nodpi_path = tmp_path / "nodpi.png"
    Image.new("RGB", (10, 10)).save(nodpi_path)
    doc_nodpi = load_image(nodpi_path)
    assert doc_nodpi.metadata["dpi"] is None
    assert not any("resolution" in w.lower() for w in doc_nodpi.warnings)

    # Invalid DPI emits warning
    invalid_path = tmp_path / "invalid.png"
    Image.new("RGB", (10, 10)).save(invalid_path, dpi=(0, 0))
    doc_invalid = load_image(invalid_path)
    assert doc_invalid.metadata["dpi"] is None
    assert any("Invalid image resolution" in w for w in doc_invalid.warnings)


def test_png_transparency_color_keys(tmp_path):
    # RGB colour key
    rgb_path = tmp_path / "key_rgb.png"
    im_rgb = Image.new("RGB", (4, 4), (10, 20, 30))
    im_rgb.putpixel((0, 0), (255, 0, 0))
    im_rgb.save(rgb_path, format="PNG", transparency=(255, 0, 0))
    doc_rgb = load_image(rgb_path)
    assert len(doc_rgb.channels) == 4
    assert doc_rgb.channels[3].kind == "Transparency"
    assert not doc_rgb.channels[3].associated
    assert doc_rgb.samples[0, 0, 3] == 0
    assert doc_rgb.samples[0, 1, 3] == 255
    assert tuple(doc_rgb.samples[0, 0, :3]) == (255, 0, 0)

    # 8-bit Grayscale colour key
    gray_path = tmp_path / "key_gray.png"
    im_gray = Image.new("L", (4, 4), 100)
    im_gray.putpixel((0, 0), 50)
    im_gray.save(gray_path, format="PNG", transparency=50)
    doc_gray = load_image(gray_path)
    assert len(doc_gray.channels) == 2
    assert doc_gray.channels[1].kind == "Transparency"
    assert doc_gray.samples[0, 0, 1] == 0
    assert doc_gray.samples[0, 1, 1] == 255
    assert doc_gray.samples[0, 0, 0] == 50

    # 16-bit Grayscale colour key
    gray16_path = tmp_path / "key_gray16.png"
    arr16 = np.array([[1000, 2000], [3000, 4000]], dtype=np.uint16)
    Image.fromarray(arr16).save(gray16_path, format="PNG", transparency=2000)
    doc_gray16 = load_image(gray16_path)
    assert doc_gray16.bits == 16
    assert doc_gray16.samples.dtype == np.uint16
    assert len(doc_gray16.channels) == 2
    assert doc_gray16.channels[1].kind == "Transparency"
    assert doc_gray16.samples[0, 1, 1] == 0
    assert doc_gray16.samples[0, 0, 1] == 65535
    assert doc_gray16.samples[0, 1, 0] == 2000

    # 1-bit colour key
    bit_path = tmp_path / "key_1bit.png"
    im_bit = Image.new("1", (4, 4), 1)
    im_bit.putpixel((0, 0), 0)
    im_bit.save(bit_path, format="PNG", transparency=0)
    doc_bit = load_image(bit_path)
    assert len(doc_bit.channels) == 2
    assert doc_bit.channels[1].kind == "Transparency"
    assert doc_bit.samples[0, 0, 1] == 0
    assert doc_bit.samples[0, 1, 1] == 255

    # Palette with transparency does not duplicate alpha
    pal_path = tmp_path / "palette_trans.png"
    im_pal = Image.new("P", (4, 4), 0)
    im_pal.save(pal_path, format="PNG", transparency=0)
    doc_pal = load_image(pal_path)
    assert len(doc_pal.channels) == 4
    assert [c.kind for c in doc_pal.channels] == ["Process", "Process", "Process", "Transparency"]

    # RGBA does not duplicate alpha
    rgba_path = tmp_path / "rgba.png"
    Image.new("RGBA", (4, 4), (10, 20, 30, 255)).save(rgba_path, format="PNG")
    doc_rgba = load_image(rgba_path)
    assert len(doc_rgba.channels) == 4
    assert [c.kind for c in doc_rgba.channels] == ["Process", "Process", "Process", "Transparency"]


def test_image_limits_rejection_and_exact_boundaries(tmp_path, monkeypatch):
    # Common format pixel boundary
    monkeypatch.setattr("tifview.reader._MAX_PIXELS", 50)
    ok_path = tmp_path / "ok_pixels.png"
    Image.new("RGB", (5, 10)).save(ok_path)
    doc_ok = load_image(ok_path)
    assert doc_ok.width * doc_ok.height == 50

    over_path = tmp_path / "over_pixels.png"
    Image.new("RGB", (5, 11)).save(over_path)
    with pytest.raises(UnsupportedImageError, match="40-million-pixel or 512 MiB decoded limit"):
        load_image(over_path)

    # Common format decoded bytes boundary
    monkeypatch.setattr("tifview.reader._MAX_PIXELS", 10_000)
    monkeypatch.setattr("tifview.reader._MAX_DECODED_BYTES", 300)
    exact_bytes_path = tmp_path / "exact_bytes.png"
    Image.new("RGB", (10, 10)).save(exact_bytes_path)  # 100 * 3 = 300 bytes
    doc_bytes = load_image(exact_bytes_path)
    assert doc_bytes.samples.nbytes == 300

    over_bytes_path = tmp_path / "over_bytes.png"
    Image.new("RGB", (10, 11)).save(over_bytes_path)  # 110 * 3 = 330 bytes
    with pytest.raises(UnsupportedImageError, match="40-million-pixel or 512 MiB decoded limit"):
        load_image(over_bytes_path)

    # TIFF format respects limits
    monkeypatch.setattr("tifview.reader._MAX_PIXELS", 50)
    with pytest.raises(UnsupportedImageError, match="40-million-pixel or 512 MiB decoded limit"):
        load_image(write_tiff(tmp_path, np.zeros((6, 10, 3), np.uint8)))
