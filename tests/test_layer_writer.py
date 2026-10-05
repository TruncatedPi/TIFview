"""Saved layer controls agree with merged TIFF pixels and original PSD data."""
from dataclasses import replace
import hashlib
import struct
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import ImageCms
from psdtags import (PsdChannel, PsdChannelId, PsdCompressionType, PsdFormat, PsdKey,
                     PsdLayerFlag, PsdLayerMask, PsdRectangle, PsdUnknown, TiffImageSourceData)
import tifffile

from tifview.editing import EditSession
from tifview.layerediting import LayerState, merged_pixels_match
from tifview.layers import LayerStack
from tifview.reader import load_image
from tifview.writer import SaveOptions, layer_preservation_reason, save_tiff_copy
from tools.make_demo import photoshop_resources, resource
from test_layers import encode_layers, parent, raster


def half(samples):
    """Small independent averaging oracle for the saved pyramid."""
    height, width, count = samples.shape
    result = np.empty(((height + 1) // 2, (width + 1) // 2, count), samples.dtype)
    for y in range(result.shape[0]):
        for x in range(result.shape[1]):
            block = samples[2*y:2*y+2, 2*x:2*x+2].astype(np.uint64)
            number = block.shape[0] * block.shape[1]
            result[y, x] = (block.sum(axis=(0, 1)) + number // 2) // number
    return result


def source(tmp_path, depth=8, mode="RGB", associated=None):
    doc = parent(depth, mode, orientation=6)
    maximum, base = doc.maximum, doc.base_count
    y, x, c = np.indices(doc.samples.shape)
    bottom = ((y * 17 + x * 23 + c * 59 + 35) * (257 if depth == 16 else 1) % (maximum + 1)).astype(doc.samples.dtype)
    top = np.empty((3, 4, base), doc.samples.dtype)
    for index in range(base):
        top[..., index] = (maximum * (index + 1)) // (base + 1)
    alpha = np.full((3, 4), maximum // 2, doc.samples.dtype)
    mask = np.array([[0, maximum], [maximum, maximum // 2]], doc.samples.dtype)
    psd_bottom = maximum - bottom if mode == "CMYK" else bottom
    psd_top = maximum - top if mode == "CMYK" else top
    opaque = PsdUnknown(PsdKey.UNKNOWN, PsdFormat.LE32BIT, b"private opaque descriptor bytes")
    top_layer = raster(psd_top, alpha, (1, 2, 4, 6), "Top", info=[opaque])
    top_layer.mask = PsdLayerMask(default_color=255, rectangle=PsdRectangle(2, 3, 4, 5))
    top_layer.channels.append(PsdChannel(PsdChannelId.USER_LAYER_MASK, data=mask.copy()))
    layer_bytes = encode_layers([raster(psd_bottom, name="Bottom"), top_layer,
                                 raster(psd_bottom, name="Hidden", visible=False)], depth)
    stack = LayerStack(layer_bytes, doc)
    merged = stack.composite_samples()
    pixels = merged[..., :base]
    values, extras = [pixels], []
    if associated is not None:
        values.append(merged[..., -1, None])
        extras.append(1 if associated else 2)
    for shade in (22, 63, 188):
        values.append(np.full((*pixels.shape[:2], 1), shade * (257 if depth == 16 else 1), pixels.dtype))
        extras.append(0)
    samples = np.concatenate(values, axis=-1)
    resources = photoshop_resources(["White", "Saved selection", "Varnish"], [2, 1, 2], solidity=27)
    resources += resource(1033, struct.pack(">6I2H", 0, 1, 1, 4, 4, 4, 24, 1) + b"\0" * 4)
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    xmp = b'<x:xmpmeta xmlns:x="adobe:ns:meta/"/>'
    filename = tmp_path / "layered-source.tif"
    settings = dict(photometric="rgb" if mode == "RGB" else "separated", extrasamples=extras,
                    compression="lzw", predictor=True, metadata=None, planarconfig="contig")
    with tifffile.TiffWriter(filename, byteorder="<") as writer:
        writer.write(samples, subifds=1, iccprofile=profile, resolution=(360, 360), resolutionunit="inch",
                     extratags=[(274, 3, 1, 6, False), (34377, 1, len(resources), resources, False),
                                (37724, 7, len(layer_bytes), layer_bytes, False), (700, 1, len(xmp), xmp, False)],
                     **settings)
        writer.write(half(samples), subfiletype=1, resolution=(180, 180), resolutionunit="inch",
                     extratags=[(274, 3, 1, 6, False)], **settings)
    original = load_image(filename)
    return original, layer_bytes, bottom, top, alpha, mask


def attach(original):
    session = EditSession(original)
    session.attach_layers(LayerStack.from_document(original))
    return session


def isolated_top(original, top, alpha, mask):
    """Oracle independent of the layer renderer: straight top pixels + alpha."""
    samples = np.full((*original.samples.shape[:2], original.base_count),
                      0 if original.color_mode == "CMYK" else original.maximum, original.samples.dtype)
    coverage = np.zeros(original.samples.shape[:2], np.float64)
    local_mask = np.ones(alpha.shape, np.float64)
    local_mask[1:3, 1:3] = mask.astype(np.float64) / original.maximum
    local_alpha = alpha.astype(np.float64) / original.maximum * local_mask
    coverage[1:4, 2:6] = local_alpha
    samples[1:4, 2:6] = np.where(local_alpha[..., None] > 0, top,
                                0 if original.color_mode == "CMYK" else original.maximum)
    return samples, np.rint(coverage * original.maximum).astype(original.samples.dtype)


@pytest.mark.parametrize("depth", [8, 16])
@pytest.mark.parametrize("mode", ["RGB", "CMYK"])
@pytest.mark.parametrize("associated", [None, False, True])
def test_saved_visibility_matches_independent_mask_oracle_and_retains_channels_layers_print_metadata(
        tmp_path, depth, mode, associated):
    original, raw, bottom, top, alpha, mask = source(tmp_path, depth, mode, associated)
    before = hashlib.sha256(original.path.read_bytes()).digest()
    session = attach(original)
    session.set_layer_visibility(0, False)
    expected_process, expected_alpha = isolated_top(original, top, alpha, mask)
    if associated:
        expected_process = ((expected_process.astype(np.uint64) * expected_alpha[..., None] + original.maximum // 2) //
                            original.maximum).astype(original.samples.dtype)
    np.testing.assert_array_equal(session.document.samples[..., :original.base_count], expected_process)
    transparency = next(c for c in session.document.channels if c.kind == "Transparency")
    np.testing.assert_array_equal(session.document.samples[..., transparency.index], expected_alpha)
    assert layer_preservation_reason(original, session.document) is None
    target = tmp_path / "hidden-bottom.tif"
    save_tiff_copy(original, session.document, target)
    reopened = load_image(target)
    np.testing.assert_array_equal(reopened.samples, session.document.samples)
    assert reopened.orientation == 6 and reopened.bits == depth and reopened.color_mode == mode
    assert reopened.icc_profile == original.icc_profile
    for original_channel in original.channels:
        if original_channel.kind in ("Spot", "Alpha mask"):
            current_channel = next(c for c in reopened.channels if c.name == original_channel.name)
            assert current_channel.kind == original_channel.kind and current_channel.display == original_channel.display
            np.testing.assert_array_equal(reopened.samples[..., current_channel.index], original.samples[..., original_channel.index])
    assert hashlib.sha256(original.path.read_bytes()).digest() == before
    with tifffile.TiffFile(target) as tif:
        page = tif.pages[0]
        assert tif.byteorder == "<" and not tif.is_bigtiff
        assert int(page.compression) == 5 and int(page.planarconfig) == 1
        assert list(map(int, page.extrasamples)) == session.document.metadata["extra_samples"]
        assert page.tags[282].value[0] / page.tags[282].value[1] == 360
        assert page.tags[283].value[0] / page.tags[283].value[1] == 360
        assert int(page.tags[296].value) == 2
        assert page.tags[700].value == b'<x:xmpmeta xmlns:x="adobe:ns:meta/"/>'
        assert len(page.subifds) == 1
        np.testing.assert_array_equal(page.pages[0].asarray(), half(session.document.samples))
        saved_raw = bytes(page.tags[37724].value)
    # Independent psdtags parsing confirms the saved flags/masks and native
    # layer data rather than trusting only the viewer's parser.
    parsed = TiffImageSourceData.fromtiff(target)
    assert [layer.name for layer in parsed.layers.layers] == ["Bottom", "Top", "Hidden"]
    assert parsed.layers.layers[0].flags & PsdLayerFlag.VISIBLE
    assert not parsed.layers.layers[1].flags & PsdLayerFlag.VISIBLE
    top_channels = {int(channel.channelid): channel for channel in parsed.layers.layers[1].channels}
    np.testing.assert_array_equal(top_channels[-2].data, mask)
    assert all(channel.compression == PsdCompressionType.RLE for layer in parsed.layers.layers for channel in layer.channels)
    before_stack, after_stack = session.document.layer_stack, LayerStack.from_document(reopened)
    assert len(raw) == len(saved_raw)
    for before_layer, after_layer in zip(before_stack.layers, after_stack.layers):
        for before_span, after_span in zip(before_layer._channel_spans, after_layer._channel_spans):
            assert raw[slice(*before_span)] == saved_raw[slice(*after_span)]
        record_before = bytearray(raw[before_layer._record_start:before_layer._record_end])
        record_after = bytearray(saved_raw[after_layer._record_start:after_layer._record_end])
        record_before[before_layer._flags_offset - before_layer._record_start] &= ~2
        record_after[after_layer._flags_offset - after_layer._record_start] &= ~2
        assert record_before == record_after


@pytest.mark.parametrize("depth,mode", [(8, "RGB"), (16, "RGB"), (8, "CMYK"), (16, "CMYK")])
def test_saved_order_reorders_whole_records_and_preserves_compressed_pixels(tmp_path, depth, mode):
    original, raw, bottom, top, alpha, mask = source(tmp_path, depth, mode)
    session = attach(original)
    session.move_layer(0, -1)
    assert session.document.layer_state.order == (2, 0, 1)
    np.testing.assert_array_equal(session.document.samples[..., :original.base_count], bottom)
    target = tmp_path / "reordered.tif"
    save_tiff_copy(original, session.document, target)
    parsed = TiffImageSourceData.fromtiff(target)
    assert [layer.name for layer in parsed.layers.layers] == ["Top", "Bottom", "Hidden"]
    reopened = load_image(target)
    stack = LayerStack.from_document(reopened)
    for original_layer in session.document.layer_stack.layers:
        moved_layer = next(layer for layer in stack.layers if layer.name == original_layer.name)
        for old_span, new_span in zip(original_layer._channel_spans, moved_layer._channel_spans):
            assert raw[slice(*old_span)] == stack.data[slice(*new_span)]
    np.testing.assert_array_equal(reopened.samples, session.document.samples)


def test_layer_changes_spot_management_and_multiple_save_copies_keep_native_masks(tmp_path):
    original, *_ = source(tmp_path)
    session = attach(original)
    session.set_layer_visibility(0, False)
    added = session.add_spot("Proof white", source_index=original.base_count)
    session.move_spot(added, 1)
    session.delete_spot(next(c.index for c in session.document.channels if c.name == "Varnish"))
    before_save = session.document.samples.copy()
    target = tmp_path / "layer-and-spots.tif"
    save_tiff_copy(original, session.document, target)
    np.testing.assert_array_equal(load_image(target).samples, before_save)
    assert merged_pixels_match(session.document)
    session.undo()
    session.undo()
    session.undo()
    session.undo()
    assert not session.dirty
    baseline_copy = tmp_path / "restored.tif"
    save_tiff_copy(original, session.document, baseline_copy)
    np.testing.assert_array_equal(load_image(baseline_copy).samples, original.samples)
    with tifffile.TiffFile(baseline_copy) as tif:
        assert bytes(tif.pages[0].tags[37724].value) == session.document.layer_stack.data


def test_paint_after_layer_change_requires_explicit_merged_copy(tmp_path):
    original, *_ = source(tmp_path)
    session = attach(original)
    session.set_layer_visibility(0, False)
    session.apply(0, (2, 1, 4, 3), np.full((2, 2), 255, np.uint8), 12)
    target = tmp_path / "stale.tif"
    assert "painted separately" in layer_preservation_reason(original, session.document)
    with pytest.raises(ValueError, match="painted separately"):
        save_tiff_copy(original, session.document, target)
    assert not target.exists()
    save_tiff_copy(original, session.document, target, SaveOptions(keep_layers=False))
    reopened = load_image(target)
    assert not reopened.metadata["has_photoshop_layers"]
    np.testing.assert_array_equal(reopened.samples, session.document.samples)


def test_independent_writer_proof_rejects_forged_current_pixel_snapshot(tmp_path):
    original, *_ = source(tmp_path)
    session = attach(original)
    session.set_layer_visibility(0, False)
    forged_pixels = session.document.samples.copy()
    forged_pixels[1, 2, 0] ^= 1
    forged = replace(session.document, samples=forged_pixels, layer_merged_samples=forged_pixels)
    assert layer_preservation_reason(original, forged) is None
    target = tmp_path / "forged-pixels.tif"
    with pytest.raises(ValueError, match="do not match"):
        save_tiff_copy(original, forged, target)
    assert not target.exists()


def test_independent_writer_proof_rejects_forged_backend_and_different_original_block(tmp_path):
    original, raw, *_ = source(tmp_path)
    session = attach(original)
    session.set_layer_visibility(0, False)
    altered = LayerStack.from_bytes(raw, original)
    altered.data += b"forged"
    forged = replace(session.document, layer_stack=altered)
    target = tmp_path / "different-stack.tif"
    with pytest.raises(ValueError, match="original Photoshop layer block"):
        save_tiff_copy(original, forged, target)
    assert not target.exists()
    proxy = SimpleNamespace(data=raw, default_order=session.document.layer_stack.default_order,
                            composite_reason=lambda *args: None)
    forged = replace(session.document, layer_stack=proxy)
    with pytest.raises(ValueError, match="original Photoshop layer block"):
        save_tiff_copy(original, forged, target)
    assert not target.exists()
    forged = replace(session.document, layer_state=LayerState((0, 0, 1), frozenset({1})))
    with pytest.raises(ValueError, match="unknown layer"):
        save_tiff_copy(original, forged, target)
    assert not target.exists()


def test_generated_transparency_markers_cannot_bypass_layer_proof(tmp_path):
    original, *_ = source(tmp_path)
    session = attach(original)
    session.set_layer_visibility(0, False)
    target = tmp_path / "invalid-alpha.tif"
    for forged in (replace(session.document, layer_stack=None),
                   replace(session.document, layer_state=None),
                   replace(session.document, metadata={**session.document.metadata, "layer_generated_transparency": False})):
        with pytest.raises(ValueError):
            save_tiff_copy(original, forged, target)
        assert not target.exists()


def test_save_source_protection_remains_active_for_layer_edits(tmp_path):
    original, *_ = source(tmp_path)
    before = original.path.read_bytes()
    session = attach(original)
    session.set_layer_visibility(0, False)
    with pytest.raises(ValueError, match="original image cannot be overwritten"):
        save_tiff_copy(original, session.document, original.path, overwrite=True)
    assert original.path.read_bytes() == before
    original.path.write_bytes(before + b"source changed")
    target = tmp_path / "changed-source.tif"
    with pytest.raises(ValueError, match="changed after opening"):
        save_tiff_copy(original, session.document, target)
    assert not target.exists()


@pytest.mark.parametrize("associated", [None, True])
def test_save_rejects_transparency_association_metadata_that_disagrees_with_native_channel(tmp_path, associated):
    original, *_ = source(tmp_path, associated=associated)
    session = attach(original)
    session.set_layer_visibility(0, False)
    transparency = next(c for c in session.document.channels if c.kind == "Transparency")
    extras = list(session.document.metadata["extra_samples"])
    extras[transparency.index - original.base_count] = 2 if associated else 1
    forged = replace(session.document, metadata={**session.document.metadata, "extra_samples": extras})
    target = tmp_path / "wrong-association.tif"
    with pytest.raises(ValueError, match="transparency|ExtraSamples|association"):
        save_tiff_copy(original, forged, target)
    assert not target.exists()
