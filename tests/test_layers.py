"""Layer pixels, ordering and opaque metadata must survive independent reads."""
import hashlib
import os
from pathlib import Path
import struct
from types import SimpleNamespace
import zlib

import numpy as np
import pytest
from psdtags import (PsdBlendMode, PsdChannel, PsdChannelId, PsdCompressionType, PsdFormat,
                     PsdKey, PsdLayer, PsdLayerFlag, PsdLayerMask, PsdLayers, PsdRectangle, PsdUnknown,
                     PsdUserMask, TiffImageSourceData)
import tifffile

from tifview.layers import LayerError, LayerStack, UnsupportedLayerError, _decode_channel, _parse_mask
from tifview.model import Channel, ImageDocument, orient
from tifview.reader import load_image


def parent(depth=8, mode="RGB", shape=(7, 11), orientation=1):
    count = {"RGB": 3, "CMYK": 4, "Gray": 1, "WhiteIsZero": 1}[mode]
    pixels = np.zeros((*shape, count), dtype=f"uint{depth}")
    channels = [Channel(i, f"Process {i}", "Process", mode, (0, 0, 0)) for i in range(count)]
    return ImageDocument(Path("synthetic.tif"), pixels, channels, mode, count, depth, orientation)


def encode_layers(layers, depth=8, byteorder="<", compression=1):
    fmt = PsdFormat.LE32BIT if byteorder == "<" else PsdFormat.BE32BIT
    source = TiffImageSourceData(fmt, PsdLayers(PsdKey.LAYER if depth == 8 else PsdKey.LAYER_16,
                                             layers), PsdUserMask())
    return source.tobytes(compression=PsdCompressionType(compression))


def raster(samples, alpha=None, bounds=None, name="Pixels", visible=True, info=(), opacity=255):
    if bounds is None:
        bounds = (0, 0, samples.shape[0], samples.shape[1])
    channels = [PsdChannel(PsdChannelId(i), data=samples[..., i].copy()) for i in range(samples.shape[2])]
    if alpha is not None:
        channels.append(PsdChannel(PsdChannelId.TRANSPARENCY_MASK, data=alpha.copy()))
    return PsdLayer(name, channels, PsdRectangle(*bounds), opacity=opacity,
                    flags=PsdLayerFlag.PHOTOSHOP5 | (PsdLayerFlag.VISIBLE if not visible else 0), info=list(info))


@pytest.mark.parametrize("depth", [8, 16])
@pytest.mark.parametrize("byteorder", ["<", ">"])
@pytest.mark.parametrize("compression", [0, 1, 2, 3])
@pytest.mark.parametrize("mode", ["RGB", "CMYK", "Gray", "WhiteIsZero"])
def test_native_channels_decode_every_supported_compression_and_container_byteorder(depth, byteorder, compression, mode):
    doc = parent(depth, mode)
    rng = np.random.default_rng(531)
    values = rng.integers(0, 2**depth, (*doc.samples.shape[:2], doc.base_count), dtype=f"uint{depth}")
    alpha = rng.integers(0, 2**depth, doc.samples.shape[:2], dtype=f"uint{depth}")
    raw = encode_layers([raster(values, alpha)], depth, byteorder, compression)
    stack = LayerStack.from_bytes(raw, doc)
    pixels = stack.decode_layer(0)
    expected = doc.maximum - values if mode in ("CMYK", "WhiteIsZero") else values
    np.testing.assert_array_equal(pixels.samples, expected)
    np.testing.assert_array_equal(pixels.alpha, alpha)
    assert pixels.samples.dtype == np.dtype(f"uint{depth}")
    assert not pixels.samples.flags.writeable and not pixels.alpha.flags.writeable
    assert stack.decode_layer(0) is pixels
    assert stack.data == raw


def test_inventory_does_not_decode_pixels_or_unknown_descriptors(monkeypatch):
    opaque = PsdUnknown(PsdKey.UNKNOWN, PsdFormat.LE32BIT, b"MIB8vowv\xff\xff\xff\xffopaque")
    values = np.full((7, 11, 3), 80, np.uint8)
    raw = encode_layers([raster(values, info=[opaque])])
    monkeypatch.setattr("tifview.layers._decode_channel", lambda *args: pytest.fail("Inventory must stay lazy"))
    monkeypatch.setattr(PsdLayer, "read", lambda *args, **kwargs: pytest.fail("Opaque layer parsers must not run"))
    stack = LayerStack(raw, parent())
    assert stack.layers[0].name == "Pixels"
    assert stack.cache_nbytes == 0
    assert stack.rewrite() == raw


