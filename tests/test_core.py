import os
import stat
import sys

import nibabel as nib
import numpy as np
import pytest

from niice.core import ants, info, io, mathexpr, ops, plotting, synthstrip


def make(path, data, zooms=(1.0, 1.0, 1.0), affine=None):
    aff = np.diag(list(zooms) + [1.0]) if affine is None else affine
    img = nib.Nifti1Image(np.asarray(data), aff)
    nib.save(img, str(path))
    return str(path)


@pytest.fixture
def t1(tmp_path):
    rng = np.random.default_rng(0)
    data = np.zeros((10, 12, 8), np.float32)
    data[2:8, 3:9, 2:6] = rng.uniform(50, 150, (6, 6, 4))
    return make(tmp_path / "t1.nii.gz", data, zooms=(1.0, 1.0, 2.0))


@pytest.fixture
def labels(tmp_path):
    data = np.zeros((6, 6, 6), np.int16)
    data[:3] = 1
    data[3:, :3] = 17
    return make(tmp_path / "atlas.nii", data)


# --------------------------------------------------------------------------- io
def test_split_ext_and_out_path(tmp_path):
    assert io.split_ext("a/b.nii.gz") == ("a/b", ".nii.gz")
    assert io.split_ext("a/b.nii") == ("a/b", ".nii")
    src = make(tmp_path / "x.nii.gz", np.zeros((2, 2, 2)))
    p = io.out_path(src, "_mask")
    assert p.endswith("x_mask.nii.gz")
    open(p, "w").close()
    assert io.out_path(src, "_mask").endswith("x_mask_1.nii.gz")
    assert io.out_path(src, "_m", ext=".nii").endswith("x_m.nii")


# --------------------------------------------------------------------------- info
def test_summary_and_compare(t1, tmp_path):
    s = info.summary(t1)
    assert s["Dimensions"] == "10 x 12 x 8"
    assert s["Voxel size"] == "1 x 1 x 2"
    st = info.intensity_stats(t1)
    assert float(st["Max"]) <= 150
    other = make(tmp_path / "o.nii.gz", np.zeros((10, 12, 8), np.float32), zooms=(1, 1, 1))
    rows, differs = info.compare([t1, other])
    d = dict(zip([r[0] for r in rows], differs))
    assert d["Voxel size"] is True
    assert d["Dimensions"] is False
    assert d["hdr.pixdim"] is True
    assert "differ" in info.text_report([t1, other])


# --------------------------------------------------------------------------- math
def test_math_validate():
    assert mathexpr.validate("a + b * 2", 2) == ["a", "b"]
    assert mathexpr.validate("np.where(a > 0, a, nan)", 1) == ["a"]
    for bad in ("c + a", "__import__('os')", "a.__class__", "lambda: 1", "a.tofile('x')", "open('x')", "1+1"):
        with pytest.raises(mathexpr.ExprError):
            mathexpr.validate(bad, 2)


def test_math_run(t1, tmp_path):
    out = str(tmp_path / "r.nii.gz")
    kind, path = mathexpr.run("a * 2 + (a > 0)", [t1], out)
    assert kind == "image"
    a = nib.load(t1).get_fdata()
    np.testing.assert_allclose(nib.load(path).get_fdata(), a * 2 + (a > 0), rtol=1e-5)
    kind, val = mathexpr.run("np.mean(a[a > 0])", [t1], out)
    assert kind == "value" and val == pytest.approx(a[a > 0].mean(), rel=1e-5)
    with pytest.raises(mathexpr.ExprError):
        mathexpr.run("a[0]", [t1], out)  # wrong grid


