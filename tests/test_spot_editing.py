"""Spot structure changes retain native data, metadata and exact bounded undo."""
from dataclasses import replace
import struct

import numpy as np
import pytest
import tifffile

from tifview.editing import EditSession, StructuralPatch
from tifview.model import Channel, ImageDocument
from tifview.photoshop import DisplayInfo
from tifview.reader import load_image
from tools.make_demo import photoshop_resources, resource


def document(tmp_path, depth=8, mode="rgb", orientation=1, height=9, width=11):
    base_count = {"rgb": 3, "separated": 4, "minisblack": 1, "miniswhite": 1}[mode]
    count = base_count + 5
    y, x, c = np.indices((height, width, count), dtype=np.int64)
    maximum = 2**depth - 1
    samples = ((y * 19 + x * 13 + c * 37) % (maximum + 1)).astype(f"uint{depth}")
    resources = photoshop_resources(["White A", "Saved selection", "White B", "Varnish"],
                                    [2, 1, 2, 2], solidity=37)
    path = tmp_path / "original.tif"
    tifffile.imwrite(path, samples, photometric=mode, extrasamples=[1, 0, 0, 0, 0], metadata=None,
                     extratags=[(274, 3, 1, orientation, False),
                                (34377, 1, len(resources), resources, False)])
    return load_image(path)


@pytest.mark.parametrize("depth", [8, 16])
@pytest.mark.parametrize("mode", ["rgb", "separated", "minisblack", "miniswhite"])
def test_new_spot_is_empty_native_plane_and_source_stays_immutable(tmp_path, depth, mode):
    doc = document(tmp_path, depth, mode)
    pixels, resources, metadata = doc.samples.copy(), doc.photoshop_resources, dict(doc.metadata)
    session = EditSession(doc)
    index = session.add_spot("New white")
    assert index == len(doc.channels)
    assert session.document.channels[index].kind == "Spot"
    assert session.document.channels[index].display.opacity == 100
    assert session.document.channels[index].color == (255, 255, 255)
    np.testing.assert_array_equal(session.document.samples[..., :index], pixels)
    assert np.all(session.document.samples[..., index] == doc.maximum)
    assert session.document.samples.dtype == doc.samples.dtype
    assert session.document.metadata["extra_samples"] == [1, 0, 0, 0, 0, 0]
    assert session.document.metadata["channel_source_indices"] == [*range(index), None]
    assert session.changed_channels() == {index}
    assert not session.document.samples.flags.writeable
    np.testing.assert_array_equal(doc.samples, pixels)
    assert doc.photoshop_resources == resources and doc.metadata == metadata
    session.undo()
    np.testing.assert_array_equal(session.document.samples, pixels)
    assert session.document.channels == doc.channels
    assert session.document.photoshop_resources == resources
    assert session.document.metadata == metadata
    assert not session.dirty
    session.redo()
    assert session.document.channels[-1].name == "New white" and session.dirty


def test_duplicate_retains_exact_samples_and_display_but_has_new_identity(tmp_path):
    doc = document(tmp_path, 16, "separated", orientation=6)
    source = doc.base_count + 1
    session = EditSession(doc)
    index = session.add_spot("Duplicate", source_index=source)
    np.testing.assert_array_equal(session.document.samples[..., index], doc.samples[..., source])
    assert session.document.channels[index].display == doc.channels[source].display
    assert session.channel_ids[index] not in session.channel_ids[:index]
    assert session.document.metadata["channel_source_indices"][index] is None
    ids = session.channel_ids
    session.apply(index, (1, 2, 4, 5), np.full((3, 3), 255, np.uint8), 0)
    np.testing.assert_array_equal(session.document.samples[..., source], doc.samples[..., source])
    session.undo()
    np.testing.assert_array_equal(session.document.samples[..., index], doc.samples[..., source])
    session.undo()
    assert len(session.document.channels) == len(doc.channels)
    session.redo()
    assert session.channel_ids == ids
    np.testing.assert_array_equal(session.document.samples[..., index], doc.samples[..., source])


def test_move_permutates_only_spot_slots_and_locks_other_indices(tmp_path):
    doc = document(tmp_path)
    slots = [c.index for c in doc.channels if c.kind == "Spot"]
    session = EditSession(doc)
    selected = session.channel_ids[slots[-1]]
    new_index = session.move_spot(slots[-1], 1)
    assert new_index == slots[0]
    assert session.channel_ids[new_index] == selected
    order = list(range(len(doc.channels)))
    for slot, before in zip(slots, [slots[-1], *slots[:-1]]):
        order[slot] = before
    np.testing.assert_array_equal(session.document.samples, doc.samples[..., order])
    for c in doc.channels:
        if c.kind != "Spot":
            assert session.document.channels[c.index] == c
    assert session.document.metadata["channel_source_indices"] == order
    assert session.document.metadata["extra_samples"] == doc.metadata["extra_samples"]
    assert [session.document.channel_label(i) for i in slots] == ["1. Varnish", "2. White A", "3. White B"]
    assert all(p.added_plane is None and p.deleted_plane is None for p in session.history)
    session.undo()
    np.testing.assert_array_equal(session.document.samples, doc.samples)
    assert session.channel_ids == tuple(range(len(doc.channels)))
    assert not session.dirty and session.changed_channels() == set()


