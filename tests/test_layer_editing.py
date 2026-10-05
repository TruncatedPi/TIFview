"""Layer changes keep merged samples, channel identities and undo in sync."""
from dataclasses import replace

import numpy as np
import pytest

from tifview.editing import EditSession, LayerPatch
from tifview.layerediting import LayerState, composite_samples, merged_pixels_match
from tifview.model import Channel, ImageDocument
from tifview.photoshop import DisplayInfo, read_resources, align_metadata
from tools.make_demo import photoshop_resources


class Stack:
    default_order = (0, 1)
    default_visible = frozenset({0, 1})

    def __init__(self, shape=(5, 7), depth=8, base=3):
        self.shape, self.depth, self.base = shape, depth, base
        self.reason = None

    def composite_reason(self, order, visible):
        return self.reason

    def composite_samples(self, order, visible):
        maximum = 2**self.depth - 1
        colors = np.zeros((*self.shape, self.base), np.float64)
        alpha = np.zeros(self.shape, np.float64)
        for identity in reversed(order):
            if identity not in visible:
                continue
            plane = np.zeros(self.shape, np.float64)
            color = np.zeros((*self.shape, self.base), np.float64)
            if identity == 0:
                plane[1:4, 2:6] = .6
                color[..., 0] = maximum
            else:
                plane.fill(1)
                color[..., -1] = maximum
            remaining = alpha * (1 - plane)
            new_alpha = plane + remaining
            colors = np.divide(color * plane[..., None] + colors * remaining[..., None],
                               new_alpha[..., None], out=np.zeros_like(colors), where=new_alpha[..., None] != 0)
            alpha = new_alpha
        return np.concatenate((np.rint(colors).astype(f"uint{self.depth}"),
                               np.rint(alpha * maximum).astype(f"uint{self.depth}")[..., None]), axis=-1)


