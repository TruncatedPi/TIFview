"""Immutable spot-channel layout changes, retaining each native sample plane."""
from __future__ import annotations

from dataclasses import replace

import numpy as np

from .model import Channel, ImageDocument
from .photoshop import DisplayInfo, read_resources, rewrite_channel_resources


def _validate(doc: ImageDocument):
    if doc.bits not in (8, 16) or doc.color_mode not in ("RGB", "CMYK", "Gray", "WhiteIsZero"):
        raise ValueError("Spot editing supports unsigned 8/16-bit RGB, CMYK and grayscale images")
    if doc.samples.dtype.kind != "u" or doc.samples.dtype.itemsize != doc.bits // 8:
        raise ValueError("Spot editing requires native unsigned 8/16-bit samples")
    if len(doc.channels) != doc.samples.shape[-1] or any(c.index != i for i, c in enumerate(doc.channels)):
        raise ValueError("Channel layout does not match the sample planes")
    if any(c.kind not in ("Spot", "Transparency", "Alpha mask") for c in doc.channels[doc.base_count:]):
        raise ValueError("Unknown extra-channel metadata cannot be safely edited")


def _spot(doc: ImageDocument, index: int) -> Channel:
    _validate(doc)
    if not isinstance(index, int) or not 0 <= index < len(doc.channels) or doc.channels[index].kind != "Spot":
        raise ValueError("Choose a spot channel; process, transparency and saved alpha channels are locked")
    return doc.channels[index]


def _name(name: str):
    if not isinstance(name, str) or not name.strip() or "\0" in name:
        raise ValueError("Enter a nonempty spot name without null characters")
    if len(name.encode("utf-16-be")) // 2 > 255:
        raise ValueError("Spot names may contain at most 255 UTF-16 characters")


def _display(doc: ImageDocument, color, solidity) -> DisplayInfo:
    if len(color) != 3 or any(not isinstance(v, int) or not 0 <= v <= 255 for v in color):
        raise ValueError("Spot preview colour must contain three values from 0 to 255")
    if not isinstance(solidity, int) or not 0 <= solidity <= 100:
        raise ValueError("Spot solidity must be from 0 to 100")
    ids = read_resources(doc.photoshop_resources).resource_ids if doc.photoshop_resources else []
    resource_id = 1077 if 1077 in ids else 1007 if 1007 in ids else 1077
    return DisplayInfo(0, tuple(v * 257 for v in color) + (0,), solidity, 2, resource_id)


def _properties(doc, channel, color, solidity):
    color = channel.color if color is None and channel else (255, 255, 255) if color is None else tuple(color)
    solidity = channel.display.opacity if solidity is None and channel and channel.display else 100 if solidity is None else solidity
    # Validate even when retaining the original non-RGB colour components.
    rgb_display = _display(doc, color, solidity)
    if channel and channel.display and tuple(color) == channel.color:
        return tuple(color), replace(channel.display, opacity=solidity)
    return tuple(color), rgb_display


def _extras(doc):
    extras = doc.metadata.get("extra_samples")
    if extras is None:
        extras = [1 if c.associated else 2 if c.kind == "Transparency" else 0
                  for c in doc.channels[doc.base_count:]]
    if len(extras) != len(doc.channels) - doc.base_count or any(v not in (0, 1, 2) for v in extras):
        raise ValueError("TIFF ExtraSamples metadata does not match the channels")
    for c, value in zip(doc.channels[doc.base_count:], extras):
        if (c.kind == "Transparency") != (value in (1, 2)):
            raise ValueError("Conflicting transparency and spot/alpha metadata cannot be edited")
    return list(extras)


def _source_indices(doc):
    indices = doc.metadata.get("channel_source_indices", list(range(len(doc.channels))))
    if len(indices) != len(doc.channels):
        raise ValueError("Channel source identities do not match the layout")
    return list(indices)