# --------------------------------------------------------------------------- ops
def test_binarise_threshold(t1, tmp_path):
    out = ops.binarise(t1, str(tmp_path / "m.nii.gz"))
    m = nib.load(out)
    assert m.get_data_dtype() == np.uint8
    a = nib.load(t1).get_fdata()
    assert np.array_equal(m.get_fdata() > 0, a > 0)
    np.testing.assert_allclose(m.affine, nib.load(t1).affine)

    out = ops.threshold(t1, str(tmp_path / "t.nii.gz"), lower=100)
    t = nib.load(out).get_fdata()
    assert np.all((t == 0) | (t >= 100)) and np.array_equal(t > 0, a >= 100)
    out = ops.threshold(t1, str(tmp_path / "t2.nii.gz"), upper=100, binarise=True)
    t = nib.load(out).get_fdata()
    assert np.array_equal(t > 0, (a <= 100) & (a != 0))
    out = ops.threshold(t1, str(tmp_path / "t3.nii.gz"), lower=50, percentile=True)
    t = nib.load(out).get_fdata()
    assert abs((t > 0).sum() - (a > 0).sum() / 2) <= 1


def test_labels_and_extract_roi(labels, tmp_path):
    assert ops.labels(labels) == [(1.0, 108), (17.0, 54)]
    out = ops.extract_roi(labels, [17], str(tmp_path / "r.nii.gz"))
    assert nib.load(out).get_fdata().sum() == 54
    out = ops.extract_roi(labels, [1, 17], str(tmp_path / "r2.nii.gz"), keep_values=True)
    assert set(np.unique(nib.load(out).get_fdata())) == {0, 1, 17}


def test_extract_volumes(tmp_path):
    data = np.arange(2 * 2 * 2 * 5, dtype=np.float32).reshape(2, 2, 2, 5)
    p = make(tmp_path / "f.nii.gz", data)
    assert ops.parse_indices("0, 2-3, -1", 5) == [0, 2, 3, 4]
    with pytest.raises(ValueError):
        ops.parse_indices("7", 5)
    out = ops.extract_volumes(p, [3], str(tmp_path / "v.nii.gz"))
    np.testing.assert_array_equal(nib.load(out).get_fdata(), data[..., 3])
    out = ops.extract_volumes(p, [1, 4], str(tmp_path / "v2.nii.gz"))
    np.testing.assert_array_equal(nib.load(out).get_fdata(), data[..., [1, 4]])


def test_subtract(t1, tmp_path):
    out = ops.subtract(t1, t1, str(tmp_path / "d.nii.gz"))
    assert np.all(nib.load(out).get_fdata() == 0)


def test_resample_spacing_keeps_field_of_view(t1, tmp_path):
    out = ops.resample(t1, str(tmp_path / "r.nii.gz"), "spacing", [2, 2, 2], order=1)
    img = nib.load(out)
    assert img.shape == (5, 6, 8)
    np.testing.assert_allclose(img.header.get_zooms(), (2, 2, 2))
    # Outer edge of the first voxel stays in place: centre moves by (new - old) / 2
    old = nib.load(t1).affine
    np.testing.assert_allclose(img.affine[:3, 3], old[:3, 3] + [0.5, 0.5, 0.0])
    # mean intensity roughly preserved
    a = nib.load(t1).get_fdata()
    assert abs(img.get_fdata().mean() - a.mean()) / a.mean() < 0.2


def test_resample_identity_and_reference(t1, labels, tmp_path):
    out = ops.resample(t1, str(tmp_path / "same.nii.gz"), "reference", ref=t1, order=1)
    np.testing.assert_allclose(nib.load(out).get_fdata(), nib.load(t1).get_fdata(), atol=1e-4)
    out = ops.resample(labels, str(tmp_path / "lab.nii.gz"), "shape", [12, 12, 12], order=0)
    img = nib.load(out)
    assert img.get_data_dtype() == np.int16
    assert set(np.unique(img.get_fdata())) <= {0, 1, 17}


