"""Interpret channel resources, conservatively, without guessing from names.

References and the unvalidated TIFF-to-resource ordering assumptions are in
docs/architecture.md. psdtags decodes resource blocks; DisplayInfo is opaque in
that library, so its two documented record layouts are decoded here.
"""

from __future__ import annotations

import colorsys
import struct
from dataclasses import dataclass, field

import numpy as np
from psdtags import TiffImageResources


@dataclass(frozen=True)
class DisplayInfo:
    color_space: int
    components: tuple[int, int, int, int]
    opacity: int
    mode: int
    resource_id: int

    @property
    def kind(self) -> str:
        return {0: "Alpha mask", 1: "Alpha mask", 2: "Spot"}.get(self.mode, "Unknown")

    @property
    def rgb(self) -> tuple[int, int, int] | None:
        """Approximate preview only; preserve the original color values too."""
        c = self.components
        if self.color_space == 0:
            rgb = np.array(c[:3]) / 65535
        elif self.color_space == 1:
            rgb = np.array(colorsys.hsv_to_rgb(*(x / 65535 for x in c[:3])))
        elif self.color_space == 2:
            # Photoshop resource CMYK is inverse ink; TIFF CMYK is not.
            rgb = np.array(c[:3]) / 65535 * (c[3] / 65535)
        elif self.color_space == 8:
            rgb = np.full(3, c[0] / 10000)
        elif self.color_space == 7:
            # Adobe Lab components are signed hundredths, referenced to D50.
            signed = [x if x < 32768 else x - 65536 for x in c[1:3]]
            light, a, b = c[0] / 100, signed[0] / 100, signed[1] / 100
            fy = (light + 16) / 116
            f = np.array([fy + a / 500, fy, fy - b / 200])
            delta = 6 / 29
            xyz = np.where(f > delta, f**3, 3 * delta**2 * (f - 4 / 29))
            xyz *= [0.96422, 1.0, 0.82521]
            # Bradford D50 -> D65, then linear sRGB.
            xyz = np.array([[.9555766, -.0230393, .0631636],
                            [-.0282895, 1.0099416, .0210077],
                            [.0122982, -.0204830, 1.3299098]]) @ xyz
            linear = np.array([[3.2404542, -1.5371385, -.4985314],
                               [-.9692660, 1.8760108, .0415560],
                               [.0556434, -.2040259, 1.0572252]]) @ xyz
            rgb = np.where(linear <= .0031308, 12.92 * linear,
                           1.055 * np.maximum(linear, 0)**(1 / 2.4) - .055)
        else:
            return None  # Color-book components are not publicly specified.
        return tuple(int(x) for x in np.rint(np.clip(rgb, 0, 1) * 255))


@dataclass
class PhotoshopMetadata:
    names: list[str] = field(default_factory=list)
    displays: list[DisplayInfo] = field(default_factory=list)
    resource_ids: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _validate_blocks(data: bytes) -> list[int]:
    """psdtags accepts some partial streams; reject truncation before decoding."""
    pos = 0
    seen = set()
    ids = []
    while pos < len(data):
        if len(data) - pos < 7 or data[pos:pos + 4] != b"8BIM":
            raise ValueError(f"Invalid resource header at byte {pos}")
        resource_id = struct.unpack_from(">H", data, pos + 4)[0]
        if resource_id in seen and resource_id in (1006, 1045, 1007, 1077):
            raise ValueError(f"Duplicate resource {resource_id}; ordering is ambiguous")
        seen.add(resource_id)
        ids.append(resource_id)
        name_size = data[pos + 6] + 1
        pos += 6 + name_size + name_size % 2
        if pos + 4 > len(data):
            raise ValueError("Truncated resource name/length")
        size = struct.unpack_from(">I", data, pos)[0]
        pos += 4 + size + size % 2
        if pos > len(data):
            raise ValueError("Truncated resource data")
    return ids