def restore_layout(doc: ImageDocument, channels, resources, metadata,
                   sample_order=None, extra_plane=None, warnings=None) -> ImageDocument:
    """Restore small layout metadata and a plane permutation/insertion, for undo.

    ``sample_order`` addresses the current stored samples. Its single ``None``
    entry, when present, is filled by ``extra_plane``. No document is mutated.
    """
    if sample_order is None:
        samples = doc.samples
    else:
        if len(sample_order) != len(channels):
            raise ValueError("Sample order does not match the channel layout")
        samples = np.empty((*doc.samples.shape[:2], len(sample_order)), dtype=doc.samples.dtype)
        for new_index, old_index in enumerate(sample_order):
            if old_index is None:
                if extra_plane is None or extra_plane.shape != doc.samples.shape[:2] or extra_plane.dtype != samples.dtype:
                    raise ValueError("Missing or invalid inserted sample plane")
                samples[..., new_index] = extra_plane
            else:
                samples[..., new_index] = doc.samples[..., old_index]
    return replace(doc, samples=samples, channels=list(channels), photoshop_resources=resources,
                   metadata=dict(metadata), warnings=list(doc.warnings if warnings is None else warnings))


def _changed(doc, channels, sample_order, source_order, logical_sources, extra_plane=None):
    extras = _extras(doc)
    new_extras = [0 if old is None else extras[old - doc.base_count]
                  for old in source_order[doc.base_count:]]
    resources = rewrite_channel_resources(doc.photoshop_resources, doc.channels, channels,
                                         source_order, doc.base_count, extras)
    metadata = dict(doc.metadata)
    metadata["extra_samples"] = new_extras
    metadata["channel_source_indices"] = logical_sources
    metadata["photoshop_resource_ids"] = read_resources(resources).resource_ids if resources else []
    return restore_layout(doc, channels, resources, metadata, sample_order, extra_plane)


def add_spot(doc: ImageDocument, name: str, color=None, solidity=None,
             source_index: int | None = None) -> ImageDocument:
    """Append an empty (white/no-ink) mask, or duplicate another spot's samples."""
    _validate(doc)
    _name(name)
    source = _spot(doc, source_index) if source_index is not None else None
    color, display = _properties(doc, source, color, solidity)
    index = len(doc.channels)
    channels = [*doc.channels, Channel(index, name, "Spot", "TIFview spot channel", tuple(color), display)]
    order = list(range(index)) + [None]
    source_order = list(range(index)) + [source_index]
    sources = _source_indices(doc) + [None]
    plane = doc.samples[..., source_index] if source else np.full(doc.samples.shape[:2], doc.maximum, doc.samples.dtype)
    if (index + 1) * plane.size * plane.dtype.itemsize > 512 * 2**20:
        raise ValueError("Adding a channel would exceed the 512 MiB decoded-image limit")
    return _changed(doc, channels, order, source_order, sources, plane)


def delete_spot(doc: ImageDocument, index: int) -> ImageDocument:
    _spot(doc, index)
    order = [i for i in range(len(doc.channels)) if i != index]
    channels = [replace(doc.channels[old], index=new) for new, old in enumerate(order)]
    sources = _source_indices(doc)
    return _changed(doc, channels, order, order, [sources[i] for i in order])


def move_spot(doc: ImageDocument, index: int, target_sequence: int) -> ImageDocument:
    _spot(doc, index)
    slots = [c.index for c in doc.channels if c.kind == "Spot"]
    if not isinstance(target_sequence, int) or not 1 <= target_sequence <= len(slots):
        raise ValueError("Choose a valid 1-based spot sequence")
    ordered = slots.copy()
    ordered.remove(index)
    ordered.insert(target_sequence - 1, index)
    if ordered == slots:
        return doc
    order = list(range(len(doc.channels)))
    for slot, old in zip(slots, ordered):
        order[slot] = old
    channels = [replace(doc.channels[old], index=new) for new, old in enumerate(order)]
    sources = _source_indices(doc)
    return _changed(doc, channels, order, order, [sources[i] for i in order])


def update_spot(doc: ImageDocument, index: int, name: str, color=None, solidity=None) -> ImageDocument:
    channel = _spot(doc, index)
    _name(name)
    color, display = _properties(doc, channel, color, solidity)
    if name == channel.name and color == channel.color and display == channel.display:
        return doc
    channels = list(doc.channels)
    channels[index] = replace(channel, name=name, color=tuple(color), display=display)
    order = list(range(len(channels)))
    return _changed(doc, channels, None, order, _source_indices(doc))
