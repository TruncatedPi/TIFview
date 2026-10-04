"""Display-only preview keys and a bounded native-byte cache."""
from collections import OrderedDict

import numpy as np


def preview_key(doc, selected, colored, visible, overlays, opacity, invert, colors):
    if selected is not None:
        if not colored:
            return (selected, False, invert)
        return (selected, True, invert, opacity, colors.get(selected, doc.channels[selected].color))
    relevant = frozenset(i for i in visible if i < doc.base_count or
                         doc.channels[i].kind == "Transparency" or overlays)
    if overlays:
        overlay_colors = tuple((i, colors.get(i, doc.channels[i].color))
                               for i in sorted(relevant) if i >= doc.base_count and
                               doc.channels[i].kind != "Transparency")
        return (None, relevant, True, opacity, overlay_colors)
    return (None, relevant, False)


class PreviewCache:
    def __init__(self, limit_bytes=96 * 2**20):
        self.limit_bytes = limit_bytes
        self.bytes = 0
        self.entries = OrderedDict()

    def clear(self):
        self.entries.clear()
        self.bytes = 0

    def get(self, key):
        if key not in self.entries:
            return None
        self.entries.move_to_end(key)
        return self.entries[key]

    def put(self, key, pixels: np.ndarray):
        previous = self.entries.pop(key, None)
        if previous is not None:
            self.bytes -= previous.nbytes
        if pixels.nbytes > self.limit_bytes:
            return
        while self.entries and self.bytes + pixels.nbytes > self.limit_bytes:
            _, old = self.entries.popitem(last=False)
            self.bytes -= old.nbytes
        pixels.flags.writeable = False
        self.entries[key] = pixels
        self.bytes += pixels.nbytes