@pytest.mark.parametrize("depth", [8, 16])
@pytest.mark.parametrize("byteorder", ["<", ">"])
def test_rewrite_moves_whole_record_channel_pairs_and_only_toggles_hidden_flag(depth, byteorder):
    fmt = PsdFormat.LE32BIT if byteorder == "<" else PsdFormat.BE32BIT
    values = np.arange(7 * 11 * 3, dtype=f"uint{depth}").reshape(7, 11, 3)
    info = [PsdUnknown(PsdKey.UNKNOWN, fmt, b"opaque descriptor must remain exact")]
    raw = encode_layers([raster(values, name="Bottom", info=info),
                         raster(values + 20, name="Top", visible=False)], depth, byteorder)
    doc = parent(depth)
    stack = LayerStack(raw, doc)
    assert stack.default_order == (1, 0)
    assert stack.default_visible == frozenset({0})
    rewritten = stack.rewrite((0, 1), {1})
    after = LayerStack(rewritten, doc)
    assert [layer.name for layer in after.layers] == ["Top", "Bottom"]
    assert after.default_visible == frozenset({0})
    assert len(rewritten) == len(raw)
    for before_index, after_index in ((0, 1), (1, 0)):
        before_layer, after_layer = stack.layers[before_index], after.layers[after_index]
        before_record = bytearray(raw[before_layer._record_start:before_layer._record_end])
        after_record = bytearray(rewritten[after_layer._record_start:after_layer._record_end])
        before_record[before_layer._flags_offset - before_layer._record_start] &= ~2
        after_record[after_layer._flags_offset - after_layer._record_start] &= ~2
        assert before_record == after_record
        for before_span, after_span in zip(before_layer._channel_spans, after_layer._channel_spans):
            assert raw[slice(*before_span)] == rewritten[slice(*after_span)]
        np.testing.assert_array_equal(stack.decode_layer(before_index).samples, after.decode_layer(after_index).samples)
    tag, after_tag = stack._layer_tag, after._layer_tag
    assert raw[:tag.payload_start] == rewritten[:after_tag.payload_start]
    assert raw[tag.payload_end:] == rewritten[after_tag.payload_end:]


