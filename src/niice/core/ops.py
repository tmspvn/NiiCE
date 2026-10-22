"""Image operations in plain numpy/scipy (no FSL needed).

Every function takes input path(s) and an output path and returns the written path(s).
`progress(fraction, message)` callbacks are optional.
"""
from __future__ import annotations

from typing import Callable, Optional, Sequence

import numpy as np
import nibabel as nib
from scipy import ndimage

from .io import load, load_data, n_volumes, save_like

Progress = Optional[Callable[[float, str], None]]


# --------------------------------------------------------------------------- binarise / threshold
def binarise(path: str, out: str, threshold: float = 0.0) -> str:
    """Mask of voxels > threshold (NaN -> 0), saved as uint8. Same as `fslmaths -nan -bin`."""
    img = load(path)
    data = np.nan_to_num(load_data(img), nan=0.0)
    return save_like(data > threshold, img, out, dtype=np.uint8)


def percentiles(path: str, qs=(0, 2, 50, 98, 100), nonzero: bool = True) -> list[float]:
    data = load_data(load(path))
    data = data[np.isfinite(data)]
    if nonzero:
        data = data[data != 0]
    if data.size == 0:
        return [0.0 for _ in qs]
    return [float(v) for v in np.percentile(data, qs)]


def threshold(path: str, out: str, lower: Optional[float] = None, upper: Optional[float] = None,
              binarise: bool = False, percentile: bool = False) -> str:
    """Zero voxels outside [lower, upper] (like fslmaths -thr / -uthr; bounds are inclusive).

    percentile=True interprets the bounds as percentiles (0-100) of the non-zero voxels.
    binarise=True writes a 0/1 mask of the kept, non-zero voxels instead of their values.
    """
    img = load(path)
    data = np.nan_to_num(load_data(img), nan=0.0)
    if percentile:
        nz = data[data != 0]
        if lower is not None:
            lower = float(np.percentile(nz, lower)) if nz.size else 0.0
        if upper is not None:
            upper = float(np.percentile(nz, upper)) if nz.size else 0.0
    keep = np.ones(data.shape, bool)
    if lower is not None:
        keep &= data >= lower
    if upper is not None:
        keep &= data <= upper
    if binarise:
        return save_like(keep & (data != 0), img, out, dtype=np.uint8)
    return save_like(np.where(keep, data, 0).astype(np.float32), img, out)


# --------------------------------------------------------------------------- ROIs and volumes
def labels(path: str, max_labels: int = 5000) -> list[tuple[float, int]]:
    """Non-zero values in a label image with their voxel counts, sorted by value."""
    data = np.asanyarray(load(path).dataobj)
    data = data[np.isfinite(data)] if data.dtype.kind == "f" else data.ravel()
    if data.dtype.kind == "f":
        data = np.round(data, 6)
    values, counts = np.unique(data, return_counts=True)
    keep = values != 0
    values, counts = values[keep], counts[keep]
    if values.size > max_labels:
        raise ValueError(f"{values.size} distinct values: this does not look like a label image")
    return [(float(v), int(c)) for v, c in zip(values, counts)]


def label_mask(data: np.ndarray, value: float) -> np.ndarray:
    # Tolerant equality: labels are integers, but may be stored as float with rounding noise.
    return np.abs(data - value) < 1e-4


def extract_roi(path: str, values: Sequence[float], out: str, keep_values: bool = False) -> str:
    """One mask containing all `values` (binary unless keep_values)."""
    img = load(path)
    data = np.nan_to_num(load_data(img, dtype=np.float64), nan=0.0)
    mask = np.zeros(data.shape, bool)
    for v in values:
        mask |= label_mask(data, v)
    if keep_values:
        return save_like(np.where(mask, data, 0).astype(np.float32), img, out)
    return save_like(mask, img, out, dtype=np.uint8)


def parse_indices(text: str, n: int) -> list[int]:
    """'0, 3, 5-7, -1' -> [0, 3, 5, 6, 7, n-1]. Negative numbers count from the end."""
    result = []
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part[1:]:
            lo, hi = part[0] + part[1:].split("-", 1)[0], part[1:].split("-", 1)[1]
            lo, hi = int(lo), int(hi)
            lo, hi = (lo % n if lo < 0 else lo), (hi % n if hi < 0 else hi)
            if lo > hi:
                lo, hi = hi, lo
            result.extend(range(lo, hi + 1))
        else:
            i = int(part)
            result.append(i % n if i < 0 else i)
    bad = [i for i in result if not 0 <= i < n]
    if bad:
        raise ValueError(f"Volume index {bad[0]} out of range 0-{n - 1}")
    if not result:
        raise ValueError("No volume index given")
    return result


