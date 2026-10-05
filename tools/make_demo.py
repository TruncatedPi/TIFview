"""Create a clearly synthetic fixture. Never represents Photoshop validation."""
import argparse
import os
import struct
import tempfile
from pathlib import Path

import numpy as np
import tifffile


def resource(resource_id: int, payload: bytes) -> bytes:
    return b"8BIM" + struct.pack(">H", resource_id) + b"\0\0" + struct.pack(">I", len(payload)) + payload + b"\0" * (len(payload) % 2)


def photoshop_resources(names, modes, colors=None, legacy=False, solidity=100):
    # UTF-16 code unit count, including a terminating null. ASCII names also
    # included to exercise Unicode preference.
    unicode = b"".join(struct.pack(">I", len((name + '\0').encode('utf-16-be')) // 2) +
                       (name + '\0').encode("utf-16-be") for name in names)
    pascal = b"".join(bytes([len(name.encode('macroman', errors='replace'))]) +
                      name.encode("macroman", errors="replace") for name in names)
    colors = colors or [(65535, 65535, 65535, 0)] * len(modes)
    display = b"".join(struct.pack(">6HB", 0, *color, solidity, mode) +
                       (b"\0" if legacy else b"") for color, mode in zip(colors, modes))
    return (resource(1006, pascal) + resource(1045, unicode) +
            resource(1007 if legacy else 1077, display if legacy else struct.pack(">I", 1) + display))


def make_demo(path: Path, layers: bool = False):
    h, w = 500, 720
    y, x = np.ogrid[:h, :w]
    data = np.full((h, w, 6), 255, np.uint8)
    data[..., 0] = (x * 255 // (w - 1)).astype(np.uint8)
    data[..., 1] = (y * 255 // (h - 1)).astype(np.uint8)
    data[..., 2] = 160
    # Distinct asymmetric dark masks to catch ordering/orientation mistakes.
    data[70:390, 100:300, 3] = 0
    circle = (x - 480)**2 + (y - 240)**2 < 140**2
    data[..., 4][circle] = 0
    data[150:350, 600:690, 5] = 100
    resources = photoshop_resources(["White Ink", "Varnish", "Saved selection"], [2, 2, 0],
                                    [(65535, 65535, 65535, 0), (0, 50000, 65535, 0), (65535, 0, 0, 0)])
    path.parent.mkdir(parents=True, exist_ok=True)
    tags = [(34377, 7, len(resources), resources, False)]
    profile = None
    if layers:
        from PIL import ImageCms
        from psdtags import (PsdChannel, PsdChannelId, PsdCompressionType, PsdFormat, PsdKey,
                             PsdLayer, PsdLayerFlag, PsdLayers, PsdRectangle, PsdUserMask, TiffImageSourceData)
        channels = [PsdChannel(PsdChannelId(i), PsdCompressionType.RLE, data[..., i].copy()) for i in range(3)]
        # Photoshop may retain a neutral fourth-channel range in an RGB file.
        # Exercise that preservation path in the packaged create/reorder check.
        neutral_ranges = struct.unpack("<10i", bytes.fromhex("0000ffff0000ffff") * 5)
        layer = PsdLayer("SYNTHETIC original base", channels, PsdRectangle(0, 0, h, w),
                         blending_ranges=neutral_ranges)
        patch = np.full((140, 140, 3), [30, 200, 70], np.uint8)
        overlay = PsdLayer("SYNTHETIC overlay", [PsdChannel(PsdChannelId(i), PsdCompressionType.RLE,
                           patch[..., i].copy()) for i in range(3)], PsdRectangle(180, 320, 320, 460),
                           flags=PsdLayerFlag.PHOTOSHOP5 | PsdLayerFlag.VISIBLE,
                           blending_ranges=neutral_ranges)
        source_data = TiffImageSourceData(PsdFormat.LE32BIT, PsdLayers(PsdKey.LAYER, [layer, overlay]), PsdUserMask())
        tags.append(source_data.tifftag(compression=PsdCompressionType.RLE))
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    # A codec or write failure may occur after tifffile creates its header.
    # Publish only a finished file, including when build tools intentionally
    # replace an existing fixture. A sibling keeps os.replace on one volume.
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp.tif", dir=path.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        tifffile.imwrite(temporary, data, photometric="rgb", extrasamples=[0, 0, 0],
                         resolution=(360, 360), resolutionunit="INCH",
                         compression="lzw", metadata=None, description="SYNTHETIC fixture - not saved by Photoshop",
                         iccprofile=profile, extratags=tags)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", nargs="?", default="validation/local/SYNTHETIC-demo.tif")
    args = parser.parse_args()
    target = Path(args.output)
    if target.exists():
        parser.error(f"Refusing to overwrite {target}")
    make_demo(target)
    print(target.resolve())
