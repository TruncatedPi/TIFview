"""Read the primary TIFF IFD or common raster formats, with raw sample retention."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import tifffile
from PIL import Image, ImageOps

from .model import Channel, ImageDocument
from .photoshop import align_metadata, read_resources
from .tiffpages import inspect_page_layout


PROCESS = {
    "RGB": [("Red", (230, 55, 55)), ("Green", (40, 190, 80)), ("Blue", (60, 100, 240))],
    "CMYK": [("Cyan", (0, 190, 220)), ("Magenta", (220, 0, 150)),
             ("Yellow", (235, 205, 0)), ("Black", (0, 0, 0))],
    "Gray": [("Gray", (130, 130, 130))],
    "WhiteIsZero": [("Gray", (130, 130, 130))],
    "Palette": [("Palette index", (130, 130, 130))],
}
FALLBACK_COLORS = [(0, 160, 220), (220, 70, 190), (225, 140, 0), (80, 175, 85)]


class UnsupportedImageError(ValueError):
    pass


def load_image(filename: str | Path) -> ImageDocument:
    path = Path(filename).resolve()
    before = file_signature(path)
    with path.open("rb") as source:
        magic = source.read(4)
    if magic in (b"II*\0", b"MM\0*", b"II+\0", b"MM\0+"):
        doc = _load_tiff(path)
    elif path.suffix.lower() in (".tif", ".tiff"):
        raise UnsupportedImageError("The file does not have a valid TIFF header.")
    else:
        doc = _load_common(path)
    if file_signature(path) != before:
        raise UnsupportedImageError("The source changed during import. Reopen the file.")
    doc.metadata["source_signature"] = before
    return doc


def file_signature(path: Path):
    stat = path.stat()
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns]


def _channels(mode: str, count: int, extra_types: list[int], resources: bytes | None,
              warnings: list[str]) -> list[Channel]:
    base = PROCESS[mode]
    channels = [Channel(i, name, "Process" if len(base) > 1 else "Image",
                        f"{mode} image samples", color) for i, (name, color) in enumerate(base)]
    ps = read_resources(resources) if resources else None
    if ps:
        warnings.extend(ps.warnings)
    names = align_metadata(ps.names, extra_types, "Photoshop names", warnings) if ps else {}
    displays = align_metadata(ps.displays, extra_types, "Photoshop DisplayInfo", warnings) if ps else {}
    if resources:
        warnings.append("Photoshop channel metadata is experimental; its sample ordering "
                        "and mask polarity have not yet been compared with Photoshop.")
    for extra_index in range(count - len(base)):
        sample_type = extra_types[extra_index]
        info = displays.get(extra_index)
        kind, evidence = "Unknown", "Extra sample without channel-type metadata"
        if sample_type in (1, 2):
            kind = "Transparency"
            evidence = f"TIFF ExtraSamples={sample_type}"
            if info and info.mode == 2:
                kind, evidence = "Unknown", "Conflicting TIFF transparency and Photoshop spot metadata"
                warnings.append(f"Sample {len(base) + extra_index}: {evidence}; not used as transparency.")
        elif info:
            kind, evidence = info.kind, f"Photoshop resource {info.resource_id}, mode={info.mode}"
        color = info.rgb if info else None
        if info and color is None:
            warnings.append(f"Extra sample {extra_index + 1}: unsupported preview color "
                            f"space {info.color_space}; using a display-only fallback color.")
        channels.append(Channel(len(base) + extra_index,
                                names.get(extra_index) or f"Extra {extra_index + 1}",
                                kind, evidence, color or FALLBACK_COLORS[extra_index % 4],
                                info, associated=sample_type == 1))
    return channels


def _load_tiff(path: Path) -> ImageDocument:
    warnings = []
    with tifffile.TiffFile(path, mode="r") as tif:
        if not tif.pages:
            raise UnsupportedImageError("This TIFF contains no image data. It may be empty or incomplete; "
                                        "open a valid TIFF or recreate the test file.")
        page = tif.pages[0]
        layout = inspect_page_layout(tif)
        mode = {0: "WhiteIsZero", 1: "Gray", 2: "RGB", 3: "Palette", 5: "CMYK"}.get(int(page.photometric))
        if mode is None:
            raise UnsupportedImageError(f"TIFF photometric {page.photometric.name} is not yet supported. "
                                        "RGB, CMYK, grayscale and palette TIFFs are supported.")
        if mode == "CMYK" and int(page.tags.valueof("InkSet", 1)) != 1:
            raise UnsupportedImageError("This separated TIFF uses a non-CMYK InkSet; interpretation is not implemented.")
        if int(page.imagedepth) != 1:
            raise UnsupportedImageError("Volumetric TIFF is not supported; use a single 2D printing image.")
        bits = page.bitspersample
        if not isinstance(bits, int) or bits not in (1, 8, 16):
            raise UnsupportedImageError(f"Unsupported BitsPerSample: {bits}; expected 1, 8 or 16.")
        if int(page.sampleformat) != 1:
            raise UnsupportedImageError("Only unsigned integer TIFF samples are supported.")
        pixels = int(page.imagelength) * int(page.imagewidth)
        decoded_bytes = pixels * int(page.samplesperpixel) * (2 if bits == 16 else 1)
        if decoded_bytes > 512 * 2**20 or pixels > 40_000_000:
            raise UnsupportedImageError("This prototype holds the full image in memory. Its current limit "
                                        "is 40 million pixels or 512 MiB of decoded samples. Use a smaller "
                                        "test copy; tiled large-file viewing is planned.")
        data = page.asarray()  # No RGB conversion; retain every primary IFD sample.
        h, w, count = page.imagelength, page.imagewidth, page.samplesperpixel
        if count < len(PROCESS[mode]):
            raise UnsupportedImageError("TIFF has too few samples for its color mode.")
        if int(page.planarconfig) == 2 and count > 1:
            data = np.moveaxis(data.reshape(count, h, w), 0, -1)
        else:
            data = data.reshape(h, w, count)
        if data.dtype.kind not in ("u", "b") or data.dtype.itemsize > 2:
            raise UnsupportedImageError(f"Unsupported decoded sample type: {data.dtype}")
        extras = [int(x) for x in page.extrasamples]
        extra_count = count - len(PROCESS[mode])
        if len(extras) > extra_count:
            raise UnsupportedImageError("ExtraSamples count conflicts with the image's color mode.")
        extras += [0] * (extra_count - len(extras))
        resources = page.tags.valueof(34377)
        resources = bytes(resources) if resources is not None else None
        channels = _channels(mode, count, extras, resources, warnings)
        orientation = int(page.tags.valueof("Orientation", 1))
        if orientation not in range(1, 9):
            warnings.append(f"Unknown TIFF orientation {orientation}; showing stored orientation.")
            orientation = 1
        if layout.independent_pages:
            warnings.append(f"This TIFF has {layout.independent_pages} additional image or unsupported "
                            "directories; only the primary image is shown. Saving a copy is not supported.")
        has_credentials = 52545 in page.tags or layout.manifest_only_pages > 0
        if has_credentials:
            warnings.append("Content Credentials metadata is present. It is not an image channel or page. "
                            "Saved copies omit it because this app cannot update its signature for edited pixels.")
        if 37724 in page.tags:
            warnings.append("Photoshop layer data is present. This prototype inspects primary IFD "
                            "channels and the saved composite; layer-internal masks are not listed.")
        icc = page.tags.valueof(34675)
        metadata = {"photometric": page.photometric.name, "compression": page.compression.name,
                    "planar_configuration": tifffile.PLANARCONFIG(page.planarconfig).name,
                    "byte_order": "IBM PC (little endian)" if tif.byteorder == "<" else "Macintosh (big endian)",
                    "pyramid_subifds": len(page.subifds or ()),
                    "extra_samples": extras, "page_count": len(tif.pages),
                    "independent_page_count": layout.independent_pages,
                    "content_credentials_ifds": layout.manifest_only_pages,
                    "has_content_credentials": has_credentials,
                    "has_photoshop_layers": 37724 in page.tags,
                    "icc_bytes": len(icc) if icc else 0,
                    "photoshop_resource_ids": read_resources(resources).resource_ids if resources else []}
        if icc:
            warnings.append("Embedded ICC is used for an sRGB preview when supported. Monitor profiling "
                            "and printing simulation are not implemented.")
        elif mode == "CMYK":
            warnings.append("CMYK composite uses a simple conversion for inspection, not a print soft proof.")
        return ImageDocument(path, data, channels, mode, len(PROCESS[mode]), bits, orientation,
                             bytes(icc) if icc else None, page.colormap, resources, metadata, warnings)


def _load_common(path: Path) -> ImageDocument:
    with Image.open(path) as image:
        warnings = []
        if getattr(image, "n_frames", 1) > 1:
            warnings.append("Only the first frame is shown.")
        image = ImageOps.exif_transpose(image)
        icc = image.info.get("icc_profile")
        if image.mode == "P":
            # Ordinary palette files have no Photoshop spot sample semantics.
            image = image.convert("RGBA" if "transparency" in image.info else "RGB")
        elif image.mode in ("1", "LA"):
            image = image.convert("LA" if image.mode == "LA" else "L")
        data = np.array(image)
        if image.mode.startswith("I"):
            if data.min() < 0 or data.max() > 65535:
                raise UnsupportedImageError("Only unsigned 8/16-bit grayscale images are supported.")
            data = data.astype(np.uint16)
            mode, extras = "Gray", []
        elif image.mode in ("RGB", "RGBA"):
            mode, extras = "RGB", [2] if image.mode == "RGBA" else []
        elif image.mode in ("L", "LA"):
            mode, extras = "Gray", [2] if image.mode == "LA" else []
        elif image.mode == "CMYK":
            mode, extras = "CMYK", []
        else:
            raise UnsupportedImageError(f"Image mode {image.mode} is not supported.")
        if data.ndim == 2:
            data = data[..., None]
        channels = _channels(mode, data.shape[-1], extras, None, warnings)
        if icc:
            warnings.append("Embedded ICC is used for sRGB preview when supported; monitor profiling is not implemented.")
        elif mode == "CMYK":
            warnings.append("CMYK composite uses an approximate unmanaged preview.")
        return ImageDocument(path, data, channels, mode, len(PROCESS[mode]),
                             16 if data.dtype.itemsize == 2 else 8, icc_profile=icc,
                             metadata={"backend": "Pillow", "icc_bytes": len(icc) if icc else 0},
                             warnings=warnings)
