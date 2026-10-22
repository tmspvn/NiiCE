"""Loading, saving and output-file naming for NIfTI images."""
from __future__ import annotations

import os
from typing import Optional

import nibabel as nib
import numpy as np

NIFTI_EXTS = (".nii.gz", ".nii")


def is_nifti(path: str) -> bool:
    return path.lower().endswith(NIFTI_EXTS)


def split_ext(path: str) -> tuple[str, str]:
    """Split 'dir/sub.nii.gz' into ('dir/sub', '.nii.gz'); handles the double extension."""
    low = path.lower()
    for ext in NIFTI_EXTS:
        if low.endswith(ext):
            return path[: -len(ext)], path[-len(ext):]
    return os.path.splitext(path)


def stem(path: str) -> str:
    """File name without directory and NIfTI extension."""
    return os.path.basename(split_ext(path)[0])


def unique_path(path: str) -> str:
    """Return `path`, or `path` with _1, _2, ... appended before the extension if it exists."""
    if not os.path.exists(path):
        return path
    base, ext = split_ext(path)
    i = 1
    while os.path.exists(f"{base}_{i}{ext}"):
        i += 1
    return f"{base}_{i}{ext}"


def out_path(src: str, suffix: str, ext: str = "same", outdir: Optional[str] = None,
             unique: bool = True) -> str:
    """Build an output name next to `src`: sub.nii.gz + '_mask' -> sub_mask.nii.gz.

    ext: 'same' keeps the input extension, otherwise '.nii' or '.nii.gz'.
    """
    base, src_ext = split_ext(src)
    if ext == "same":
        ext = src_ext if src_ext in NIFTI_EXTS else ".nii.gz"
    outdir = outdir or os.path.dirname(os.path.abspath(src))
    path = os.path.join(outdir, os.path.basename(base) + suffix + ext)
    return unique_path(path) if unique else path


def load(path: str):
    return nib.load(path)


def load_data(img, dtype=np.float32) -> np.ndarray:
    """Voxel data with scaling applied; NaN/inf are kept so callers can decide what to do."""
    return np.asanyarray(img.get_fdata(dtype=dtype))


def n_volumes(img) -> int:
    return img.shape[3] if len(img.shape) > 3 else 1


def best_dtype(data: np.ndarray):
    """Pick a sensible on-disk type: uint8 for masks, keep ints, float32 otherwise."""
    if data.dtype == bool:
        return np.uint8
    if np.issubdtype(data.dtype, np.integer):
        return data.dtype
    return np.float32


def save_like(data: np.ndarray, ref, path: str, dtype=None) -> str:
    """Save `data` on the grid (affine + header) of the reference image `ref`."""
    data = np.asarray(data)
    dtype = dtype or best_dtype(data)
    if data.dtype == bool:
        data = data.astype(np.uint8)
    header = ref.header.copy()
    cls = nib.Nifti2Image if isinstance(ref, nib.Nifti2Image) else nib.Nifti1Image
    out = cls(data.astype(dtype, copy=False), ref.affine, header=header)
    out.set_data_dtype(dtype)
    # The source scaling (scl_slope/inter) does not apply to the new data.
    out.header.set_slope_inter(None, None)
    if hasattr(ref, "get_qform"):
        out.set_qform(*ref.get_qform(coded=True))
        out.set_sform(*ref.get_sform(coded=True))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    nib.save(out, path)
    return path
