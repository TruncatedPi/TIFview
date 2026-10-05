"""Layer visibility/order edits and native merged-channel reconstruction."""
from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from .model import Channel, ImageDocument
from .photoshop import read_resources, rewrite_channel_resources
from . import spots


@dataclass(frozen=True)
class LayerState:
    """Stable layer identities in Photoshop's top-to-bottom stack order."""
    order: tuple[int, ...]
    visible: frozenset[int]


def default_state(stack) -> LayerState:
    return LayerState(tuple(stack.default_order), frozenset(stack.default_visible))


def validate_state(stack, state: LayerState):
    identities = set(stack.default_order)
    if (len(state.order) != len(identities) or set(state.order) != identities or
            not state.visible <= identities):
        raise ValueError("Layer order or visibility refers to an unknown layer")


def composite_samples(doc: ImageDocument, stack, state: LayerState) -> np.ndarray:
    """Validate straight native process/alpha samples returned by the backend."""
    validate_state(stack, state)
    reason = stack.composite_reason(state.order, state.visible)
    if reason:
        raise ValueError(reason)
    result = stack.composite_samples(state.order, state.visible)
    if (not isinstance(result, np.ndarray) or result.shape != (*doc.samples.shape[:2], doc.base_count + 1) or
            result.dtype != doc.samples.dtype):
        raise ValueError("Layer composite does not match the image dimensions or native bit depth")
    return result


def _protected_pixels(doc: ImageDocument, composite: np.ndarray):
    transparency = [c for c in doc.channels if c.kind == "Transparency"]
    if len(transparency) > 1:
        raise ValueError("Layer editing supports at most one image transparency channel")
    alpha_channel = transparency[0] if transparency else None
    process = composite[..., :doc.base_count]
    alpha = composite[..., doc.base_count]
    indices = tuple(range(doc.base_count)) + (() if alpha_channel is None else (alpha_channel.index,))
    if alpha_channel is None:
        return indices, process
    values = np.empty((*alpha.shape, len(indices)), dtype=doc.samples.dtype)
    values[..., -1] = alpha
    if alpha_channel.associated:
        # Even uint16 products plus half the maximum fit uint32. Process in
        # bands instead of allocating several whole-image uint64 arrays.
        rows = max(1, 65536 // max(1, alpha.shape[1]))
        for y in range(0, alpha.shape[0], rows):
            block = np.multiply(process[y:y + rows], alpha[y:y + rows, :, None], dtype=np.uint32)
            block += doc.maximum // 2
            block //= doc.maximum
            values[y:y + rows, :, :doc.base_count] = block
    else:
        values[..., :doc.base_count] = process
    return indices, values


def require_unchanged_merged_pixels(original: ImageDocument, current: ImageDocument, stack):
    """Avoid discarding channel painting when recomputing a layer composite."""
    if current.metadata.get("layer_composite_applied"):
        matches = merged_pixels_match(current)
    else:
        sources = spots._source_indices(current)
        protected = list(range(original.base_count)) + [c.index for c in original.channels if c.kind == "Transparency"]
        matches = all(source in sources and np.array_equal(original.samples[..., source],
                     current.samples[..., sources.index(source)]) for source in protected)
    if not matches:
        raise ValueError("Process or image-transparency pixels were painted separately from the layers. "
                         "Undo those pixel edits before changing layer visibility or order.")
    # A supported baseline must match the authoritative primary TIFF. This
    # catches unmodelled Photoshop document settings or stale merged pixels
    # before an otherwise small layer change replaces unrelated appearance.
    # An explicit hide of unsupported baseline features can still proceed.
    if stack.composite_reason(stack.default_order, stack.default_visible) is None:
        checked = getattr(stack, "_baseline_verification", None)
        if checked is None or checked[0] is not original.samples:
            state = default_state(stack)
            baseline = composite_samples(original, stack, state)
            indices, expected = _protected_pixels(original, baseline)
            matched = all(np.array_equal(original.samples[..., index], expected[..., position])
                          for position, index in enumerate(indices))
            stack._baseline_verification = (original.samples, matched)
        if not stack._baseline_verification[1]:
            raise ValueError("The saved TIFF composite does not match the supported Photoshop layer pixels. "
                             "Layer visibility/order editing is unavailable for this file; inspect it in Photoshop.")


def merged_pixels_match(doc: ImageDocument) -> bool:
    """Fast native proof that later painting did not invalidate layer pixels."""
    expected = doc.layer_merged_samples
    if (expected is None or expected.shape[:2] != doc.samples.shape[:2] or
            expected.dtype != doc.samples.dtype or expected.shape[-1] < doc.base_count):
        return False
    if not np.array_equal(doc.samples[..., :doc.base_count], expected[..., :doc.base_count]):
        return False
    transparency = [c for c in doc.channels if c.kind == "Transparency"]
    if doc.layer_merged_transparency is None:
        return not transparency
    return (len(transparency) == 1 and 0 <= doc.layer_merged_transparency < expected.shape[-1] and
            np.array_equal(doc.samples[..., transparency[0].index], expected[..., doc.layer_merged_transparency]))


def recompose_document(doc: ImageDocument, stack, state: LayerState):
    """Return a new merged view, retaining every spot and saved-alpha plane.

    The optional appended plane is a genuine unassociated TIFF transparency
    sample, rather than a saved-alpha mask. Its original-source identity is
    ``None`` and it remains until undo removes the edit that introduced it.
    """
    spots._validate(doc)
    composite = composite_samples(doc, stack, state)
    indices, values = _protected_pixels(doc, composite)
    alpha = composite[..., doc.base_count]
    add_alpha = len(indices) == doc.base_count and np.any(alpha != doc.maximum)
    count = len(doc.channels) + int(add_alpha)
    if count * doc.samples.shape[0] * doc.samples.shape[1] * doc.samples.dtype.itemsize > 512 * 2**20:
        raise ValueError("Layer transparency would exceed the 512 MiB decoded-image limit")
    channels = list(doc.channels)
    resources = doc.photoshop_resources
    metadata = dict(doc.metadata)
    if add_alpha:
        channels.append(Channel(len(channels), "Transparency", "Transparency",
                                "TIFF ExtraSamples=2 generated from Photoshop layers", (130, 130, 130)))
        extras = spots._extras(doc)
        sources = list(range(len(doc.channels))) + [None]
        resources = rewrite_channel_resources(resources, doc.channels, channels, sources, doc.base_count, extras)
        metadata["extra_samples"] = [*extras, 2]
        metadata["channel_source_indices"] = [*spots._source_indices(doc), None]
        metadata["photoshop_resource_ids"] = read_resources(resources).resource_ids if resources else []
        metadata["layer_generated_transparency"] = True
    samples = np.empty((*doc.samples.shape[:2], count), dtype=doc.samples.dtype)
    samples[..., :len(doc.channels)] = doc.samples
    for position, index in enumerate(indices):
        samples[..., index] = values[..., position]
    if add_alpha:
        samples[..., -1] = alpha
    metadata["layer_composite_applied"] = True
    result = replace(doc, samples=samples, channels=channels, photoshop_resources=resources,
                     metadata=metadata, warnings=list(doc.warnings), layer_stack=stack, layer_state=state,
                     layer_merged_samples=samples,
                     layer_merged_transparency=next((c.index for c in channels if c.kind == "Transparency"), None))
    return result, add_alpha