def without_thumbnails(data: bytes) -> bytes:
    """Drop stale Photoshop preview caches, copying every other block verbatim."""
    _validate_blocks(data)
    retained = []
    pos = 0
    while pos < len(data):
        start = pos
        resource_id = struct.unpack_from(">H", data, pos + 4)[0]
        name_size = data[pos + 6] + 1
        pos += 6 + name_size + name_size % 2
        size = struct.unpack_from(">I", data, pos)[0]
        pos += 4 + size + size % 2
        if resource_id not in (1033, 1036):
            retained.append(data[start:pos])
    return b"".join(retained)


def _displays(data: bytes, resource_id: int) -> list[DisplayInfo]:
    if resource_id == 1077:
        if len(data) < 4 or struct.unpack_from(">I", data)[0] != 1:
            raise ValueError("Unsupported DisplayInfo version (expected 1)")
        data = data[4:]
        stride = 13
    else:
        stride = 14  # Legacy DisplayInfo: one padding byte per record.
    if len(data) % stride:
        raise ValueError(f"Invalid DisplayInfo record length in resource {resource_id}")
    result = []
    for pos in range(0, len(data), stride):
        space, c1, c2, c3, c4, opacity, mode = struct.unpack_from(">6HB", data, pos)
        if mode not in (0, 1, 2) or opacity > 100:
            raise ValueError(f"Unsupported DisplayInfo mode/opacity: {mode}/{opacity}")
        result.append(DisplayInfo(space, (c1, c2, c3, c4), opacity, mode, resource_id))
    return result


def read_resources(data: bytes) -> PhotoshopMetadata:
    result = PhotoshopMetadata()
    try:
        result.resource_ids = _validate_blocks(data)
        resources = TiffImageResources.frombytes(data)
    except Exception as exc:
        result.warnings.append(f"Photoshop resources could not be read: {exc}")
        return result
    for resource_id in (1045, 1006):
        if resource_id in resources:
            result.names = [str(x).rstrip("\0") for x in resources[resource_id].values]
            break  # Prefer Unicode names.
    for resource_id in (1077, 1007):
        if resource_id in resources:
            try:
                result.displays = _displays(resources[resource_id].value, resource_id)
            except ValueError as exc:
                result.warnings.append(str(exc))
            break  # A malformed newer resource must not silently use stale data.
    return result


def align_metadata(values: list, extra_types: list[int], label: str,
                   warnings: list[str]) -> dict[int, object]:
    """Map resources only when counts identify one of two explicit layouts.

    Candidate layouts: all extras, or extras excluding TIFF transparency.
    Never zip a mismatching list, which can shift white/varnish names.
    """
    if not values:
        return {}
    all_indices = list(range(len(extra_types)))
    non_transparency = [i for i, kind in enumerate(extra_types) if kind not in (1, 2)]
    if len(values) == len(all_indices):
        indices = all_indices
    elif len(values) == len(non_transparency):
        indices = non_transparency
    else:
        warnings.append(f"{label}: {len(values)} records for {len(extra_types)} extra "
                        "samples; mapping withheld. Check Photoshop.")
        return {}
    return dict(zip(indices, values))


def _resource_blocks(data: bytes):
    """Yield framed blocks without decoding or rewriting unrelated payloads."""
    _validate_blocks(data)
    pos = 0
    while pos < len(data):
        start = pos
        resource_id = struct.unpack_from(">H", data, pos + 4)[0]
        name_size = data[pos + 6] + 1
        pos += 6 + name_size + name_size % 2
        header = data[start:pos]
        size = struct.unpack_from(">I", data, pos)[0]
        pos += 4
        payload = data[pos:pos + size]
        pos += size + size % 2
        yield resource_id, header, payload, data[start:pos]


def _resource(resource_id: int, payload: bytes, header: bytes | None = None) -> bytes:
    header = header or b"8BIM" + struct.pack(">H", resource_id) + b"\0\0"
    return header + struct.pack(">I", len(payload)) + payload + b"\0" * (len(payload) % 2)


