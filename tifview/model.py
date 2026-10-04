from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .photoshop import DisplayInfo


@dataclass(frozen=True)
class Channel:
    index: int
    name: str
    kind: str
    evidence: str
    color: tuple[int, int, int]
    display: DisplayInfo | None = None
    associated: bool = False


def orient(array: np.ndarray, orientation: int) -> np.ndarray:
    """Return a view with TIFF display orientation, leaving stored samples intact."""
    if orientation == 2:
        return array[:, ::-1]
    if orientation == 3:
        return array[::-1, ::-1]
    if orientation == 4:
        return array[::-1]
    if orientation == 5:
        return np.swapaxes(array, 0, 1)
    if orientation == 6:
        return np.rot90(array, -1, axes=(0, 1))
    if orientation == 7:
        return np.swapaxes(array, 0, 1)[::-1, ::-1]
    if orientation == 8:
        return np.rot90(array, 1, axes=(0, 1))
    return array


@dataclass
class ImageDocument:
    path: Path
    samples: np.ndarray  # Stored H x W x S, uint8/uint16/bool; never RGB-flattened.
    channels: list[Channel]
    color_mode: str
    base_count: int
    bits: int
    orientation: int = 1
    icc_profile: bytes | None = None
    colormap: np.ndarray | None = None
    photoshop_resources: bytes | None = None
    metadata: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    icc_transform: object | None = field(default=None, repr=False)

    def __post_init__(self):
        self.samples.flags.writeable = False
        if self.icc_profile and self.color_mode in ("RGB", "CMYK", "Gray", "WhiteIsZero"):
            import io
            from PIL import ImageCms
            try:
                input_mode = self.color_mode if self.color_mode in ("RGB", "CMYK") else "L"
                self.icc_transform = ImageCms.buildTransformFromOpenProfiles(
                    ImageCms.ImageCmsProfile(io.BytesIO(self.icc_profile)),
                    ImageCms.createProfile("sRGB"), input_mode, "RGB")
                self.metadata["preview_color_conversion"] = "Embedded ICC to sRGB (8-bit display)"
            except Exception as exc:
                self.warnings.append(f"ICC preview conversion unavailable: {exc}. Using approximate colours.")
        self.metadata.setdefault("preview_color_conversion", "Approximate unmanaged preview")

    @property
    def display_samples(self) -> np.ndarray:
        return orient(self.samples, self.orientation)

    @property
    def width(self) -> int:
        return self.display_samples.shape[1]

    @property
    def height(self) -> int:
        return self.display_samples.shape[0]

    @property
    def maximum(self) -> int:
        return (1 << self.bits) - 1

    def report(self) -> dict:
        """JSON-safe diagnostic inventory; does not save into the source image."""
        return {
            "file": str(self.path), "stored_shape": list(self.samples.shape),
            "display_size": [self.width, self.height], "dtype": str(self.samples.dtype),
            "bits": self.bits, "color_mode": self.color_mode,
            "orientation": self.orientation, "metadata": self.metadata,
            "channels": [{"sample_index": c.index, "name": c.name, "kind": c.kind,
                          "evidence": c.evidence, "preview_rgb": list(c.color),
                          "display_info": None if c.display is None else {
                              "resource_id": c.display.resource_id,
                              "color_space": c.display.color_space,
                              "components": list(c.display.components),
                              "mode": c.display.mode, "opacity_or_solidity": c.display.opacity,
                          }} for c in self.channels],
            "warnings": self.warnings,
        }
