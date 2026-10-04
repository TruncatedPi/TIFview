"""C2PA metadata IFDs must not be mistaken for independent printing images."""
import hashlib
import struct

import numpy as np
import pytest
import tifffile

from tifview.editing import EditSession
from tifview.reader import load_image
from tifview.tiffpages import inspect_page_layout
from tifview.writer import SaveOptions, reduce_half, save_tiff_copy
from tools.make_demo import photoshop_resources


def append_metadata_ifd(path, tags=((52545, 7, b"SYNTHETIC manifest placeholder"),)):
    """Append a classic little-endian metadata-only IFD without pixel tags."""
    raw = bytearray(path.read_bytes())
    assert raw[:4] == b"II*\0"
    offset = struct.unpack_from("<I", raw, 4)[0]
    while True:
        count = struct.unpack_from("<H", raw, offset)[0]
        next_pointer = offset + 2 + count * 12
        offset = struct.unpack_from("<I", raw, next_pointer)[0]
        if not offset:
            break
    raw += b"\0" * (len(raw) % 2)
    metadata_offset = len(raw)
    struct.pack_into("<I", raw, next_pointer, metadata_offset)
    payload_offset = metadata_offset + 2 + len(tags) * 12 + 4
    directory = bytearray(struct.pack("<H", len(tags)))
    payloads = bytearray()
    for code, dtype, payload in tags:
        assert len(payload) > 4
        directory += struct.pack("<HHII", code, dtype, len(payload), payload_offset + len(payloads))
        payloads += payload
    raw += directory + b"\0\0\0\0" + payloads
    path.write_bytes(raw)


def printing_source(tmp_path, depth=8, pyramid=True):
    maximum = 2**depth - 1
    samples = np.full((9, 13, 5), maximum, dtype=f"uint{depth}")
    samples[..., 0] = maximum // 3
    samples[1:7, 3:8, 3] = 0
    resources = photoshop_resources(["White", "Varnish"], [2, 2])
    source = tmp_path / "source.tif"
    common = dict(photometric="rgb", extrasamples=[0, 0], compression="lzw", metadata=None)
    with tifffile.TiffWriter(source) as writer:
        writer.write(samples, subifds=1 if pyramid else None,
                     extratags=[(34377, 7, len(resources), resources, False)], **common)
        if pyramid:
            writer.write(reduce_half(samples), subfiletype=1, **common)
    return source, samples


@pytest.mark.parametrize("depth", [8, 16])
@pytest.mark.parametrize("pyramid", [False, True])
def test_new_spot_saves_past_metadata_only_c2pa_ifd(tmp_path, depth, pyramid):
    source, samples = printing_source(tmp_path, depth, pyramid)
    append_metadata_ifd(source)
    before_hash = hashlib.sha256(source.read_bytes()).digest()
    with tifffile.TiffFile(source) as tif:
        assert len(tif.pages) == 2
        assert set(tif.pages[1].tags.keys()) == {52545}
        assert tif.pages[1].shape == ()
        layout = inspect_page_layout(tif)
        assert layout.manifest_only_pages == 1
        assert layout.independent_pages == 0
        assert layout.pyramid_levels == int(pyramid)
    original = load_image(source)
    np.testing.assert_array_equal(original.samples, samples)
    assert original.metadata["independent_page_count"] == 0
    assert original.metadata["content_credentials_ifds"] == 1
    assert original.metadata["has_content_credentials"]
    assert any("Content Credentials" in warning for warning in original.warnings)
    session = EditSession(original)
    index = session.add_spot("New white")
    session.apply(index, (1, 1, 4, 4), np.full((3, 3), 255, np.uint8), 0)
    target = save_tiff_copy(original, session.document, tmp_path / "edited.tif")
    reopened = load_image(target)
    np.testing.assert_array_equal(reopened.samples, session.document.samples)
    assert [c.name for c in reopened.channels[-3:]] == ["White", "Varnish", "New white"]
    assert all(c.kind == "Spot" for c in reopened.channels[-3:])
    with tifffile.TiffFile(target) as tif:
        assert len(tif.pages) == 1
        assert 52545 not in tif.pages[0].tags
        assert len(tif.pages[0].subifds) == 1
        np.testing.assert_array_equal(tif.pages[0].pages[0].asarray(), reduce_half(session.document.samples))
    assert hashlib.sha256(source.read_bytes()).digest() == before_hash


@pytest.mark.parametrize("extra_tags", [
    ((52545, 7, b"SYNTHETIC manifest placeholder"), (60000, 7, b"unknown metadata")),
    ((52545, 1, b"wrong manifest tag type"),),
    ((60000, 7, b"unrecognised metadata-only page"),),
])
def test_ambiguous_metadata_ifd_remains_protected(tmp_path, extra_tags):
    source, _ = printing_source(tmp_path)
    append_metadata_ifd(source, extra_tags)
    original = load_image(source)
    with tifffile.TiffFile(source) as tif:
        layout = inspect_page_layout(tif)
        assert layout.manifest_only_pages == 0
        assert layout.independent_pages == 1
    with pytest.raises(ValueError, match="independent image pages"):
        save_tiff_copy(original, original, tmp_path / "copy.tif")
    assert not (tmp_path / "copy.tif").exists()