def rewrite_channel_resources(data, before_channels, after_channels, source_indices,
                              base_count, extra_types):
    """Keep Photoshop resources aligned with changed native extra samples.

    IDs are raw uint32 arrays in Photoshop TIFFs and psd-tools. The observed
    v6 spot-halftone layout is intentionally narrow; opaque/custom variants
    must not be silently attached to a different ink. Unknown blocks are copied.
    """
    blocks = list(_resource_blocks(data or b""))
    indexed = {1006, 1045, 1007, 1077, 1053, 1044, 1043, 1067, 1022}
    resources = {}
    for resource_id, _, payload, _ in blocks:
        if resource_id in indexed:
            if resource_id in resources:
                raise ValueError(f"Duplicate Photoshop resource {resource_id}; spot changes are ambiguous")
            resources[resource_id] = payload
    if len(source_indices) != len(after_channels):
        raise ValueError("Invalid spot channel mapping")
    ps = read_resources(data) if data else PhotoshopMetadata()
    if ps.warnings:
        raise ValueError("Cannot change spots with unreadable Photoshop channel metadata: " + "; ".join(ps.warnings))
    for values, label in ((ps.names, "names"), (ps.displays, "display information")):
        warnings = []
        align_metadata(values, extra_types, label, warnings)
        if warnings:
            raise ValueError("Cannot change spots: " + "; ".join(warnings))
    extras = after_channels[base_count:]
    if any(c.kind == "Unknown" or (c.kind != "Transparency" and c.display is None) for c in extras):
        raise ValueError("Resolve unknown extra channels in Photoshop before changing spots")
    layout_changed = list(source_indices) != list(range(len(before_channels)))
    if layout_changed and 1022 in resources:
        raise ValueError("This file has a Photoshop Quick Mask reference. Exit Quick Mask and save in Photoshop before changing spot order/count.")

    replacements = {}
    # Include all names, also transparency, to retain a fallback name if a
    # preceding spot is deleted. Unicode counts are UTF-16 code units, not len().
    names = [c.name for c in extras]
    unicode_names = []
    pascal_names = []
    for name in names:
        encoded = (name + "\0").encode("utf-16-be")
        unicode_names.append(struct.pack(">I", len(encoded) // 2) + encoded)
        encoded = name.encode("macroman", errors="replace")[:255]
        pascal_names.append(bytes([len(encoded)]) + encoded)
    replacements[1045] = b"".join(unicode_names)
    replacements[1006] = b"".join(pascal_names)
    transparency = [c for c in extras if c.kind == "Transparency"]
    if any(c.display is not None for c in transparency) and not all(c.display is not None for c in transparency):
        raise ValueError("Mixed Photoshop transparency display records are not supported for spot changes")
    displays = [c.display for c in extras if c.display is not None]
    preferred = 1077 if 1077 in resources or 1007 not in resources else 1007
    for resource_id in ({preferred} | ({1007, 1077} & resources.keys())):
        payload = b"".join(struct.pack(">6HB", d.color_space, *d.components, d.opacity, d.mode) +
                           (b"\0" if resource_id == 1007 else b"") for d in displays)
        replacements[resource_id] = (struct.pack(">I", 1) if resource_id == 1077 else b"") + payload

    ids = resources.get(1053)
    id_map = {}
    if ids is not None:
        if len(ids) % 4:
            raise ValueError("Invalid Photoshop alpha identifiers")
        values = list(struct.unpack(f">{len(ids) // 4}I", ids))
        counts = {len(extra_types), sum(t not in (1, 2) for t in extra_types)}
        raw = len(values) in counts
        prefixed = bool(values) and values[0] == len(values) - 1 and len(values) - 1 in counts
        if raw and prefixed and values[0] != 0:
            raise ValueError("Ambiguous Photoshop alpha identifier framing")
        if not raw:
            if not prefixed:
                raise ValueError("Photoshop alpha identifiers do not match the extra samples")
            values = values[1:]
        warnings = []
        id_map = align_metadata(values, extra_types, "Photoshop alpha identifiers", warnings)
        if warnings:
            raise ValueError("; ".join(warnings))
        nonzero = [value for value in values if value]
        if len(set(nonzero)) != len(nonzero):
            raise ValueError("Duplicate Photoshop alpha identifiers")
    seed_data = resources.get(1044)
    if seed_data is not None and len(seed_data) != 4:
        raise ValueError("Invalid Photoshop document ID seed")
    seed = struct.unpack(">I", seed_data)[0] if seed_data else 1
    next_id = max(seed, max(id_map.values(), default=0) + 1, 1)
    after_ids, seen, allocated = [], set(), False
    for channel, source_index in zip(extras, source_indices[base_count:]):
        value = id_map.get(source_index - base_count) if source_index is not None else None
        if channel.kind == "Transparency":
            value = 0 if value is None else value
        elif value is None or value == 0 or value in seen:
            if next_id >= 2**32 - 1:
                raise ValueError("Photoshop channel IDs are exhausted")
            value = next_id
            next_id += 1
            allocated = True
        if value:
            seen.add(value)
        after_ids.append(value)
    replacements[1053] = struct.pack(f">{len(after_ids)}I", *after_ids)
    if allocated or seed_data is None:
        replacements[1044] = struct.pack(">I", max(seed, next_id))

    if layout_changed and 1043 in resources:
        payload = resources[1043]
        spots = [c for c in before_channels if c.kind == "Spot"]
        if len(payload) < 4 or struct.unpack_from(">HH", payload) != (6, len(spots)) or len(payload) != 4 + 18 * len(spots):
            raise ValueError("Custom or unsupported Photoshop spot halftones cannot be safely reordered. Save a standard TIFF from Photoshop first.")
        screens = {c.index: payload[4 + i * 18:4 + (i + 1) * 18] for i, c in enumerate(spots)}
        if any(struct.unpack_from(">h", screen, 10)[0] not in (0, 1, 2, 3, 4, 6) for screen in screens.values()):
            raise ValueError("Custom Photoshop spot halftone shapes cannot be safely changed")
        # Sample-observed Photoshop default (no custom PostScript shape).
        default_screen = bytes.fromhex("000000000001000000000000000000000000")
        records = [screens.get(source_index, default_screen) for c, source_index in
                   zip(after_channels, source_indices) if c.kind == "Spot"]
        replacements[1043] = struct.pack(">HH", 6, len(records)) + b"".join(records)
    if 1067 in resources:
        payload = resources[1067]
        if len(payload) < 4:
            raise ValueError("Invalid Photoshop alternate spot colours")
        version, count = struct.unpack_from(">HH", payload)
        if version != 1 or len(payload) != 4 + 14 * count:
            raise ValueError("Unsupported Photoshop alternate spot colours")
        alternate = {}
        for pos in range(4, len(payload), 14):
            channel_id = struct.unpack_from(">I", payload, pos)[0]
            if channel_id in alternate:
                raise ValueError("Duplicate Photoshop alternate spot colour IDs")
            alternate[channel_id] = payload[pos + 4:pos + 14]
        known_spot_ids = {id_map.get(c.index - base_count) for c in before_channels if c.kind == "Spot"}
        if not alternate.keys() <= known_spot_ids:
            raise ValueError("Alternate spot colours contain unknown Photoshop channel IDs")
        records = []
        for c, source_index, new_id in zip(extras, source_indices[base_count:], after_ids):
            if c.kind != "Spot" or source_index is None:
                continue
            old_id = id_map.get(source_index - base_count)
            old = before_channels[source_index]
            if old_id in alternate and c.display and old.display and (
                    c.display.color_space, c.display.components) == (old.display.color_space, old.display.components):
                records.append(struct.pack(">I", new_id) + alternate[old_id])
        replacements[1067] = struct.pack(">HH", 1, len(records)) + b"".join(records)

    result, written = [], set()
    for resource_id, header, _, raw in blocks:
        if resource_id in (1033, 1036):
            continue
        if resource_id in replacements:
            result.append(_resource(resource_id, replacements[resource_id], header))
            written.add(resource_id)
        else:
            result.append(raw)
    for resource_id in sorted(replacements.keys() - written):
        result.append(_resource(resource_id, replacements[resource_id]))
    return b"".join(result) or None