def test_delete_undo_preserves_later_saved_alpha_and_original_identity(tmp_path):
    doc = document(tmp_path)
    removed = doc.base_count + 1
    saved_alpha = removed + 1
    session = EditSession(doc)
    assert session.delete_spot(removed)
    order = [i for i in range(len(doc.channels)) if i != removed]
    np.testing.assert_array_equal(session.document.samples, doc.samples[..., order])
    assert session.document.channels[saved_alpha - 1].kind == "Alpha mask"
    assert session.document.metadata["channel_source_indices"] == order
    assert session.document.metadata["extra_samples"] == [1, 0, 0, 0]
    assert all(0 <= i < len(session.document.channels) for i in session.changed_channels())
    assert session.history[0].deleted_plane.nbytes == doc.samples[..., removed].nbytes
    session.undo()
    np.testing.assert_array_equal(session.document.samples, doc.samples)
    assert session.document.channels == doc.channels
    session.redo()
    np.testing.assert_array_equal(session.document.samples, doc.samples[..., order])


def test_structure_and_pixel_history_interleave_exactly_with_save_checkpoint(tmp_path):
    doc = document(tmp_path, 16, orientation=7)
    session = EditSession(doc)
    snapshots = []

    def capture():
        snapshots.append((session.document.samples.copy(), session.document.channels.copy(),
                          session.document.photoshop_resources, dict(session.document.metadata),
                          session.channel_ids, session.state))

    capture()
    first = doc.base_count + 1
    session.apply(first, (1, 1, 3, 3), np.full((2, 2), 255, np.uint8), 0)
    capture()
    duplicate = session.add_spot("White copy", source_index=first)
    capture()
    session.apply(duplicate, (2, 2, 5, 5), np.full((3, 3), 255, np.uint8), 12000)
    capture()
    moved = session.move_spot(duplicate, 1)
    capture()
    session.update_spot(moved, "Copy renamed", (9, 45, 203), 62)
    capture()
    session.mark_saved()
    session.delete_spot(first + 2)
    capture()
    session.apply(moved, (1, 1, 3, 3), np.full((2, 2), 128, np.uint8), 500)
    capture()
    assert session.dirty
    for expected in reversed(snapshots[:-1]):
        session.undo()
        pixels, channels, resources, metadata, ids, state = expected
        np.testing.assert_array_equal(session.document.samples, pixels)
        assert session.document.channels == channels
        assert session.document.photoshop_resources == resources
        assert session.document.metadata == metadata
        assert session.channel_ids == ids and session.state == state
        assert session.dirty == (state != session.saved_state)
    assert not session.can_undo
    for expected in snapshots[1:]:
        session.redo()
        pixels, channels, resources, metadata, ids, state = expected
        np.testing.assert_array_equal(session.document.samples, pixels)
        assert session.document.channels == channels
        assert session.document.photoshop_resources == resources
        assert session.document.metadata == metadata
        assert session.channel_ids == ids and session.state == state
    np.testing.assert_array_equal(doc.samples, snapshots[0][0])


def test_new_branch_drops_structural_redo_and_never_reuses_identity(tmp_path):
    session = EditSession(document(tmp_path))
    original_count = len(session.document.channels)
    index = session.add_spot("First")
    first_id = session.channel_ids[index]
    session.mark_saved()
    session.undo()
    assert session.dirty
    second = session.add_spot("Second")
    assert second == original_count and session.channel_ids[second] != first_id
    assert not session.can_redo
    assert session.dirty
    session.mark_saved()
    session.update_spot(second, "Second renamed", None, 25)
    session.undo()
    assert not session.dirty


def test_history_stores_one_plane_per_add_delete_and_no_image_for_move_or_properties(tmp_path):
    doc = document(tmp_path, 16, height=200, width=240)
    session = EditSession(doc)
    index = session.add_spot("Added")
    session.move_spot(index, 1)
    session.update_spot(doc.base_count + 1, "Changed", None, 56)
    session.delete_spot(doc.base_count + 1)
    assert all(isinstance(p, StructuralPatch) for p in session.history)
    planes = [array for p in session.history for array in (p.added_plane, p.deleted_plane) if array is not None]
    assert len(planes) == 2
    assert sum(a.nbytes for a in planes) == doc.samples[..., 0].nbytes * 2
    assert all(a.ndim == 2 and not a.flags.writeable for a in planes)
    assert sum(p.bytes for p in session.history) < doc.samples.nbytes