# --------------------------------------------------------------------------- plotting
def test_gif(t1, tmp_path):
    o = plotting.GifOptions(view="axial", scale=1, rewind=True)
    frames = plotting.render_frames([t1], o)
    assert frames.dtype == np.uint8 and frames.ndim == 3
    o2 = plotting.GifOptions(view="coronal", cmap="inferno", crosshair=[5, 6, 4])
    frames2 = plotting.render_frames([t1, t1], o2)
    assert frames2.ndim == 4 and frames2.shape[-1] == 3
    out = plotting.make_gif([t1], str(tmp_path / "a.gif"), o)
    from PIL import Image
    im = Image.open(out)
    assert im.n_frames == len(frames)


def test_gif_4d(tmp_path):
    data = np.random.default_rng(1).uniform(1, 2, (6, 7, 5, 9)).astype(np.float32)
    p = make(tmp_path / "bold.nii.gz", data)
    frames = plotting.render_frames([p], plotting.GifOptions(rewind=False, scale=1))
    assert len(frames) == 9


def test_histogram_values(t1, tmp_path):
    v = plotting.histogram_values(t1)
    assert v.size == 6 * 6 * 4
    mask = make(tmp_path / "mask.nii.gz", (nib.load(t1).get_fdata() > 100).astype(np.uint8))
    vm = plotting.histogram_values(t1, mask)
    assert np.all(vm > 100)
    assert plotting.describe(v)["N"] == v.size


# --------------------------------------------------------------------------- synthstrip
def test_synthstrip_command(tmp_path):
    inp, out = str(tmp_path / "a b" / "t1.nii.gz"), str(tmp_path / "out" / "brain.nii.gz")
    cmd = synthstrip.build_command("docker", "docker", inp, out, mask=str(tmp_path / "out" / "m.nii.gz"),
                                   no_csf=True)
    assert cmd[:3] == ["docker", "run", "--rm"]
    assert f"{tmp_path / 'a b'}:/niice/0" in cmd and f"{tmp_path / 'out'}:/niice/1" in cmd
    assert cmd[cmd.index("-i") + 1] == "/niice/0/t1.nii.gz"
    assert cmd[cmd.index("-m") + 1] == "/niice/1/m.nii.gz"
    assert "--no-csf" in cmd
    native = synthstrip.build_command("native", "/x/mri_synthstrip", inp, out)
    assert native[:3] == ["/x/mri_synthstrip", "-i", inp]


# --------------------------------------------------------------------------- ants
FAKE_LOG = """ antsRegistration call:
--------------------------------------------------------------------------------------
antsRegistration --verbose 1 --dimensionality 3 --float 0 --collapse-output-transforms 1 --output [ /o/coreg.x.,/o/coreg.x.Warped.nii.gz,/o/coreg.x.InverseWarped.nii.gz ] --interpolation Linear --use-histogram-matching 0 --winsorize-image-intensities [ 0.005,0.995 ] --initial-moving-transform [ f.nii.gz,m.nii.gz,1 ] --transform Rigid[ 0.1 ] --metric MI[ f.nii.gz,m.nii.gz,1,32,Regular,0.25 ] --convergence [ 1000x500x250x0,1e-6,10 ] --shrink-factors 8x4x2x1 --smoothing-sigmas 3x2x1x0vox --transform Affine[ 0.1 ] --metric MI[ f.nii.gz,m.nii.gz,1,32,Regular,0.25 ] --convergence [ 1000x500x250x0,1e-6,10 ] --shrink-factors 8x4x2x1 --smoothing-sigmas 3x2x1x0vox --transform SyN[ 0.1,3,0 ] --metric MI[ f.nii.gz,m.nii.gz,1,32] --convergence [ 100x70x50x0,1e-6,10 ] --shrink-factors 8x4x2x1 --smoothing-sigmas 3x2x1x0vox
--------------------------------------------------------------------------------------
All_Command_lines_OK
*** Running Euler3DTransform registration ***
  Current level = 1 of 4
    number of iterations = 1000
    shrink factors = 8 8 8
DIAGNOSTIC,Iteration,metricValue,convergenceValue,ITERATION_TIME_INDEX,SINCE_LAST
 2DIAGNOSTIC,     1, -1.0e-01, inf, 1.0e-01, 1.0e-01,
 2DIAGNOSTIC,    20, -2.0e-01, 1.0e-03, 2.0e-01, 1.0e-02,
  Current level = 2 of 4
    number of iterations = 500
 2DIAGNOSTIC,     5, -2.0e-01, 1.0e-03, 2.0e-01, 1.0e-02,
*** Running AffineTransform registration ***
  Current level = 1 of 4
    number of iterations = 1000
 2DIAGNOSTIC,     1, -1.0e-01, inf, 1.0e-01, 1.0e-01,
*** Running SyN registration (varianceForUpdateField = 3.0000e+00, varianceForTotalField = 0.0000e+00) ***
  Current level = 1 of 4
    number of iterations = 100
XXDIAGNOSTIC,Iteration,metricValue,convergenceValue,ITERATION_TIME_INDEX,SINCE_LAST
 1DIAGNOSTIC,     1, -3.0e-01, inf, 1.0e-01, 1.0e-01,
  Current level = 3 of 4
    number of iterations = 50
 1DIAGNOSTIC,    25, -3.0e-01, inf, 1.0e-01, 1.0e-01,
  Current level = 4 of 4
    number of iterations = 0
"""


