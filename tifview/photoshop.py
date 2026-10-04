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
