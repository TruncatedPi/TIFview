import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from tifview.reader import load_image
from tools import make_demo as demo


@pytest.mark.parametrize("existing", [False, True])
def test_generated_fixture_loads_named_channel_pixels(tmp_path, existing):
    target = tmp_path / "nested" / "SYNTHETIC-demo.tif"
    if existing:
        target.parent.mkdir()
        target.write_bytes(b"previous fixture")

    demo.make_demo(target)

    document = load_image(target)
    assert document.samples.shape == (500, 720, 6)
    assert document.samples.dtype == np.uint8
    assert [channel.name for channel in document.channels] == [
        "Red", "Green", "Blue", "White Ink", "Varnish", "Saved selection",
    ]
    assert [channel.kind for channel in document.channels] == [
        "Process", "Process", "Process", "Spot", "Spot", "Alpha mask",
    ]
    np.testing.assert_array_equal(document.samples[0, 0], [0, 0, 160, 255, 255, 255])
    assert document.samples[100, 150, 3] == 0
    assert document.samples[240, 480, 4] == 0
    assert document.samples[200, 650, 5] == 100
    assert list(target.parent.iterdir()) == [target]


@pytest.mark.parametrize("existing", [False, True])
def test_failed_write_leaves_no_partial_fixture_and_preserves_existing(tmp_path, monkeypatch, existing):
    target = tmp_path / "SYNTHETIC-demo.tif"
    original = b"previous valid fixture contents"
    if existing:
        target.write_bytes(original)

    def fail_after_header(path, *args, **kwargs):
        temporary = Path(path)
        assert temporary != target
        assert temporary.parent == target.parent
        temporary.write_bytes(b"II*\0\0\0\0\0")
        raise RuntimeError("synthetic codec failure")

    monkeypatch.setattr(demo.tifffile, "imwrite", fail_after_header)
    with pytest.raises(RuntimeError, match="synthetic codec failure"):
        demo.make_demo(target)

    if existing:
        assert target.read_bytes() == original
        assert list(tmp_path.iterdir()) == [target]
    else:
        assert not target.exists()
        assert list(tmp_path.iterdir()) == []


def test_failed_publish_preserves_existing_and_cleans_temporary_file(tmp_path, monkeypatch):
    target = tmp_path / "SYNTHETIC-demo.tif"
    original = b"previous valid fixture contents"
    target.write_bytes(original)

    def fail_replace(source, destination):
        assert Path(source).stat().st_size > 8
        assert destination == target
        raise PermissionError("destination is open")

    monkeypatch.setattr(demo.os, "replace", fail_replace)
    with pytest.raises(PermissionError, match="destination is open"):
        demo.make_demo(target)

    assert target.read_bytes() == original
    assert list(tmp_path.iterdir()) == [target]


def test_cli_refuses_existing_target(tmp_path):
    target = tmp_path / "SYNTHETIC-demo.tif"
    original = b"previous fixture"
    target.write_bytes(original)

    result = subprocess.run(
        [sys.executable, "-m", "tools.make_demo", str(target)],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
    )

    assert result.returncode == 2
    assert "Refusing to overwrite" in result.stderr
    assert target.read_bytes() == original