def extract_volumes(path: str, indices: Sequence[int], out: str) -> str:
    """Write the selected volumes (one index -> 3D file, several -> 4D file). Like fslroi."""
    img = load(path)
    nv = n_volumes(img)
    for i in indices:
        if not 0 <= i < nv:
            raise ValueError(f"Volume index {i} out of range 0-{nv - 1}")
    if len(img.shape) < 4:
        return save_like(np.asanyarray(img.dataobj), img, out)
    data = np.stack([np.asanyarray(img.dataobj[..., i]) for i in indices], axis=-1)
    if len(indices) == 1:
        data = data[..., 0]
    return save_like(data, img, out)


# --------------------------------------------------------------------------- subtract
def subtract(path_a: str, path_b: str, out: str) -> str:
    """a - b on the grid of a. Both images must have the same voxel grid."""
    a_img, b_img = load(path_a), load(path_b)
    a, b = load_data(a_img), load_data(b_img)
    if a.shape[:3] != b.shape[:3]:
        raise ValueError(f"Shapes differ: {a.shape} vs {b.shape}. Resample one image first.")
    if a.ndim == 4 and b.ndim == 3:
        b = b[..., None]
    return save_like((a - b).astype(np.float32), a_img, out)


# --------------------------------------------------------------------------- resample
def target_grid(img, mode: str, values=None, ref=None):
    """Compute (new_shape, new_affine) for resampling.

    mode 'spacing': values = new voxel size (mm) per axis.
    mode 'shape':   values = new number of voxels per axis.
    mode 'reference': grid of the image `ref` (a path or nibabel image).

    For 'spacing'/'shape' the field of view is kept: with s = new_size / old_size on an axis,
    new voxel i covers old voxel coordinates s*i + (s-1)/2 (the outer voxel edges coincide).
    """
    shape = np.array(img.shape[:3])
    zooms = np.array(img.header.get_zooms()[:3], float)
    if mode == "reference":
        ref = load(ref) if isinstance(ref, str) else ref
        return tuple(int(s) for s in ref.shape[:3]), ref.affine.copy()
    values = np.array(values, float)
    if mode == "spacing":
        if np.any(values <= 0):
            raise ValueError("Voxel size must be > 0")
        s = values / zooms
        new_shape = np.maximum(1, np.round(shape / s)).astype(int)
    elif mode == "shape":
        new_shape = np.maximum(1, np.round(values)).astype(int)
        s = shape / new_shape
    else:
        raise ValueError(f"Unknown resample mode {mode!r}")
    # Matrix taking new voxel coordinates to old voxel coordinates.
    m = np.eye(4)
    m[:3, :3] = np.diag(s)
    m[:3, 3] = (s - 1) / 2
    return tuple(int(v) for v in new_shape), img.affine @ m


def resample(path: str, out: str, mode: str, values=None, ref=None, order: int = 1,
             progress: Progress = None) -> str:
    """Resample to a new grid. order: 0 nearest neighbour, 1 linear, 3 cubic B-spline."""
    img = load(path)
    new_shape, new_affine = target_grid(img, mode, values, ref)
    # For each output voxel v_new: v_old = inv(A_old) @ A_new @ v_new
    m = np.linalg.inv(img.affine) @ new_affine
    data = np.asanyarray(img.dataobj)
    is_int = np.issubdtype(data.dtype, np.integer) or data.dtype == bool
    work = data.astype(np.float32) if data.dtype.kind != "f" else np.nan_to_num(data.astype(np.float32))
    vols = [work] if work.ndim == 3 else [work[..., i] for i in range(work.shape[3])]
    res = []
    for i, vol in enumerate(vols):
        if progress:
            progress(i / len(vols), f"Volume {i + 1}/{len(vols)}")
        res.append(ndimage.affine_transform(vol, m[:3, :3], offset=m[:3, 3], output_shape=new_shape,
                                            order=order, mode="constant", cval=0.0,
                                            prefilter=order > 1))
    result = res[0] if len(res) == 1 else np.stack(res, axis=-1)
    dtype = data.dtype if (order == 0 and is_int) else np.float32
    nib.save(_with_grid(img, result.astype(dtype), new_affine), out)
    return out


def _with_grid(img, data, affine):
    cls = nib.Nifti2Image if isinstance(img, nib.Nifti2Image) else nib.Nifti1Image
    hdr = img.header.copy()
    new = cls(data, affine, header=hdr)
    new.set_data_dtype(data.dtype)
    new.header.set_slope_inter(None, None)
    codes = (int(img.header["sform_code"]) or 2, int(img.header["qform_code"]) or 2) \
        if "sform_code" in img.header else (2, 2)
    new.set_sform(affine, codes[0])
    new.set_qform(affine, codes[1])
    zooms = list(np.sqrt((affine[:3, :3] ** 2).sum(axis=0)))
    if data.ndim > 3:
        zooms += list(img.header.get_zooms()[3:data.ndim])
    new.header.set_zooms(zooms)
    return new


def describe_grid(shape, affine) -> str:
    zooms = np.sqrt((np.asarray(affine)[:3, :3] ** 2).sum(axis=0))
    return (" x ".join(str(int(s)) for s in shape[:3]) + "  @  "
            + " x ".join(f"{z:.3g}" for z in zooms) + " mm")