def test_progress_parser():
    p = ants.RegistrationProgress()
    history = []
    for line in FAKE_LOG.splitlines():
        p.feed(line)
        history.append(p.fraction)
    assert [s.name for s in p.stages] == ["Rigid", "Affine", "SyN"]
    assert p.stages[2].iterations == [100, 70, 50, 0]
    assert history == sorted(history)          # never goes backwards
    assert 0.5 < p.fraction < 1.0              # SyN dominates the cost, last level reached
    assert p.status.startswith("Stage 3/3: SyN - level 4/4")


def test_transform_chain_and_details(tmp_path):
    pre = "/o/coreg.ab12c."
    assert ants.transform_chain(pre, "s") == [(pre + "1Warp.nii.gz", False), (pre + "0GenericAffine.mat", False)]
    assert ants.transform_chain(pre, "s", inverse=True) == [(pre + "0GenericAffine.mat", True),
                                                             (pre + "1InverseWarp.nii.gz", False)]
    assert ants.transform_chain(pre, "a", inverse=True) == [(pre + "0GenericAffine.mat", True)]
    cmd = ants.apply_command("m.nii.gz", "f.nii.gz", "o.nii.gz", ants.transform_chain(pre, "s", True))
    assert cmd[-4:] == ["-t", f"[{pre}0GenericAffine.mat,1]", "-t", pre + "1InverseWarp.nii.gz"]
    text = ants.details_text("ab12c", "/d/f.nii.gz", "/d/m.nii.gz", pre, "s",
                             ants.registration_command("/d/f.nii.gz", "/d/m.nii.gz", pre))
    path = tmp_path / "coreg.ab12c.details"
    path.write_text(text)
    d = ants.parse_details(str(path))
    assert d == {"id": "ab12c", "fixed": "/d/f.nii.gz", "moving": "/d/m.nii.gz", "prefix": pre, "transform": "s"}


