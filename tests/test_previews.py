from pathlib import Path

import numpy as np
import pytest
from PIL import ImageCms

from tifview.model import Channel, ImageDocument
from tifview.photoshop import DisplayInfo
from tifview.preview import PreviewCache, preview_key
from tifview import render as renderer


def preview_document(mode="RGB", bits=8, icc=False, orientation=1):
    base_count = {"RGB": 3, "CMYK": 4, "Gray": 1, "WhiteIsZero": 1}[mode]
    rng = np.random.default_rng(42)
    samples = rng.integers(0, 2**bits, (29, 23, base_count + 3), dtype=f"uint{bits}")
    alpha = samples[..., base_count]
    alpha[:3] = 0
    alpha[3:6] = 2**bits - 1
    # Valid premultiplied samples exercise unassociation before display.
    samples[..., :base_count] = np.minimum(samples[..., :base_count], alpha[..., None])
    channels = [Channel(i, f"Process {i}", "Process", "test", (40, 90, 140))
                for i in range(base_count)]
    channels.extend([
        Channel(base_count, "Transparency", "Transparency", "test", (255, 255, 255), associated=True),
        Channel(base_count + 1, "White", "Spot", "test", (230, 120, 200)),
        Channel(base_count + 2, "Selection", "Alpha mask", "test", (70, 170, 20),
                DisplayInfo(0, (0, 0, 0, 0), 100, 0, 1077)),
    ])
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes() if icc else None
    return ImageDocument(Path("preview.tif"), samples, channels, mode, base_count, bits,
                         orientation=orientation, icc_profile=profile)


def settings(doc, **changes):
    result = dict(selected=None, colored=False, visible=set(range(len(doc.channels))),
                  overlays=False, opacity=.65, invert=False, colors={})
    result.update(changes)
    return result


def test_preview_keys_reuse_only_equivalent_display_pixels():
    doc = preview_document()
    spot = doc.base_count + 1
    alpha = doc.base_count
    gray = settings(doc, selected=spot)
    assert preview_key(doc, **gray) == preview_key(doc, **settings(
        doc, selected=spot, visible=set(), overlays=True, opacity=.1, colors={spot: (1, 2, 3)}))
    assert preview_key(doc, **gray) != preview_key(doc, **settings(doc, selected=spot, invert=True))

    colored = settings(doc, selected=spot, colored=True)
    assert preview_key(doc, **colored) == preview_key(doc, **settings(
        doc, selected=spot, colored=True, visible=set(), overlays=True, colors={0: (1, 2, 3)}))
    for change in ({"opacity": .1}, {"invert": True}, {"colors": {spot: (1, 2, 3)}}):
        assert preview_key(doc, **colored) != preview_key(doc, **(colored | change))

    composite = settings(doc)
    process_and_alpha = set(range(doc.base_count + 1))
    assert preview_key(doc, **composite) == preview_key(doc, **settings(
        doc, visible=process_and_alpha, colored=True, invert=True, opacity=.1,
        colors={spot: (1, 2, 3)}))
    assert preview_key(doc, **composite) != preview_key(doc, **settings(
        doc, visible=process_and_alpha - {alpha}))
    assert preview_key(doc, **composite) != preview_key(doc, **settings(
        doc, visible=process_and_alpha - {0}))
    overlays = settings(doc, overlays=True)
    assert preview_key(doc, **overlays) != preview_key(doc, **settings(
        doc, overlays=True, colors={spot: (1, 2, 3)}))
    assert preview_key(doc, **overlays) != preview_key(doc, **settings(
        doc, overlays=True, visible=process_and_alpha))