def test_structural_undo_limit_failure_is_atomic_and_eviction_stays_bounded(tmp_path):
    doc = document(tmp_path)
    reference = EditSession(doc)
    reference.add_spot("Added")
    required = reference.history[0].bytes
    rejected = EditSession(doc, history_limit=required - 1)
    with pytest.raises(ValueError, match="undo limit"):
        rejected.add_spot("Added")
    assert rejected.document is doc and not rejected.can_undo and not rejected.dirty
    assert rejected.channel_ids == tuple(range(len(doc.channels)))
    reference.update_spot(len(doc.channels), "Renamed", None, 61)
    limit = max(required, reference.history[-1].bytes)
    session = EditSession(doc, history_limit=limit)
    session.add_spot("Added")
    session.update_spot(len(doc.channels), "Renamed", None, 61)
    assert sum(p.bytes for p in session.history) <= limit
    assert len(session.history) == 1
    session.undo()
    assert session.document.channels[-1].name == "Added"


def test_noop_properties_and_moves_do_not_allocate_history_or_change_metadata(tmp_path):
    doc = document(tmp_path)
    session = EditSession(doc)
    spot = doc.base_count + 1
    assert session.move_spot(spot, 1) == spot
    assert not session.update_spot(spot, doc.channels[spot].name)
    assert session.document is doc and not session.dirty and not session.can_undo


@pytest.mark.parametrize("index_offset", [-3, 0, 2])
def test_process_transparency_and_saved_alpha_cannot_be_deleted_moved_or_changed(tmp_path, index_offset):
    doc = document(tmp_path)
    index = doc.base_count + index_offset
    session = EditSession(doc)
    for operation in (lambda: session.delete_spot(index), lambda: session.move_spot(index, 1),
                      lambda: session.update_spot(index, "Changed"),
                      lambda: session.add_spot("Copy", source_index=index)):
        with pytest.raises(ValueError, match="locked"):
            operation()
    assert session.document is doc and not session.dirty


def test_rename_solidity_and_duplicate_preserve_original_cmyk_display_components(tmp_path):
    samples = np.full((8, 9, 4), 200, np.uint8)
    names = photoshop_resources(["White"], [2])
    # The non-RGB colour is deliberately not an exact roundtrip through RGB8.
    display = DisplayInfo(2, (60123, 51234, 44444, 62890), 29, 2, 1007)
    resources = names[:names.index(b"8BIM" + struct.pack(">H", 1077))]
    resources += resource(1007, struct.pack(">6HB", display.color_space, *display.components,
                                           display.opacity, display.mode) + b"\0")
    path = tmp_path / "cmyk-display.tif"
    tifffile.imwrite(path, samples, photometric="rgb", extrasamples=[0], metadata=None,
                     extratags=[(34377, 1, len(resources), resources, False)])
    doc = load_image(path)
    session = EditSession(doc)
    assert session.update_spot(3, "Renamed", None, 41)
    assert session.document.channels[3].display == replace(display, opacity=41)
    assert session.document.samples is doc.samples
    copied = session.add_spot("Copied", source_index=3, solidity=68)
    assert session.document.channels[copied].display == replace(display, opacity=68)
    session.undo()
    session.undo()
    assert session.document.channels[3].display == display
    assert session.document.photoshop_resources == resources


def test_invalid_inputs_and_unknown_extra_metadata_are_rejected_without_changes(tmp_path):
    doc = document(tmp_path)
    session = EditSession(doc)
    for operation in (lambda: session.add_spot(""), lambda: session.add_spot("Bad\0name"),
                      lambda: session.add_spot("a" * 256), lambda: session.add_spot("New", solidity=101),
                      lambda: session.add_spot("New", color=(256, 0, 0)),
                      lambda: session.move_spot(doc.base_count + 1, 0)):
        with pytest.raises(ValueError):
            operation()
    assert session.document is doc and not session.dirty
    channels = list(doc.channels)
    channels[-1] = replace(channels[-1], kind="Unknown")
    unknown = EditSession(replace(doc, channels=channels))
    with pytest.raises(ValueError, match="Unknown"):
        unknown.add_spot("New")


@pytest.mark.parametrize("mode,bits,dtype", [("Palette", 8, "uint8"), ("Gray", 1, "bool")])
def test_unsupported_spot_editing_formats_are_rejected(mode, bits, dtype):
    doc = ImageDocument(None, np.zeros((2, 3, 1), dtype),
                        [Channel(0, "Image", "Image", "test", (1, 2, 3))], mode, 1, bits)
    with pytest.raises(ValueError, match="8/16"):
        EditSession(doc).add_spot("New")
