"""Layer safety inspection must avoid pixel decoding and opaque parsers."""
import struct

import numpy as np
import pytest
from psdtags import (PsdChannel, PsdChannelId, PsdCompressionType, PsdFormat,
                     PsdKey, PsdLayer, PsdLayers, PsdRectangle, PsdUserMask,
                     TiffImageSourceData)

from tifview.layercheck import spot_structure_layer_reason


SIGNATURE = b"Adobe Photoshop Document Data Block\0"


def tag(key, payload=b"", byteorder="<", alignment=4):
    signature = b"MIB8" if byteorder == "<" else b"8BIM"
    stored_key = key[::-1] if byteorder == "<" else key
    return signature + stored_key + struct.pack(byteorder + "I", len(payload)) + payload + b"\0" * (-len(payload) % alignment)


def layer_data(channel_ids=(0, 1, 2, -1, -2), extra_tags=b"", byteorder="<", ranges=b""):
    # Deliberately invalid compressed pixel bytes: the scanner must only inspect
    # their declared lengths, not decode the payload or any opaque descriptor.
    pixels = b"opaque pixel payload"
    channel_info = b"".join(struct.pack(byteorder + "hI", index, len(pixels)) for index in channel_ids)
    extra = struct.pack(byteorder + "II", 0, len(ranges)) + ranges + b"\x03abc" + extra_tags
    signature = b"MIB8" if byteorder == "<" else b"8BIM"
    record = (struct.pack(byteorder + "4iH", 0, 0, 2, 3, len(channel_ids)) + channel_info
              + signature + b"norm" + b"\xff\0\0\0" + struct.pack(byteorder + "I", len(extra)) + extra)
    payload = struct.pack(byteorder + "h", 1) + record + pixels * len(channel_ids)
    return SIGNATURE + tag(b"Layr", payload, byteorder)


@pytest.mark.parametrize("byteorder", ["<", ">"])
@pytest.mark.parametrize("depth", [8, 16])
def test_generated_rle_layer_metadata_is_safe_without_pixel_decoding(byteorder, depth, monkeypatch):
    channels = [PsdChannel(PsdChannelId(i), PsdCompressionType.RLE,
                           np.full((3, 5), i * 20, dtype=f"uint{depth}")) for i in range(3)]
    layer = PsdLayer("Original layer", channels, PsdRectangle(0, 0, 3, 5))
    source = TiffImageSourceData(PsdFormat.LE32BIT if byteorder == "<" else PsdFormat.BE32BIT,
                                PsdLayers(PsdKey.LAYER if depth == 8 else PsdKey.LAYER_16, [layer]), PsdUserMask())
    data = source.tobytes(compression=PsdCompressionType.RLE)
    monkeypatch.setattr(PsdChannel, "read_image", lambda *args, **kwargs: pytest.fail("Pixels must remain opaque"))
    assert spot_structure_layer_reason(data, 3) is None


@pytest.mark.parametrize("byteorder", ["<", ">"])
def test_unknown_descriptors_and_fake_nested_tags_remain_opaque(byteorder):
    # The unknown tag payload intentionally looks like an unsafe restriction.
    opaque = tag(b"vowv", tag(b"brst", struct.pack(byteorder + "I", 9), byteorder, 2), byteorder, 2)
    data = layer_data(extra_tags=opaque, byteorder=byteorder)
    data += tag(b"GenI", b"opaque document descriptor", byteorder)
    before = bytes(data)
    assert spot_structure_layer_reason(data, 3) is None
    assert data == before


@pytest.mark.parametrize("key", [b"Alph", b"brst"])
def test_top_level_known_channel_dependencies_are_rejected(key):
    payload = struct.pack("<I", 9) if key == b"brst" else b""
    reason = spot_structure_layer_reason(layer_data() + tag(key, payload), 3)
    assert reason and ("Alph" in reason or "restrictions" in reason)


@pytest.mark.parametrize("channel", [3, 12, -4])
def test_extra_and_unknown_layer_pixel_channels_are_rejected(channel):
    reason = spot_structure_layer_reason(layer_data((0, 1, 2, channel)), 3)
    assert reason and "channel" in reason


@pytest.mark.parametrize("byteorder", ["<", ">"])
def test_process_only_blending_restrictions_are_safe_but_extra_references_are_not(byteorder):
    safe = tag(b"brst", struct.pack(byteorder + "2I", 0, 2), byteorder, 2)
    assert spot_structure_layer_reason(layer_data(extra_tags=safe, byteorder=byteorder), 3) is None
    unsafe = tag(b"brst", struct.pack(byteorder + "I", 3), byteorder, 2)
    assert "restrictions" in spot_structure_layer_reason(layer_data(extra_tags=unsafe, byteorder=byteorder), 3)
    malformed = tag(b"brst", b"abc", byteorder, 2)
    assert "unsupported" in spot_structure_layer_reason(layer_data(extra_tags=malformed, byteorder=byteorder), 3)


def test_extra_channel_blending_ranges_are_rejected():
    assert spot_structure_layer_reason(layer_data(ranges=b"\0" * 32), 3) is None
    assert "additional channels" in spot_structure_layer_reason(layer_data(ranges=b"\0" * 40), 3)
    assert "unsupported" in spot_structure_layer_reason(layer_data(ranges=b"\0" * 31), 3)


@pytest.mark.parametrize("data", [b"", SIGNATURE[:-1], b"Adobe Photoshop Document Data V0002\0",
                                  SIGNATURE + b"8B64", SIGNATURE + b"MIB",
                                  SIGNATURE + b"MIB8ryaL\xff\xff\xff\xff"])
def test_unsupported_or_truncated_document_frames_are_rejected(data):
    assert spot_structure_layer_reason(data, 3) is not None


def test_truncation_in_layer_headers_extra_tags_or_declared_pixels_is_rejected():
    data = layer_data(extra_tags=tag(b"vowv", b"abc", alignment=2))
    # Truncating any bytes within this single bounded block invalidates the
    # outer length; channel and nested lengths are also tested independently.
    for cut in (len(SIGNATURE) + 5, len(SIGNATURE) + 13, len(data) - 1, len(data) - 20):
        assert spot_structure_layer_reason(data[:cut], 3) is not None
    layer_header = SIGNATURE + tag(b"Layr", struct.pack("<h", 1))
    assert "truncated" in spot_structure_layer_reason(layer_header, 3)
    # A bounded but truncated nested tag must not loop forever or reach pixels.
    assert spot_structure_layer_reason(layer_data(extra_tags=b"MIB8vowv"), 3) is not None


def test_declared_channel_pixels_must_fit_inside_the_layer_block():
    data = bytearray(layer_data())
    # Signature, tag header, layer count, rectangle, channel count, channel ID.
    offset = len(SIGNATURE) + 12 + 2 + 16 + 2 + 2
    struct.pack_into("<I", data, offset, 2**32 - 1)
    assert "truncated" in spot_structure_layer_reason(bytes(data), 3)


def test_empty_supported_document_and_zero_size_layer_block_have_no_dependency():
    assert spot_structure_layer_reason(SIGNATURE, 3) is None
    assert spot_structure_layer_reason(SIGNATURE + tag(b"Layr"), 3) is None


@pytest.mark.parametrize("base_count", [0, -1, 57, None])
def test_unknown_colour_mode_cannot_be_checked(base_count):
    assert spot_structure_layer_reason(layer_data(), base_count) is not None