@pytest.mark.skipif(sys.platform.startswith("win"), reason="shell script")
def test_ants_streaming_with_fake_script(tmp_path, monkeypatch):
    """Run a fake antsRegistrationSyNQuick.sh that replays a log, through the real runner."""
    from niice.core.proc import run_streaming
    log = tmp_path / "log.txt"
    log.write_text(FAKE_LOG)
    script = tmp_path / "antsRegistrationSyNQuick.sh"
    script.write_text(f"#!/bin/sh\ncat '{log}'\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    cmd = ants.registration_command("f.nii.gz", "m.nii.gz", str(tmp_path / "p."))
    assert cmd[0] == str(script)
    p = ants.RegistrationProgress()
    assert run_streaming(cmd, p.feed) == 0
    assert p.stage == 2


# --------------------------------------------------------------------------- spaces / alignment
from niice.core import space


def test_space_compare_levels(t1, tmp_path):
    a = nib.load(t1)
    data = a.get_fdata()
    assert space.compare(a, a).level == "same"
    # same world space, half resolution -> 'grid'
    half = ops.resample(t1, str(tmp_path / "half.nii.gz"), "spacing", [2, 2, 4])
    r = space.compare(a, nib.load(half))
    assert r.level == "grid" and any("Voxel size" in i for i in r.issues) and r.overlap > 0.95
    # same shape, shifted affine -> warning (the voxel-wise math trap)
    aff = a.affine.copy()
    aff[:3, 3] += [3, 0, 0]
    r = space.compare(a, nib.Nifti1Image(data, aff))
    assert r.level == "warning" and "Same dimensions" in r.summary
    # far away -> no overlap
    aff[:3, 3] += [500, 0, 0]
    r = space.compare(a, nib.Nifti1Image(data, aff))
    assert r.level == "warning" and r.overlap == 0
    # rotated grid is reported
    rot = np.eye(4)
    th = np.radians(10)
    rot[:2, :2] = [[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]]
    r = space.compare(a, nib.Nifti1Image(data, rot @ a.affine))
    assert any("rotated by 10.0" in i for i in r.issues)
    # flipped axis order (LAS vs RAS) is not reported as a rotation
    flip = a.affine @ np.array([[-1, 0, 0, 9], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])
    r = space.compare(a, nib.Nifti1Image(data[::-1], flip))
    assert any("ordered differently" in i for i in r.issues) and not any("rotated" in i for i in r.issues)
    assert r.level == "warning"  # same shape, different affine


def test_qform_sform_issue(t1):
    img = nib.load(t1)
    img.set_qform(img.affine, 1)
    img.set_sform(img.affine, 1)
    assert space.qform_sform_issue(img) is None
    shifted = img.affine.copy()
    shifted[0, 3] += 5
    img.set_sform(shifted, 1)
    assert "disagree" in space.qform_sform_issue(img)


def test_resample_pair_world_and_voxel(t1, tmp_path):
    a = nib.load(t1)
    aff = a.affine.copy()
    aff[:3, 3] += [4, 0, 0]  # shift by 4 mm along x
    moved = make(tmp_path / "moved.nii.gz", a.get_fdata().astype(np.float32), affine=aff)
    world = space.resample_pair(t1, moved, by="world")
    voxel = space.resample_pair(t1, moved, by="voxel")
    # in voxel mode the arrays coincide exactly, in world mode they are 4 voxels apart
    assert np.allclose(voxel["ref"], voxel["mov"], atol=1e-5)
    assert not np.allclose(world["ref"], world["mov"], atol=1e-2)
    # the union grid is wider than the reference by the shift
    assert world["ref"].shape[0] == a.shape[0] + 4
    rgb = space.composite(space.plane(world["ref"], 2, 4), space.plane(world["mov"], 2, 4), "checker",
                          ref_fov=space.plane(world["ref_fov"], 2, 4))
    assert rgb.shape[-1] == 3 and rgb.max() <= 1


def test_preview_guard(t1):
    assert io.preview_skip_reason(t1, 64) is None
    assert "preview skipped" in io.preview_skip_reason(t1, 0.0001)
    assert io.preview_skip_reason(t1, 0) is None  # 0 = no limit
    data, aff = io.load_strided(t1, max_dim=4)
    assert max(data.shape) <= 4 and aff[0, 0] == 3  # every 3rd voxel of 10 -> 4 voxels


def test_text_report_mentions_space(t1, tmp_path):
    aff = nib.load(t1).affine.copy()
    aff[:3, 3] += 50
    other = make(tmp_path / "o2.nii.gz", np.zeros(nib.load(t1).shape, np.float32), affine=aff)
    assert "[WARN]" in info.text_report([t1, other])