def test_preview_cache_is_bounded_and_reuses_recent_views():
    cache = PreviewCache(limit_bytes=12)
    images = [np.full((2, 2), i, np.uint8) for i in range(5)]
    for key in range(3):
        cache.put(key, images[key])
    assert cache.get(0) is images[0]
    cache.put(3, images[3])
    assert cache.get(1) is None
    assert cache.get(0) is images[0]
    assert cache.get(2) is images[2]
    assert cache.get(3) is images[3]
    assert cache.bytes == 12
    assert not images[3].flags.writeable

    # Replacing a cached preview must not double-count its bytes.
    cache.put(3, images[4])
    assert cache.bytes == 12
    assert cache.get(3) is images[4]
    cache.put("large", np.zeros((4, 4), np.uint8))
    assert cache.get("large") is None
    assert cache.bytes <= cache.limit_bytes
    cache.clear()
    assert cache.bytes == 0
    assert all(cache.get(key) is None for key in (0, 2, 3))


@pytest.mark.parametrize("mode", ["Gray", "WhiteIsZero", "CMYK"])
def test_native_uint16_grayscale_matches_full_range_without_modifying_samples(mode):
    count = 4 if mode == "CMYK" else 1
    native = np.arange(65536, dtype=np.uint16).reshape(256, 256)
    samples = np.zeros((256, 256, count), np.uint16)
    samples[..., 0] = native
    original = samples.copy()
    channels = [Channel(i, str(i), "Process" if count > 1 else "Image", "test", (1, 2, 3))
                for i in range(count)]
    doc = ImageDocument(Path("sixteen.tif"), samples, channels, mode, count, 16)
    expected = np.rint(native.astype(np.float64) * 255 / 65535).astype(np.uint8)
    if mode in ("WhiteIsZero", "CMYK"):
        expected = 255 - expected

    np.testing.assert_array_equal(renderer.grayscale(doc, 0), expected)
    np.testing.assert_array_equal(renderer.grayscale(doc, 0, invert=True), 255 - expected)
    np.testing.assert_array_equal(doc.samples, original)
    assert not doc.samples.flags.writeable
    assert not np.shares_memory(renderer.grayscale(doc, 0), doc.samples)


@pytest.mark.parametrize("mode,bits,icc,orientation", [
    ("RGB", 8, False, 6), ("RGB", 8, True, 1), ("RGB", 16, True, 1),
    ("CMYK", 8, False, 1), ("CMYK", 16, False, 1),
    ("Gray", 16, False, 1), ("WhiteIsZero", 8, False, 1),
])
def test_banded_preview_matches_one_block_with_masks_alpha_and_icc(monkeypatch, mode, bits, icc, orientation):
    doc = preview_document(mode, bits, icc, orientation)
    original = doc.samples.copy()
    spot = doc.base_count + 1
    views = [
        settings(doc),
        settings(doc, overlays=True, opacity=.37,
                 visible=set(range(len(doc.channels))) - {0}, colors={spot: (8, 210, 97)}),
        settings(doc, selected=spot, colored=True, opacity=.73, invert=True,
                 colors={spot: (120, 30, 245)}),
    ]
    if icc:
        assert doc.icc_transform is not None
    for view in views:
        monkeypatch.setattr(renderer, "_BLOCK_PIXELS", doc.width * doc.height)
        reference = renderer.render(doc, **view)
        # Seven-row bands deliberately cross checkerboard boundaries at y=16.
        monkeypatch.setattr(renderer, "_BLOCK_PIXELS", doc.width * 7)
        banded = renderer.render(doc, **view)
        np.testing.assert_array_equal(banded, reference)
        assert banded.dtype == np.uint8
        assert banded.shape == (doc.height, doc.width, 3)
        np.testing.assert_array_equal(doc.samples, original)
    assert not doc.samples.flags.writeable


def test_preview_can_cancel_between_bands_without_changing_source(monkeypatch):
    doc = preview_document()
    original = doc.samples.copy()
    monkeypatch.setattr(renderer, "_BLOCK_PIXELS", doc.width * 7)
    checks = 0

    def cancel_after_first_band():
        nonlocal checks
        checks += 1
        return checks > 1

    with pytest.raises(renderer.RenderCancelled):
        renderer.render(doc, cancelled=cancel_after_first_band)
    assert checks > 1
    np.testing.assert_array_equal(doc.samples, original)
    with pytest.raises(renderer.RenderCancelled):
        renderer.render(doc, selected=0, cancelled=lambda: True)
