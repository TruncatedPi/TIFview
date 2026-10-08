"""Canvas growth preserves native samples, print scale and copy-save semantics."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import tifffile
from PySide6.QtCore import QRectF

from tifview.canvas import CanvasGeometry
from tifview.editing import EditSession, CanvasPatch
from tifview.layers import LayerStack
from tifview.model import Channel, ImageDocument
from tifview.reader import load_image
from tifview.svg import SvgArtwork, Placement, canvas_fit, job_data, load_job
from tifview.writer import save_tiff_copy, SaveOptions, layer_preservation_reason
from tools.make_demo import photoshop_resources
from test_app_layers import make_source, opened, drain, cleanup
from test_layers import encode_layers, raster
from test_vectors import SVG, shape_layer


def image(tmp_path, depth=8, mode="RGB", orientation=1, alpha=0):
    maximum = 2**depth - 1
    base = {"RGB": 3, "CMYK": 4, "Gray": 1, "WhiteIsZero": 1}[mode]
    count = base + 2 + bool(alpha)
    pixels = (np.arange(9 * 13 * count, dtype=np.uint32).reshape(9, 13, count) * 127 % maximum).astype(f"uint{depth}")
    if alpha:
        pixels[..., -1] = maximum // 2
        if alpha == 1:
            pixels[..., :base] //= 2
    resources = photoshop_resources(["White Ink", "Saved selection"], [2, 0])
    path = tmp_path / "SYNTHETIC-canvas-source.tif"
    tifffile.imwrite(path, pixels, photometric={"RGB":"rgb", "CMYK":"separated", "Gray":"minisblack", "WhiteIsZero":"miniswhite"}[mode],
                     extrasamples=[0, 0] + ([alpha] if alpha else []), metadata=None,
                     resolution=(254, 508),
                     extratags=[(274, 3, 1, orientation, False), (34377, 7, len(resources), resources, False)])
    return load_image(path)


@pytest.mark.parametrize("depth", [8, 16])
@pytest.mark.parametrize("mode", ["RGB", "CMYK", "Gray", "WhiteIsZero"])
@pytest.mark.parametrize("orientation", range(1, 9))
def test_transparent_canvas_save_preserves_every_original_plane_and_orientation(tmp_path, depth, mode, orientation):
    doc = image(tmp_path, depth, mode, orientation)
    original_file = doc.path.read_bytes()
    e = EditSession(doc)
    before = doc.display_samples.copy()
    e.expand_canvas(doc.width + 8, doc.height + 6, 3, 2)
    after = e.document
    np.testing.assert_array_equal(after.display_samples[2:2+doc.height, 3:3+doc.width, :len(doc.channels)], before)
    assert after.display_samples[0, 0, -1] == 0
    assert np.all(after.display_samples[2:2+doc.height, 3:3+doc.width, -1] == doc.maximum)
    spot = next(c.index for c in doc.channels if c.kind == "Spot")
    selection = next(c.index for c in doc.channels if c.kind == "Alpha mask")
    assert after.display_samples[0, 0, spot] == doc.maximum
    assert after.display_samples[0, 0, selection] == 0
    assert after.metadata["dpi"] == doc.metadata["dpi"]
    target = tmp_path / "SYNTHETIC-expanded.tif"
    save_tiff_copy(doc, after, target)
    saved = load_image(target)
    assert saved.orientation == orientation and saved.metadata["dpi"] == doc.metadata["dpi"]
    np.testing.assert_array_equal(saved.samples, after.samples)
    e.undo()
    np.testing.assert_array_equal(e.document.samples, doc.samples)
    assert e.document.canvas is None and e.channel_ids == tuple(range(len(doc.channels)))
    e.redo()
    np.testing.assert_array_equal(e.document.samples, saved.samples)
    assert doc.path.read_bytes() == original_file


@pytest.mark.parametrize("depth", [8, 16])
@pytest.mark.parametrize("alpha", [1, 2])
@pytest.mark.parametrize("mode", ["RGB", "CMYK"])
def test_existing_alpha_polarity_and_association_are_kept(tmp_path, depth, alpha, mode):
    doc = image(tmp_path, depth, mode, alpha=alpha)
    e = EditSession(doc); e.expand_canvas(20, 14, 2, 1)
    assert len(e.document.channels) == len(doc.channels)
    assert e.document.channels[-1].associated == (alpha == 1)
    np.testing.assert_array_equal(e.document.display_samples[1:10, 2:15], doc.display_samples)
    assert e.document.samples[0, 0, -1] == 0
    if alpha == 1:
        assert np.all(e.document.samples[0, 0, :doc.base_count] == 0)
    save_tiff_copy(doc, e.document, tmp_path / "associated-expanded.tif")


def test_white_background_and_repeated_growth_mixed_with_spot_edits_undo_exactly(tmp_path):
    doc = image(tmp_path)
    e = EditSession(doc)
    e.expand_canvas(20, 15, 2, 3, transparent=False)
    assert len(e.document.channels) == len(doc.channels)
    np.testing.assert_array_equal(e.document.samples[0, 0, :3], [255,255,255])
    e.add_spot("Varnish")
    e.apply(3, (0,0,2,2), np.full((2,2),255,np.uint8), 0)
    e.expand_canvas(25, 20, 1, 1)
    final = e.document.samples.copy()
    for _ in range(4): e.undo()
    np.testing.assert_array_equal(e.document.samples, doc.samples)
    for _ in range(4): e.redo()
    np.testing.assert_array_equal(e.document.samples, final)


def test_canvas_history_does_not_store_full_image_snapshots():
    samples = np.full((1800,1800,4),255,np.uint8)
    channels = [Channel(i,n,"Process","RGB",(0,0,0)) for i,n in enumerate(("Red","Green","Blue"))]
    channels.append(Channel(3,"Transparency","Transparency","TIFF",(0,0,0)))
    doc = ImageDocument(Path("synthetic.tif"),samples,channels,"RGB",3,8,metadata={"extra_samples":[2]})
    e = EditSession(doc,history_limit=4096)
    e.expand_canvas(1900,1900,50,50)
    assert isinstance(e.history[-1],CanvasPatch) and e.history[-1].bytes < 4096
    e.undo(); np.testing.assert_array_equal(e.document.samples, samples)
    e.redo(); assert e.document.width == 1900


@pytest.mark.parametrize("offset", [(0,0),(4,3)])
def test_canvas_growth_retains_raster_layers_and_independent_native_composite(tmp_path, offset):
    doc = load_image(make_source(tmp_path))
    stack = LayerStack.from_document(doc)
    e = EditSession(doc);e.attach_layers(stack)
    e.expand_canvas(26,20,*offset)
    expanded = e.document
    assert expanded.canvas.layers_preserved and layer_preservation_reason(doc,expanded) is None
    save_tiff_copy(doc,expanded,tmp_path / "SYNTHETIC-raster-expanded.tif")
    saved = load_image(tmp_path / "SYNTHETIC-raster-expanded.tif")
    after = LayerStack.from_document(saved)
    for old, new in zip(stack.layers,after.layers):
        assert new.bounds == (old.bounds[0]+offset[1],old.bounds[1]+offset[0],old.bounds[2]+offset[1],old.bounds[3]+offset[0])
        assert [stack.data[a:b] for a,b in old._channel_spans] == [after.data[a:b] for a,b in new._channel_spans]
    e.set_layer_visibility(1,False)
    e.undo();e.undo();np.testing.assert_array_equal(e.document.samples,doc.samples)
    e.redo();np.testing.assert_array_equal(e.document.samples,expanded.samples)


def test_vector_dependent_canvas_growth_requires_explicit_merged_copy(tmp_path):
    pixels = np.full((20,20,3),80,np.uint8)
    data = encode_layers([raster(pixels),shape_layer()])
    path = tmp_path / "SYNTHETIC-vector.tif"
    tifffile.imwrite(path,pixels,photometric="rgb",metadata=None,extratags=[(37724,7,len(data),data,False)])
    doc = load_image(path);e=EditSession(doc);e.expand_canvas(24,24)
    assert "Vector" in layer_preservation_reason(doc,e.document)
    with pytest.raises(ValueError,match="layers unchecked"):
        save_tiff_copy(doc,e.document,tmp_path / "rejected.tif")
    save_tiff_copy(doc,e.document,tmp_path / "merged.tif",SaveOptions(keep_layers=False))
    assert not load_image(tmp_path / "merged.tif").metadata["has_photoshop_layers"]


def test_writer_rejects_unattested_resize_and_bad_canvas_bounds(tmp_path):
    doc=image(tmp_path)
    forged=replace(doc,samples=np.zeros((20,20,len(doc.channels)),np.uint8))
    with pytest.raises(ValueError,match="dimensions"):
        save_tiff_copy(doc,forged,tmp_path / "forged.tif")
    forged.canvas=CanvasGeometry((doc.width,doc.height),(20,20),(100,0))
    with pytest.raises(ValueError,match="geometry"):
        save_tiff_copy(doc,forged,tmp_path / "forged.tif")


def test_svg_overflow_canvas_fit_preserves_physical_size_and_negative_stroke(tmp_path):
    doc=image(tmp_path)
    svg=SvgArtwork.from_bytes(b'<svg xmlns="http://www.w3.org/2000/svg" width="2mm" height="2mm" viewBox="0 0 20 20"><rect width="20" height="20" fill="none" stroke="red" stroke-width="1"/></svg>')
    size=canvas_fit(doc,svg,Placement(width_mm=2,height_mm=2))
    assert size == (22,42,1,1)
    assert svg.width_mm == 2 and svg.full_rect(2,2).left() == pytest.approx(-.05)
    e=EditSession(doc);e.expand_canvas(*size)
    with pytest.raises(ValueError,match="Save the expanded TIFF"):
        job_data(e.document,svg,Placement(width_mm=2,height_mm=2))


def test_svg_import_dialog_defaults_to_transparent_and_handles_expand_keep_cancel(tmp_path,monkeypatch):
    from tifview.app import CanvasFitDialog
    app,w=opened(tmp_path)
    svg=SvgArtwork.from_bytes(SVG)
    try:
        dialog=CanvasFitDialog(w.doc,canvas_fit(w.doc,svg,Placement(width_mm=50,height_mm=25)),w)
        assert dialog.background.currentText()=="Transparent"
        dialog.deleteLater()
        monkeypatch.setattr(CanvasFitDialog,"exec",lambda self:2)
        w.offer_svg_canvas(svg); assert w.doc.width==16 and w.view.svg_item is not None
        w.remove_svg()
        monkeypatch.setattr(CanvasFitDialog,"exec",lambda self:0)
        w.offer_svg_canvas(svg); assert w.view.svg_item is None
        monkeypatch.setattr(CanvasFitDialog,"exec",lambda self:1)
        w.offer_svg_canvas(svg);drain(app,w)
        assert w.doc.width>16 and w.doc.height>12
        assert w.doc.channels[-1].kind=="Transparency" and w.doc.display_samples[-1,-1,-1]==0
        assert w.undo_action.isEnabled()
        w.undo();drain(app,w)
        assert (w.doc.width,w.doc.height)==(16,12)
        w.redo();drain(app,w);assert w.doc.width>16
    finally:
        w.vectors_panel.mark_saved();cleanup(app,w)


def test_layer_generated_alpha_survives_canvas_change_requiring_merged_export(tmp_path):
    pixels=np.full((20,20,3),80,np.uint8)
    data=encode_layers([raster(pixels),shape_layer()])
    path=tmp_path / "SYNTHETIC-layer-alpha.tif"
    tifffile.imwrite(path,pixels,photometric="rgb",metadata=None,extratags=[(37724,7,len(data),data,False)])
    doc=load_image(path);e=EditSession(doc);e.attach_layers(LayerStack.from_document(doc))
    e.set_layer_visibility(0,False)
    assert e.document.metadata["layer_generated_transparency"]
    e.expand_canvas(24,24)
    save_tiff_copy(doc,e.document,tmp_path / "merged-expanded.tif",SaveOptions(keep_layers=False))
    np.testing.assert_array_equal(load_image(tmp_path / "merged-expanded.tif").samples,e.document.samples)


def test_canvas_copy_updates_xmp_attributes_and_elements_without_changing_other_properties(tmp_path):
    from xml.dom import minidom
    raw=b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description xmlns:tiff="http://ns.adobe.com/tiff/1.0/" xmlns:exif="http://ns.adobe.com/exif/1.0/" xmlns:custom="urn:synthetic" tiff:ImageWidth="13" exif:PixelYDimension="9" custom:label="keep me"><tiff:ImageLength>9</tiff:ImageLength><exif:PixelXDimension>13</exif:PixelXDimension><custom:ImageWidth>leave unchanged</custom:ImageWidth></rdf:Description></rdf:RDF></x:xmpmeta>'
    p=tmp_path / "SYNTHETIC-xmp.tif"
    tifffile.imwrite(p,np.zeros((9,13,3),np.uint8),photometric="rgb",metadata=None,
                     extratags=[(274,3,1,6,False),(700,1,len(raw),raw,False)])
    doc=load_image(p);e=EditSession(doc);e.expand_canvas(20,25,2,3)
    target=tmp_path / "expanded-xmp.tif";save_tiff_copy(doc,e.document,target)
    with tifffile.TiffFile(target) as f:
        dom=minidom.parseString(f.pages[0].tags[700].value)
        desc=dom.getElementsByTagName('rdf:Description')[0]
        assert desc.getAttribute('tiff:ImageWidth')=='25'
        assert desc.getAttribute('exif:PixelYDimension')=='20'
        assert dom.getElementsByTagName('tiff:ImageLength')[0].firstChild.nodeValue=='20'
        assert dom.getElementsByTagName('exif:PixelXDimension')[0].firstChild.nodeValue=='25'
        assert desc.getAttribute('custom:label')=='keep me'
        assert dom.getElementsByTagName('custom:ImageWidth')[0].firstChild.nodeValue=='leave unchanged'
    with tifffile.TiffFile(p) as f:
        assert f.pages[0].tags[700].value==raw