def document(depth=8, associated=None, orientation=1, mode="RGB"):
    base = {"RGB": 3, "CMYK": 4, "Gray": 1, "WhiteIsZero": 1}[mode]
    stack = Stack(depth=depth, base=base)
    composite = stack.composite_samples(stack.default_order, stack.default_visible)
    process = composite[..., :base]
    channels = [Channel(i, f"Process {i}", "Process" if base > 1 else "Image", "test", (120, 120, 120))
                for i in range(base)]
    extras = []
    values = [process]
    if associated is not None:
        if associated:
            process = ((process.astype(np.uint64) * composite[..., -1, None] + (2**depth - 1) // 2) //
                       (2**depth - 1)).astype(process.dtype)
            values[0] = process
        channels.append(Channel(len(channels), "Transparency", "Transparency", "test", (130, 130, 130),
                                associated=associated))
        values.append(composite[..., -1, None])
        extras.append(1 if associated else 2)
    for name, kind, display_mode, shade in [("White", "Spot", 2, 41), ("Selection", "Alpha mask", 1, 67),
                                           ("Varnish", "Spot", 2, 123)]:
        info = DisplayInfo(0, (65535, 65535, 65535, 0), 30, display_mode, 1077)
        channels.append(Channel(len(channels), name, kind, "test", (255, 255, 255), info))
        values.append(np.full((*stack.shape, 1), shade * (257 if depth == 16 else 1), process.dtype))
        extras.append(0)
    resources = photoshop_resources(["White", "Selection", "Varnish"], [2, 1, 2], solidity=30)
    doc = ImageDocument(None, np.concatenate(values, axis=-1), channels, mode, base, depth,
                        orientation, photoshop_resources=resources, metadata={"extra_samples": extras})
    return doc, stack


def capture(session):
    doc = session.document
    return (doc.samples.copy(), doc.channels.copy(), doc.photoshop_resources, dict(doc.metadata),
            session.channel_ids, doc.layer_state, session.state)


def assert_capture(session, expected):
    pixels, channels, resources, metadata, ids, state, sequence = expected
    np.testing.assert_array_equal(session.document.samples, pixels)
    assert session.document.channels == channels
    assert session.document.photoshop_resources == resources
    assert session.document.metadata == metadata
    assert session.channel_ids == ids
    assert session.document.layer_state == state
    assert session.state == sequence


@pytest.mark.parametrize("depth", [8, 16])
@pytest.mark.parametrize("associated", [False, True])
@pytest.mark.parametrize("mode", ["RGB", "CMYK", "Gray", "WhiteIsZero"])
def test_visibility_rebuilds_native_process_transparency_and_exact_undo(depth, associated, mode):
    original, stack = document(depth, associated, orientation=6, mode=mode)
    session = EditSession(original)
    session.attach_layers(stack)
    before = capture(session)
    assert session.set_layer_visibility(1, False)
    expected = stack.composite_samples((0, 1), frozenset({0}))
    process = expected[..., :original.base_count]
    if associated:
        process = ((process.astype(np.uint64) * expected[..., -1, None] + original.maximum // 2) //
                   original.maximum).astype(process.dtype)
    np.testing.assert_array_equal(session.document.samples[..., :original.base_count], process)
    np.testing.assert_array_equal(session.document.samples[..., original.base_count], expected[..., -1])
    np.testing.assert_array_equal(session.document.samples[..., original.base_count + 1:],
                                  original.samples[..., original.base_count + 1:])
    assert session.document.layer_state == LayerState((0, 1), frozenset({0}))
    after = capture(session)
    session.undo()
    assert_capture(session, before)
    assert not session.dirty
    session.redo()
    assert_capture(session, after)
    np.testing.assert_array_equal(original.samples, before[0])
    assert not original.samples.flags.writeable


@pytest.mark.parametrize("depth", [8, 16])
@pytest.mark.parametrize("mode", ["RGB", "CMYK", "Gray", "WhiteIsZero"])
def test_hiding_background_adds_genuine_transparency_preserving_spot_records_and_identities(depth, mode):
    original, stack = document(depth=depth, mode=mode)
    session = EditSession(original)
    session.attach_layers(stack)
    before = capture(session)
    assert session.set_layer_visibility(1, False)
    result = session.document
    index = len(original.channels)
    assert result.channels[index].kind == "Transparency" and not result.channels[index].associated
    assert result.channels[index].display is None
    assert result.metadata["extra_samples"] == [0, 0, 0, 2]
    assert result.metadata["channel_source_indices"] == [*range(index), None]
    assert result.metadata["layer_generated_transparency"] is True
    assert session.channel_ids[:-1] == before[4] and session.channel_ids[-1] not in before[4]
    np.testing.assert_array_equal(result.samples[..., original.base_count:index],
                                  original.samples[..., original.base_count:])
    ps = read_resources(result.photoshop_resources)
    warnings = []
    displays = align_metadata(ps.displays, result.metadata["extra_samples"], "displays", warnings)
    assert not warnings and [displays[i].mode for i in range(3)] == [2, 1, 2]
    assert 3 not in displays
    after = capture(session)
    session.undo()
    assert_capture(session, before)
    session.redo()
    assert_capture(session, after)
    generated_id = session.channel_ids[-1]
    session.set_layer_visibility(1, True)
    assert session.channel_ids[-1] == generated_id
    assert np.all(session.document.samples[..., -1] == original.maximum)


def test_layer_order_uses_stable_identity_changes_composite_and_roundtrips():
    original, stack = document(16, associated=False)
    session = EditSession(original)
    session.attach_layers(stack)
    before = capture(session)
    assert session.move_layer(0, 1) == 0
    assert session.document.layer_state.order == (1, 0)
    np.testing.assert_array_equal(session.document.samples[..., :4],
                                  stack.composite_samples((1, 0), stack.default_visible))
    assert isinstance(session.history[-1], LayerPatch)
    assert session.history[-1].added_plane is None
    session.undo()
    assert_capture(session, before)
    session.redo()
    assert session.document.layer_state.order == (1, 0)


def test_mixed_layer_spot_and_raster_history_retains_alpha_and_exact_checkpoints():
    original, stack = document(16, orientation=7)
    session = EditSession(original)
    white = original.base_count
    session.apply(white, (1, 1, 3, 3), np.full((2, 2), 255, np.uint8), 0)
    session.attach_layers(stack)
    session.undo()
    assert session.document.layer_state == LayerState(stack.default_order, stack.default_visible)
    snapshots = [capture(session)]

    def record():
        snapshots.append(capture(session))

    session.apply(white, (1, 1, 3, 3), np.full((2, 2), 255, np.uint8), 0)
    record()
    session.set_layer_visibility(1, False)
    record()
    added = session.add_spot("Extra white")
    record()
    session.apply(added, (2, 1, 4, 3), np.full((2, 2), 128, np.uint8), 3000)
    record()
    session.move_spot(added, 1)
    record()
    session.move_layer(0, 1)
    record()
    session.mark_saved()
    session.delete_spot(white + 2)
    record()
    session.set_layer_visibility(0, False)
    record()
    for expected in reversed(snapshots[:-1]):
        session.undo()
        assert_capture(session, expected)
        assert session.dirty == (session.state != session.saved_state)
    for expected in snapshots[1:]:
        session.redo()
        assert_capture(session, expected)
    assert not session.can_redo


@pytest.mark.parametrize("before_layers", [True, False])
def test_layer_change_rejects_separate_process_paint_without_discarding_pixels(before_layers):
    original, stack = document(associated=True)
    session = EditSession(original)
    session.attach_layers(stack)
    if not before_layers:
        session.set_layer_visibility(1, False)
    session.apply(0, (2, 1, 4, 3), np.full((2, 2), 255, np.uint8), 33)
    before = capture(session)
    count = len(session.history)
    with pytest.raises(ValueError, match="painted separately"):
        session.move_layer(0, 1)
    assert_capture(session, before)
    assert len(session.history) == count
    session.undo()
    session.move_layer(0, 1)


def test_metadata_only_layer_change_still_has_history_and_requires_backend_support():
    original, stack = document()
    stack.default_visible = frozenset({1})
    original = replace(original, samples=np.concatenate((
        stack.composite_samples(stack.default_order, stack.default_visible)[..., :3], original.samples[..., 3:]), axis=-1))
    session = EditSession(original)
    session.attach_layers(stack)
    session.move_layer(0, 1)
    assert session.history[-1].pixels is None
    assert session.document.layer_state.order == (1, 0)
    before = capture(session)
    stack.reason = "Visible adjustment layers are not supported"
    with pytest.raises(ValueError, match="adjustment"):
        session.set_layer_visibility(0, True)
    assert_capture(session, before)


def test_layer_history_limit_rejection_is_atomic_and_new_branch_has_fresh_alpha_identity():
    original, stack = document()
    reference = EditSession(original)
    reference.attach_layers(stack)
    reference.set_layer_visibility(1, False)
    required = reference.history[-1].bytes
    session = EditSession(original, history_limit=required - 1)
    session.attach_layers(stack)
    before = capture(session)
    with pytest.raises(ValueError, match="undo limit"):
        session.set_layer_visibility(1, False)
    assert_capture(session, before)
    assert not session.can_undo and not session.dirty
    first_id = reference.channel_ids[-1]
    reference.undo()
    reference.set_layer_visibility(0, False)
    reference.set_layer_visibility(1, False)
    assert reference.channel_ids[-1] != first_id
    assert not reference.can_redo


def test_layer_noops_bad_inputs_and_unattached_stack_do_not_change_history():
    original, stack = document()
    session = EditSession(original)
    with pytest.raises(ValueError, match="valid Photoshop layer"):
        session.set_layer_visibility(0, False)
    session.attach_layers(stack)
    assert not session.set_layer_visibility(0, True)
    assert session.move_layer(0, -1) == 0 and session.move_layer(1, 1) == 1
    for operation in (lambda: session.move_layer(2, -1), lambda: session.move_layer(0, 0),
                      lambda: session.set_layer_visibility(0, 1), lambda: session.set_layer_visibility(2, False)):
        with pytest.raises(ValueError):
            operation()
    assert not session.can_undo and not session.dirty
    assert session.document is original


def test_invalid_backend_samples_or_layer_state_are_rejected():
    original, stack = document()
    state = LayerState((0, 0), frozenset({0}))
    with pytest.raises(ValueError, match="unknown layer"):
        composite_samples(original, stack, state)
    stack.composite_samples = lambda *args: np.zeros((1, 1, 4), np.uint8)
    session = EditSession(original)
    session.attach_layers(stack)
    with pytest.raises(ValueError, match="dimensions"):
        session.set_layer_visibility(0, False)
    assert not session.dirty and not session.can_undo


def test_fast_merged_proof_survives_spot_moves_and_undo_but_detects_later_process_paint():
    original, stack = document()
    session = EditSession(original)
    session.attach_layers(stack)
    session.set_layer_visibility(1, False)
    proof = session.document.layer_merged_samples
    assert proof is session.document.samples and merged_pixels_match(session.document)
    session.add_spot("Another spot")
    session.move_spot(len(session.document.channels) - 1, 1)
    assert session.document.layer_merged_samples is proof and merged_pixels_match(session.document)
    session.undo()
    session.undo()
    session.undo()
    assert session.document.layer_merged_samples is None
    session.redo()
    proof = session.document.layer_merged_samples
    assert merged_pixels_match(session.document)
    session.apply(0, (2, 1, 4, 3), np.full((2, 2), 255, np.uint8), 10)
    assert not np.shares_memory(session.document.samples, proof)
    assert not merged_pixels_match(session.document)
    session.undo()
    assert merged_pixels_match(session.document)


@pytest.mark.parametrize("depth", [8, 16])
def test_hiding_the_last_unsupported_visible_layer_allows_first_recomposition(depth):
    original, stack = document(depth=depth)
    # Its merged image may contain appearance that the unsupported layer adds.
    # The explicit hide should still permit the now-supported remainder.
    samples = original.samples.copy()
    samples[0, 0, 0] ^= 1
    original = replace(original, samples=samples)
    stack.composite_reason = lambda order, visible: "Unsupported feathered mask" if 1 in visible else None
    session = EditSession(original)
    session.attach_layers(stack)
    assert session.set_layer_visibility(1, False)
    assert merged_pixels_match(session.document)
    assert not hasattr(stack, "_baseline_verification")
    before = capture(session)
    with pytest.raises(ValueError, match="feathered"):
        session.set_layer_visibility(1, True)
    assert_capture(session, before)


def test_new_transparency_without_preexisting_photoshop_extra_channel_resources():
    original, stack = document()
    original = replace(original, samples=original.samples[..., :original.base_count].copy(),
                       channels=original.channels[:original.base_count], photoshop_resources=None,
                       metadata={"extra_samples": []})
    session = EditSession(original)
    session.attach_layers(stack)
    session.set_layer_visibility(1, False)
    assert session.document.metadata["extra_samples"] == [2]
    metadata = read_resources(session.document.photoshop_resources)
    assert metadata.names == ["Transparency"] and not metadata.warnings
    assert metadata.displays == []
    session.undo()
    assert session.document.photoshop_resources is None
    assert session.document.samples.shape == original.samples.shape


def test_layer_history_eviction_retains_valid_current_proof_and_does_not_store_image_snapshots():
    original, stack = document()
    reference = EditSession(original)
    reference.attach_layers(stack)
    reference.set_layer_visibility(1, False)
    reference.move_layer(0, 1)
    reference.set_layer_visibility(0, False)
    limit = max(patch.bytes for patch in reference.history)
    session = EditSession(original, history_limit=limit)
    session.attach_layers(stack)
    session.set_layer_visibility(1, False)
    session.move_layer(0, 1)
    session.set_layer_visibility(0, False)
    assert sum(patch.bytes for patch in session.history) <= limit
    assert len(session.history) < 3
    assert all(not hasattr(patch.before, "layer_merged_samples") for patch in session.history)
    assert merged_pixels_match(session.document)
    session.undo()
    assert merged_pixels_match(session.document)
    session.redo()
    assert merged_pixels_match(session.document)


@pytest.mark.parametrize("depth", [8, 16])
@pytest.mark.parametrize("changed_channel", [0, 3])
@pytest.mark.parametrize("prior_spot_edit", [False, True])
def test_mismatched_supported_baseline_rejects_first_layer_edit_without_changing_document_or_history(
        depth, changed_channel, prior_spot_edit):
    original, stack = document(depth=depth, associated=False)
    samples = original.samples.copy()
    samples[0, 0, changed_channel] ^= 1
    original = replace(original, samples=samples)
    original_pixels = original.samples.copy()
    original_metadata = dict(original.metadata)
    session = EditSession(original)
    session.attach_layers(stack)
    if prior_spot_edit:
        session.add_spot("Existing edit must survive")
    before = capture(session)
    history = tuple(session.history)
    checkpoint = session.saved_state
    for operation in (lambda: session.set_layer_visibility(1, False), lambda: session.move_layer(0, 1)):
        with pytest.raises(ValueError, match="saved TIFF composite does not match"):
            operation()
        assert_capture(session, before)
        assert tuple(session.history) == history
        assert session.saved_state == checkpoint
        assert session.dirty == prior_spot_edit
        np.testing.assert_array_equal(original.samples, original_pixels)
        assert original.metadata == original_metadata
    assert stack._baseline_verification[0] is original.samples
    assert stack._baseline_verification[1] is False


@pytest.mark.parametrize("depth", [8, 16])
@pytest.mark.parametrize("associated", [None, False, True])
@pytest.mark.parametrize("mode", ["RGB", "CMYK"])
def test_exact_supported_baseline_allows_edits_and_caches_verification_outside_document_metadata(depth, associated, mode):
    original, stack = document(depth=depth, associated=associated, mode=mode)
    session = EditSession(original)
    session.attach_layers(stack)
    metadata_before = dict(original.metadata)
    original_pixels = original.samples.copy()
    baseline_calls = 0
    compose = stack.composite_samples

    def counted(order, visible):
        nonlocal baseline_calls
        if tuple(order) == stack.default_order and frozenset(visible) == stack.default_visible:
            baseline_calls += 1
        return compose(order, visible)

    stack.composite_samples = counted
    assert session.set_layer_visibility(1, False)
    assert baseline_calls == 1
    assert stack._baseline_verification[0] is original.samples
    assert stack._baseline_verification[1] is True
    assert merged_pixels_match(session.document)
    session.move_layer(0, 1)
    assert baseline_calls == 1
    assert merged_pixels_match(session.document)
    assert original.metadata == metadata_before
    assert "_baseline_verification" not in session.document.metadata
    np.testing.assert_array_equal(original.samples, original_pixels)
    session.undo()
    session.undo()
    assert not session.dirty
    np.testing.assert_array_equal(session.document.samples, original_pixels)
