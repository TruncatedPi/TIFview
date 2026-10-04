"""Inspect channel dependencies in opaque Photoshop TIFF layer data.

Adobe's layer-record and additional-layer-information layouts:
https://www.adobe.com/devnet-apps/photoshop/fileformatashtml/

TIFF tag 37724 begins with a NUL-terminated signature and tagged blocks. Its
32-bit Windows form reverses signatures/keys and uses little-endian integers.
Top-level payloads are padded to four bytes; tags inside layer records to two.
This scanner reads only bounded headers. It neither decodes channel pixels nor
interprets opaque descriptors, including newer tags such as Photoshop's vowv.
"""
from __future__ import annotations

import struct


_SIGNATURE = b"Adobe Photoshop Document Data Block\0"
_LAYER_KEYS = {b"Layr", b"Lr16", b"Lr32"}
_NEUTRAL_BLEND_RANGE = b"\x00\x00\xff\xff\x00\x00\xff\xff"


class _InvalidLayerData(ValueError):
    pass


class _Cursor:
    def __init__(self, data: memoryview, byteorder: str):
        self.data = data
        self.byteorder = byteorder
        self.pos = 0

    @property
    def remaining(self) -> int:
        return len(self.data) - self.pos

    def take(self, count: int) -> memoryview:
        if count < 0 or count > self.remaining:
            raise _InvalidLayerData("truncated Photoshop layer metadata")
        start = self.pos
        self.pos += count
        return self.data[start:self.pos]

    def skip(self, count: int):
        self.take(count)

    def unpack(self, fmt: str):
        size = struct.calcsize(self.byteorder + fmt)
        return struct.unpack(self.byteorder + fmt, self.take(size))


def _restriction_reason(payload: memoryview, byteorder: str, base_count: int) -> str | None:
    if len(payload) % 4:
        raise _InvalidLayerData("unsupported Photoshop brst channel restrictions")
    for (channel,) in struct.iter_unpack(byteorder + "i", payload):
        if not 0 <= channel < base_count:
            return "Photoshop layer blending restrictions reference an extra or unknown channel."
    return None


def _scan_tags(cursor: _Cursor, base_count: int, alignment: int, top_level: bool) -> str | None:
    expected = b"MIB8" if cursor.byteorder == "<" else b"8BIM"
    while cursor.remaining:
        signature = bytes(cursor.take(4))
        if signature != expected:
            raise _InvalidLayerData("unsupported Photoshop layer tag signature")
        key = bytes(cursor.take(4))
        if cursor.byteorder == "<":
            key = key[::-1]
        (size,) = cursor.unpack("I")
        payload = cursor.take(size)
        cursor.skip((-size) % alignment)
        if key == b"Alph":
            return "Photoshop layer data contains an Alph block with additional channel data."
        if key == b"brst":
            reason = _restriction_reason(payload, cursor.byteorder, base_count)
            if reason:
                return reason
        if key in _LAYER_KEYS:
            if not top_level:
                raise _InvalidLayerData("unsupported nested Photoshop layer records")
            if size:
                reason = _scan_layers(_Cursor(payload, cursor.byteorder), base_count)
                if reason:
                    return reason
    return None


def _scan_layers(cursor: _Cursor, base_count: int) -> str | None:
    (count,) = cursor.unpack("h")
    pixel_bytes = 0
    for _ in range(abs(count)):
        top, left, bottom, right = cursor.unpack("4i")
        if bottom < top or right < left:
            raise _InvalidLayerData("invalid Photoshop layer rectangle")
        (channel_count,) = cursor.unpack("H")
        if channel_count > 56:
            raise _InvalidLayerData("unsupported Photoshop layer channel count")
        for _ in range(channel_count):
            channel, size = cursor.unpack("hI")
            if channel >= base_count:
                return "Photoshop layers contain pixels for an extra channel whose order could change."
            if channel < -3:
                return "Photoshop layers contain an unknown channel type."
            pixel_bytes += size
        expected = b"MIB8" if cursor.byteorder == "<" else b"8BIM"
        if bytes(cursor.take(4)) != expected:
            raise _InvalidLayerData("unsupported Photoshop layer blend signature")
        cursor.skip(8)  # Blend key, opacity, clipping, flags, filler.
        (extra_size,) = cursor.unpack("I")
        extra = _Cursor(cursor.take(extra_size), cursor.byteorder)
        (mask_size,) = extra.unpack("I")
        extra.skip(mask_size)
        (ranges_size,) = extra.unpack("I")
        ranges = extra.take(ranges_size)
        if ranges_size % 8:
            raise _InvalidLayerData("unsupported Photoshop layer blending ranges")
        # Default source/destination black/white split values do not restrict
        # blending. Photoshop may retain these neutral entries beyond the
        # current process channels. Keep the original bytes, and reject any
        # extra entry that actually depends on an additional channel's values.
        for offset in range(8 * (base_count + 1), ranges_size, 8):
            if ranges[offset:offset + 8] != _NEUTRAL_BLEND_RANGE:
                return "Photoshop layer blending ranges have custom settings for additional channels."
        (name_size,) = extra.unpack("B")
        extra.skip(name_size)
        extra.skip((-(name_size + 1)) % 4)
        reason = _scan_tags(extra, base_count, 2, False)
        if reason:
            return reason
    # Channel byte lengths come from the records; skip their compressed payloads
    # without reading RLE tables, masks, or pixel values. A final even pad is legal.
    cursor.skip(pixel_bytes)
    if cursor.remaining == 1 and cursor.pos % 2:
        if bytes(cursor.take(1)) != b"\0":
            raise _InvalidLayerData("invalid Photoshop layer data padding")
    if cursor.remaining:
        raise _InvalidLayerData("unsupported trailing Photoshop layer data")
    return None


def spot_structure_layer_reason(data: bytes, base_count: int) -> str | None:
    """Explain why raw layer preservation is unsafe after changing spot order/count.

    None means this supported metadata layout has no known extra-channel
    dependency. The caller separately verifies unchanged process/transparency
    samples. Unknown, length-bounded descriptors remain opaque and untouched.
    """
    if not isinstance(base_count, int) or not 1 <= base_count <= 56:
        return "Photoshop layer safety cannot be checked for this image colour mode."
    if not data.startswith(_SIGNATURE):
        return "Photoshop layer data has an unsupported document signature."
    payload = memoryview(data)[len(_SIGNATURE):]
    if not payload:
        return None
    signature = bytes(payload[:4])
    if signature not in (b"8BIM", b"MIB8"):
        return "Photoshop layer data uses an unsupported or truncated format."
    byteorder = "<" if signature == b"MIB8" else ">"
    try:
        return _scan_tags(_Cursor(payload, byteorder), base_count, 4, True)
    except _InvalidLayerData as exc:
        return f"Photoshop layer safety cannot be checked: {exc}."