@pytest.mark.parametrize("depth", [8, 16])
def test_top_first_order_visibility_normal_alpha_and_clipped_canvas_bounds(depth):
    doc = parent(depth, shape=(4, 6))
    maximum = doc.maximum
    red = np.zeros((4, 6, 3), dtype=f"uint{depth}")
    red[..., 0] = maximum
    blue = np.zeros((4, 4, 3), dtype=f"uint{depth}")
    blue[..., 2] = maximum
    blue_alpha = np.full((4, 4), maximum // 2, dtype=f"uint{depth}")
    stack = LayerStack(encode_layers([raster(red, name="Red"), raster(blue, blue_alpha, (-1, 2, 3, 6), "Blue")], depth), doc)
    original = stack.composite_samples()
    expected = red.copy()
    expected[:3, 2:, 0] = maximum - maximum // 2
    expected[:3, 2:, 2] = maximum // 2
    np.testing.assert_array_equal(original[..., :3], expected)
    assert np.all(original[..., -1] == maximum)
    reordered = stack.composite_samples((0, 1))
    np.testing.assert_array_equal(reordered[..., :3], red)
    isolated = stack.composite_samples(visible={1})
    assert np.all(isolated[:3, 2:, -1] == maximum // 2)
    assert np.all(isolated[3, :, -1] == 0)
    assert np.all(isolated[:, :2, -1] == 0)
    assert not original.flags.writeable


@pytest.mark.parametrize("orientation", range(1, 9))
def test_layer_preview_respects_parent_orientation_and_canvas_stride(orientation):
    doc = parent(orientation=orientation)
    values = np.arange(7 * 11 * 3, dtype=np.uint8).reshape(7, 11, 3)
    stack = LayerStack(encode_layers([raster(values)]), doc)
    np.testing.assert_array_equal(stack.render_layer(0), orient(values, orientation))
    np.testing.assert_array_equal(stack.render_composite(stride=2), orient(values[::2, ::2], orientation))


def test_native_cache_has_byte_limit_and_returns_immutable_evicted_pixels():
    values = np.zeros((7, 11, 3), np.uint8)
    limit = 7 * 11 * 4
    stack = LayerStack(encode_layers([raster(values), raster(values + 1)]), parent(), cache_bytes=limit)
    first = stack.decode_layer(0)
    stack.decode_layer(1)
    assert stack.cache_nbytes == limit
    assert stack.decode_layer(0) is not first
    assert stack.cache_nbytes <= limit
    np.testing.assert_array_equal(first.samples, values)
    stack.clear_cache()
    assert stack.cache_nbytes == 0
    uncached = LayerStack(stack.data, parent(), cache_bytes=0)
    assert uncached.decode_layer(0) is not uncached.decode_layer(0)
    assert uncached.cache_nbytes == 0


@pytest.mark.parametrize("key,reason", [(PsdKey.COLOR_BALANCE, "adjustment"),
                                        (PsdKey.VECTOR_MASK_SETTING_CS6, "vector"),
                                        (PsdKey.EFFECTS_LAYER, "effects"),
                                        (PsdKey.SECTION_DIVIDER_SETTING, "groups"),
                                        (PsdKey.FILL_OPACITY, "fill opacity"),
                                        (PsdKey.CHANNEL_BLENDING_RESTRICTIONS_SETTING, "restricted")])
def test_unsupported_appearance_blocks_combined_render_and_rewrite_but_lists_cached_pixels(key, reason):
    value = struct.pack("<I", 1) if key == PsdKey.SECTION_DIVIDER_SETTING else b"\0" * 4
    info = [PsdUnknown(key, PsdFormat.LE32BIT, value)]
    values = np.full((7, 11, 3), 23, np.uint8)
    stack = LayerStack(encode_layers([raster(values, info=info)]), parent())
    assert reason in stack.composite_reason()
    with pytest.raises(UnsupportedLayerError, match=reason):
        stack.render_composite()
    with pytest.raises(UnsupportedLayerError, match=reason):
        stack.composite_samples()
    with pytest.raises(UnsupportedLayerError, match=reason):
        stack.rewrite(visible=set()) if reason == "groups" else stack.rewrite()
    np.testing.assert_array_equal(stack.render_layer(0), values)
    if reason != "groups":
        assert stack.composite_reason(visible=set()) is None
        assert LayerStack(stack.rewrite(visible=set()), parent()).default_visible == frozenset()


def test_unsupported_blend_mode_can_show_cached_pixels_but_never_silently_recompose():
    layer = raster(np.full((7, 11, 3), 40, np.uint8))
    layer.blendmode = PsdBlendMode.MULTIPLY
    stack = LayerStack(encode_layers([layer]), parent())
    assert "blend mode mul" in stack.composite_reason()
    np.testing.assert_array_equal(stack.render_layer(0), 40)
    assert stack.composite_reason(visible=set()) is None


def test_unicode_name_counts_utf16_units_and_opaque_bytes_remain_exact():
    name = "White \u767d \U0001f3a8"
    encoded = (name + "\0").encode("utf-16-le")
    unicode = PsdUnknown(PsdKey.UNICODE_LAYER_NAME, PsdFormat.LE32BIT,
                         struct.pack("<I", len(encoded) // 2) + encoded)
    stack = LayerStack(encode_layers([raster(np.zeros((7, 11, 3), np.uint8), info=[unicode])]), parent())
    assert stack.layers[0].name == name


@pytest.mark.parametrize("depth", [8, 16])
def test_plain_user_mask_uses_its_own_rectangle_and_default_outside_mask(depth):
    maximum = 2**depth - 1
    values = np.zeros((7, 11, 3), dtype=f"uint{depth}")
    values[..., 0] = maximum
    mask_pixels = np.array([[0, maximum], [maximum, maximum // 2]], dtype=f"uint{depth}")
    layer = raster(values)
    layer.mask = PsdLayerMask(default_color=255, rectangle=PsdRectangle(2, 3, 4, 5))
    layer.channels.append(PsdChannel(PsdChannelId.USER_LAYER_MASK, data=mask_pixels))
    stack = LayerStack(encode_layers([layer], depth), parent(depth))
    result = stack.composite_samples()
    expected_alpha = np.full((7, 11), maximum, dtype=f"uint{depth}")
    expected_alpha[2:4, 3:5] = mask_pixels
    np.testing.assert_array_equal(result[..., -1], expected_alpha)
    np.testing.assert_array_equal(stack.decode_layer(0).mask, mask_pixels)
    # Fully transparent process pixels are canonical white; the source plane
    # is retained independently and remains red even beneath a black mask.
    assert np.all(result[2, 3, :3] == maximum)
    assert stack.decode_layer(0).samples[2, 3, 0] == maximum


def test_disabling_plain_mask_restores_layer_alpha_and_preserves_mask_pixels():
    values = np.full((7, 11, 3), 21, np.uint8)
    layer = raster(values)
    from psdtags import PsdLayerMaskFlag
    layer.mask = PsdLayerMask(default_color=0, rectangle=PsdRectangle(0, 0, 7, 11),
                              flags=PsdLayerMaskFlag.DISABLED)
    layer.channels.append(PsdChannel(PsdChannelId.USER_LAYER_MASK, data=np.zeros((7, 11), np.uint8)))
    stack = LayerStack(encode_layers([layer]), parent())
    result = stack.composite_samples()
    assert np.all(result[..., -1] == 255)
    np.testing.assert_array_equal(result[..., :3], values)
    assert np.all(stack.decode_layer(0).mask == 0)


def test_byte_limit_applies_before_allocating_unbounded_layer_rectangle():
    stack = LayerStack(encode_layers([raster(np.zeros((7, 11, 3), np.uint8))]), parent())
    malformed = bytearray(stack.data)
    struct.pack_into("<4i", malformed, stack.layers[0]._record_start, 0, 0, 2**30, 2**30)
    oversized = LayerStack(malformed, parent())
    with pytest.raises(LayerError, match="decoded limit"):
        oversized.decode_layer(0)


def test_global_mask_settings_and_channel_dependencies_block_recomposition_but_keep_inventory():
    base = LayerStack(encode_layers([raster(np.zeros((7, 11, 3), np.uint8))]), parent())
    # Standard overlay metadata delegates its masks to the individual layers.
    assert base.global_issues == ()
    mask_tag = next(tag for tag in base._top_tags if tag.key == b"LMsk")
    changed = bytearray(base.data)
    changed[mask_tag.payload_start + 12] = 1
    stack = LayerStack(changed, parent())
    assert "global layer mask" in stack.composite_reason()
    assert stack.layers[0].has_pixels
    np.testing.assert_array_equal(stack.render_layer(0), 0)
    # An additional document-level channel-dependency block remains opaque,
    # but it cannot silently participate in the supported normal compositor.
    raw = base.data + b"MIB8hplA" + struct.pack("<I", 4) + b"abcd"
    stack = LayerStack(raw, parent())
    assert "Alph" in stack.composite_reason()
    assert stack.cache_nbytes == 0


def test_banded_composition_decodes_each_layer_once_even_with_disabled_shared_cache(monkeypatch):
    doc = parent(shape=(200, 800))
    values = np.full((200, 800, 3), 80, np.uint8)
    stack = LayerStack(encode_layers([raster(values), raster(values + 1)]), doc, cache_bytes=0)
    calls = []
    original = stack.decode_layer
    def counted(index):
        calls.append(index)
        return original(index)
    monkeypatch.setattr(stack, "decode_layer", counted)
    stack.render_composite()
    assert calls == [0, 1]
    assert stack.cache_nbytes == 0


@pytest.mark.parametrize("depth", [8, 16])
@pytest.mark.parametrize("mode", ["RGB", "CMYK"])
def test_single_layer_native_pixels_and_stride_match_masked_opacity_oracle(depth, mode):
    doc = parent(depth, mode, shape=(200, 801))
    maximum, count = doc.maximum, doc.base_count
    rng = np.random.default_rng(502)
    colors = rng.integers(0, maximum + 1, (203, 804, count), dtype=f"uint{depth}")
    alpha = rng.integers(0, maximum + 1, (203, 804), dtype=f"uint{depth}")
    alpha[::7, ::11] = 0
    mask = rng.integers(0, maximum + 1, (150, 193), dtype=f"uint{depth}")
    stored_colors = maximum - colors if mode == "CMYK" else colors
    layer = raster(stored_colors, alpha, (-3, -5, 200, 799), opacity=128)
    layer.mask = PsdLayerMask(default_color=255, rectangle=PsdRectangle(10, 7, 160, 200))
    layer.channels.append(PsdChannel(PsdChannelId.USER_LAYER_MASK, data=mask))
    stack = LayerStack(encode_layers([layer], depth), doc)
    coverage = np.ones(alpha.shape, np.float64)
    coverage[13:163, 12:205] = mask.astype(np.float64) / maximum
    effective = alpha.astype(np.float64) / maximum * (128 / 255) * coverage
    background = 0 if mode == "CMYK" else maximum
    expected = np.zeros((200, 801, count + 1), dtype=f"uint{depth}")
    expected[..., :count] = background
    expected[:, :799, :count] = np.where(effective[3:, 5:, None] > 0, colors[3:, 5:], background)
    expected[:, :799, -1] = np.rint(effective[3:, 5:] * maximum)
    np.testing.assert_array_equal(stack.composite_samples(), expected)
    from tifview.render import render
    channels = doc.channels + [Channel(count, "Alpha", "Transparency", "test", (130, 130, 130))]
    for stride in (2, 3, 7):
        expected_doc = ImageDocument(doc.path, expected[::stride, ::stride], channels, mode, count, depth)
        np.testing.assert_array_equal(stack.render_composite(stride=stride), render(expected_doc))


@pytest.mark.parametrize("byteorder", ["<", ">"])
def test_mask_parameters_use_adobe_bit_four_and_preserve_feather_without_guessing(byteorder):
    plain = struct.pack(byteorder + "4i2B", -2, 1, 5, 8, 255, 0) + b"\0\0"
    assert _parse_mask(memoryview(plain), byteorder).issue is None
    feathered = struct.pack(byteorder + "4i3Bd", 0, 0, 7, 11, 0, 16, 2, 100.0) + b"\0"
    mask = _parse_mask(memoryview(feathered), byteorder)
    assert mask.feather == 100 and mask.issue == "feathered layer mask"
    density = struct.pack(byteorder + "4i4B", 0, 0, 7, 11, 255, 16, 1, 128)
    assert _parse_mask(memoryview(density), byteorder).density == 128


@pytest.mark.parametrize("payload", [b"", b"bad", b"\x00\x00abc", b"\x00\x00" + b"a" * 78,
                                      b"\x01\x00\x01\x00x", b"\x02\x00badzip",
                                      b"\x02\x00" + zlib.compress(b"a" * 78),
                                      b"\x02\x00" + zlib.compress(b"a" * 77) + b"trailing",
                                      b"\x04\x00"])
def test_decoder_rejects_wrong_lengths_unsupported_or_unbounded_channels(payload):
    with pytest.raises(LayerError):
        _decode_channel(memoryview(payload), (7, 11), 8, "<")


def test_rle_rows_checked_independently_instead_of_accepting_wrong_row_boundaries():
    # Total output length could be right, but row 0 expands to 12 bytes and row
    # 1 to 10. Each row must expand to exactly the declared width (11).
    table = struct.pack("<2H", 2, 2)
    encoded = b"\x01\0" + table + b"\xf5a\xf7b"
    with pytest.raises(LayerError, match="RLE"):
        _decode_channel(memoryview(encoded), (2, 11), 8, "<")


@pytest.mark.parametrize("order,visible", [((0, 0), {0}), ((0, 2), {0}), ((0,), {3}), ((), set())])
def test_invalid_order_or_visibility_is_rejected(order, visible):
    stack = LayerStack(encode_layers([raster(np.zeros((7, 11, 3), np.uint8))]), parent())
    with pytest.raises(LayerError):
        stack.rewrite(order, visible)


def test_malformed_layer_frame_is_bounded_and_detected_before_any_decode():
    raw = encode_layers([raster(np.zeros((7, 11, 3), np.uint8))])
    for cut in (0, 10, 36, 49, len(raw) - 1):
        with pytest.raises(LayerError):
            LayerStack(raw[:cut], parent())
    stack = LayerStack(raw, parent())
    malformed = bytearray(raw)
    # Corrupt the first declared compressed channel length.
    struct.pack_into("<I", malformed, stack.layers[0]._record_start + 20, 2**32 - 1)
    with pytest.raises(LayerError):
        LayerStack(malformed, parent())


def test_from_document_reads_tag_lazily_and_rejects_changed_source(tmp_path):
    values = np.arange(7 * 11 * 3, dtype=np.uint8).reshape(7, 11, 3)
    raw = encode_layers([raster(values)])
    filename = tmp_path / "layered.tif"
    tifffile.imwrite(filename, values, photometric="rgb", metadata=None,
                     extratags=[(37724, 7, len(raw), raw, False)])
    doc = load_image(filename)
    before = hashlib.sha256(filename.read_bytes()).hexdigest()
    stack = LayerStack.from_document(doc)
    np.testing.assert_array_equal(stack.decode_layer(0).samples, values)
    assert hashlib.sha256(filename.read_bytes()).hexdigest() == before
    filename.write_bytes(filename.read_bytes() + b"changed")
    with pytest.raises(LayerError, match="changed"):
        LayerStack.from_document(doc)


def test_raw_block_limit_is_checked_before_copying_caller_data(monkeypatch):
    monkeypatch.setattr("tifview.layers._RAW_BYTES", 32)
    class Oversized:
        def __len__(self):
            return 33
        def __bytes__(self):
            pytest.fail("Oversized metadata must be rejected before allocating a copy")
    with pytest.raises(LayerError, match="raw metadata limit"):
        LayerStack.from_bytes(Oversized(), parent())


@pytest.mark.parametrize("datatype,count,reason", [(7, 512 * 2**20 + 1, "raw metadata limit"),
                                                   (1, 512 * 2**20 + 1, "raw metadata limit"),
                                                   (3, 1, "tag type"), (16, 1, "tag type")])
def test_tiff_tag_bounds_and_type_are_checked_before_payload_read(monkeypatch, datatype, count, reason):
    class Tag:
        dtype = datatype
        @property
        def valuebytecount(self):
            return count * struct.calcsize(tifffile.TIFF.DATA_FORMATS[self.dtype])
        @property
        def value(self):
            pytest.fail("Malformed or oversized TIFF tag data must not be read")
    class Tiff:
        pages = [SimpleNamespace(tags={37724: Tag()})]
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
    monkeypatch.setattr("tifview.layers.tifffile.TiffFile", Tiff)
    with pytest.raises(LayerError, match=reason):
        LayerStack.from_document(parent())


def test_tiff_bound_accepts_exact_limit_without_reading_or_copying_twice(tmp_path, monkeypatch):
    values = np.full((7, 11, 3), 51, np.uint8)
    raw = encode_layers([raster(values)])
    filename = tmp_path / "limit.tif"
    tifffile.imwrite(filename, values, photometric="rgb", metadata=None,
                     extratags=[(37724, 7, len(raw), raw, False)])
    document = load_image(filename)
    monkeypatch.setattr("tifview.layers._RAW_BYTES", len(raw))
    stack = LayerStack.from_document(document)
    assert stack.data is LayerStack.from_bytes(stack.data, document).data
    np.testing.assert_array_equal(stack.decode_layer(0).samples, values)
    monkeypatch.setattr("tifview.layers._RAW_BYTES", len(raw) - 1)
    with pytest.raises(LayerError, match="raw metadata limit"):
        LayerStack.from_document(document)


@pytest.mark.skipif(not os.environ.get("TIFVIEW_SAMPLE"), reason="Private Photoshop sample stays local")
def test_private_photoshop_layer_inventory_and_native_mask_polarity():
    doc = load_image(os.environ["TIFVIEW_SAMPLE"])
    before = hashlib.sha256(doc.path.read_bytes()).hexdigest()
    stack = LayerStack.from_document(doc)
    assert len(stack.layers) > 0
    for layer in stack.layers:
        if layer.has_pixels:
            pixels = stack.decode_layer(layer.index)
            assert pixels.samples.shape == (layer.height, layer.width, doc.base_count)
            assert pixels.alpha.dtype == doc.samples.dtype
    assert hashlib.sha256(doc.path.read_bytes()).hexdigest() == before