def test_manifest_ifd_not_at_end_is_not_discarded(tmp_path):
    source, _ = printing_source(tmp_path)
    append_metadata_ifd(source)
    append_metadata_ifd(source)
    with tifffile.TiffFile(source) as tif:
        layout = inspect_page_layout(tif)
        assert layout.manifest_only_pages == 1
        assert layout.independent_pages == 1
    original = load_image(source)
    with pytest.raises(ValueError, match="independent image pages"):
        save_tiff_copy(original, original, tmp_path / "copy.tif")


@pytest.mark.parametrize("subfiletype", [0, 1])
def test_real_independent_image_after_primary_stays_protected(tmp_path, subfiletype):
    source, samples = printing_source(tmp_path)
    with tifffile.TiffWriter(source, append=True) as writer:
        writer.write(samples[:4, :6], photometric="rgb", extrasamples=[0, 0],
                     planarconfig="contig", subfiletype=subfiletype, metadata=None)
    append_metadata_ifd(source)
    with tifffile.TiffFile(source) as tif:
        layout = inspect_page_layout(tif)
        assert layout.manifest_only_pages == 1
        assert layout.independent_pages == 1
    original = load_image(source)
    session = EditSession(original)
    session.add_spot("New white")
    with pytest.raises(ValueError, match="independent image pages"):
        save_tiff_copy(original, session.document, tmp_path / "copy.tif", SaveOptions(pyramid=False))


def test_last_metadata_only_manifest_subifd_does_not_add_a_pyramid_level(tmp_path):
    source = tmp_path / "child-manifest.tif"
    samples = np.full((9, 13, 3), 128, np.uint8)
    with tifffile.TiffWriter(source) as writer:
        writer.write(samples, photometric="rgb", subifds=2, metadata=None)
        writer.write(reduce_half(samples), photometric="rgb", subfiletype=1, metadata=None)
        writer.write(reduce_half(reduce_half(samples)), photometric="rgb", subfiletype=1, metadata=None)
    with tifffile.TiffFile(source) as tif:
        offset = tif.pages[0].pages[1].offset
    raw = bytearray(source.read_bytes())
    payload = b"SYNTHETIC child manifest placeholder"
    raw[offset:offset + 18] = struct.pack("<HHHIII", 1, 52545, 7, len(payload), len(raw), 0)
    raw += payload
    source.write_bytes(raw)
    with tifffile.TiffFile(source) as tif:
        layout = inspect_page_layout(tif)
        assert layout.manifest_only_pages == 1
        assert layout.pyramid_levels == 1
        assert layout.independent_pages == 0
    original = load_image(source)
    target = save_tiff_copy(original, original, tmp_path / "no-credentials.tif")
    with tifffile.TiffFile(target) as tif:
        assert len(tif.pages[0].subifds) == 1


@pytest.mark.parametrize("reduced", [False, True])
def test_unflagged_or_incompatible_child_image_is_protected(tmp_path, reduced):
    source = tmp_path / "extra-child.tif"
    samples = np.full((9, 13, 5), 128, np.uint8)
    with tifffile.TiffWriter(source) as writer:
        writer.write(samples, photometric="rgb", extrasamples=[0, 0], subifds=1, metadata=None)
        child = reduce_half(samples)
        if reduced:
            # Flagging a thumbnail is insufficient when it drops stored channels.
            child = child[..., :3]
        writer.write(child, photometric="rgb", extrasamples=[] if reduced else [0, 0],
                     subfiletype=int(reduced), metadata=None)
    original = load_image(source)
    with tifffile.TiffFile(source) as tif:
        layout = inspect_page_layout(tif)
        assert layout.independent_pages == 1
    with pytest.raises(ValueError, match="independent image pages"):
        save_tiff_copy(original, original, tmp_path / "copy.tif")


def test_reduced_subifd_with_different_inkset_is_protected(tmp_path):
    source = tmp_path / "different-inks.tif"
    samples = np.full((9, 13, 4), 128, np.uint8)
    with tifffile.TiffWriter(source) as writer:
        writer.write(samples, photometric="separated", subifds=1, metadata=None,
                     extratags=[(332, 3, 1, 1, False)])
        writer.write(reduce_half(samples), photometric="separated", subfiletype=1, metadata=None,
                     extratags=[(332, 3, 1, 2, False)])
    original = load_image(source)
    with tifffile.TiffFile(source) as tif:
        assert inspect_page_layout(tif).independent_pages == 1
    with pytest.raises(ValueError, match="independent image pages"):
        save_tiff_copy(original, original, tmp_path / "copy.tif")
