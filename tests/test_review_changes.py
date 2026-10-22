"""Tests for the changes requested in the NiiCE review (IDs refer to NiiCE_review.xlsx)."""
import os
import shutil
import time

import nibabel as nib
import numpy as np
import pytest

from niice import plugins, settings
from niice.core import ants, io, ops, plotting, space


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("NIICE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    plugins.load_all(force=True)


@pytest.fixture
def img(tmp_path):
    data = np.zeros((12, 14, 10), np.float32)
    data[3:9, 4:10, 2:8] = np.arange(6 * 6 * 6, dtype=np.float32).reshape(6, 6, 6) + 1
    p = tmp_path / "sub 01.nii.gz"
    nib.save(nib.Nifti1Image(data, np.diag([2, 2, 2, 1.0])), str(p))
    return str(p)


def pump(app, seconds):
    t = time.time()
    while time.time() - t < seconds:
        app.processEvents()
        time.sleep(0.01)


# OPS-02: spaces and copies such as "x.nii (copy).gz"
def test_odd_names_are_read_and_named_sensibly(img, tmp_path):
    copy = tmp_path / "sub 01.nii (copy).gz"
    shutil.copy(img, copy)
    assert io.is_nifti(str(copy)) and io.looks_like_nifti_name(str(copy))
    assert io.load(str(copy)).shape == (12, 14, 10)
    out = ops.binarise(str(copy), io.out_path(str(copy), "_mask"))
    assert os.path.basename(out) == "sub 01 (copy)_mask.nii.gz"
    fake = tmp_path / "fake.nii.gz"
    fake.write_text("hello")
    with pytest.raises(ValueError, match="not a NIfTI"):
        io.load(str(fake))
    renamed = tmp_path / "noext"
    shutil.copy(img, renamed)
    assert io.is_nifti(str(renamed))  # recognised by content


# REG-13 / REG-18: .niicereg record files (older .details still readable)
def test_niicereg_record(tmp_path):
    pre = str(tmp_path / "NiiCE.coreg.ab12c" / "coreg.ab12c.")
    os.makedirs(os.path.dirname(pre))
    rec = pre[:-1] + ants.RECORD_EXT
    with open(rec, "w") as f:
        f.write(ants.details_text("ab12c", "/d/f.nii.gz", "/d/m.nii.gz", pre, "a", ["x"]))
    assert rec.endswith("coreg.ab12c.niicereg")
    assert ants.parse_details(rec)["prefix"] == pre
    old = pre[:-1] + ".details"
    open(old, "w").write("Registration ID: ab12c\nFixed img: /d/f.nii.gz\n")
    assert ants.parse_details(old)["prefix"] == pre


# PLOT-37: balance slider changes overlay / contour / blend
def test_composite_balance():
    r, m = np.full((4, 4), 0.8), np.full((4, 4), 0.6)
    only_ref = space.composite(r, m, "overlay", alpha=0.0)
    both = space.composite(r, m, "overlay", alpha=0.5)
    only_mov = space.composite(r, m, "overlay", alpha=1.0)
    assert only_ref[0, 0, 1] == 0 and only_ref[0, 0, 0] == pytest.approx(0.8)
    assert both[0, 0, 0] == pytest.approx(0.8) and both[0, 0, 1] == pytest.approx(0.6)
    assert only_mov[0, 0, 0] == 0 and only_mov[0, 0, 1] == pytest.approx(0.6)
    assert space.composite(r, m, "blend", alpha=0.25)[0, 0, 0] == pytest.approx(0.75)


# PLOT-02: preview from a reduced copy
def test_gif_preview_reduced(img):
    full = plotting.render_frames([img], plotting.GifOptions(scale=1, rewind=False, crop=False))
    small = plotting.render_frames([img], plotting.GifOptions(scale=1, rewind=False, crop=False,
                                                             max_voxels=200))
    assert small.shape[1] < full.shape[1] and len(small) < len(full)


# GEN: plugin template is listed after creation
def test_new_plugin_template_loads():
    path = plugins.new_plugin_file(settings.plugins_dir())
    plugins.load_all(force=True)
    t = plugins.get_tool("my.my_plugin")
    assert t is not None and t.menu == "Operations/My plugin" and t.source == "plugin file my_plugin.py"
    assert os.path.basename(plugins.new_plugin_file(settings.plugins_dir())) == "my_plugin_2.py"


# GUI behaviour --------------------------------------------------------------------------------
@pytest.fixture
def qt():
    from niice.gui import common
    return common.app()


def test_extract_roi_ticks_while_typing(qt, tmp_path):
    from niice.gui import ops as gops
    lab = np.zeros((6, 6, 6), np.int16)
    lab[:2], lab[2:4], lab[4:] = 1, 17, 53
    p = str(tmp_path / "atlas.nii.gz")
    nib.save(nib.Nifti1Image(lab, np.eye(4)), p)
    d = gops.ExtractRoiDialog([p])
    from PySide6 import QtWidgets
    edit = [e for e in d.findChildren(QtWidgets.QLineEdit) if "tick" in e.placeholderText().lower()][0]
    edit.setText("17")
    assert d.chosen() == [17.0]
    edit.setText("17, 53")
    assert d.chosen() == [17.0, 53.0]
    edit.setText("53")            # deleting a typed number unticks it again
    assert d.chosen() == [53.0]
    assert not d.panel.run_btn.autoDefault()  # plain Enter does not run


def test_ctrl_enter_runs_and_closes(qt, img):
    from niice.gui import ops as gops
    d = gops.ThresholdDialog([img])
    d.show()
    d.run_and_close()
    for _ in range(300):
        if not d.isVisible():
            break
        pump(qt, 0.02)
    assert not d.isVisible()
    msg, outputs = d.quick_result
    assert msg.startswith("Saved:\n") and outputs[0] in msg and os.path.exists(outputs[0])


def test_quick_binarise_pop_up(qt, img, monkeypatch):
    from niice.gui import common, ops as gops
    monkeypatch.setattr(common, "_popup_seconds", lambda: 0.3)
    assert gops.binarise([img]) == 0
    assert os.path.exists(io.out_path(img, "_mask", unique=False))


def test_quick_subtract_order_and_error(qt, img, tmp_path, monkeypatch):
    from niice.gui import common, ops as gops
    monkeypatch.setattr(common, "_popup_seconds", lambda: 0.3)
    b = str(tmp_path / "b.nii.gz")
    nib.save(nib.Nifti1Image(np.ones((12, 14, 10), np.float32), np.diag([2, 2, 2, 1.0])), b)
    gops.subtract([img, b])
    out = os.path.join(os.path.dirname(img), "difference.nii.gz")
    np.testing.assert_allclose(io.load(out).get_fdata(), io.load(img).get_fdata() - 1)
    # different shapes: the pop-up stays open with an explanation (closed here by the test)
    c = str(tmp_path / "c.nii.gz")
    nib.save(nib.Nifti1Image(np.ones((3, 3, 3), np.float32), np.eye(4)), c)
    seen = {}
    real_failure = common.Toast.failure

    def failure(self, message, details=""):
        seen["msg"] = message
        real_failure(self, message, details)
        common.QtCore.QTimer.singleShot(50, self.close)

    monkeypatch.setattr(common.Toast, "failure", failure)
    gops.subtract([img, c])
    assert "Shapes differ" in seen["msg"] and "resample" in seen["msg"].lower()
