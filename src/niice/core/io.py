"""Loading, saving and output-file naming for NIfTI images."""
from __future__ import annotations

import os
from typing import Optional

import nibabel as nib
import numpy as np

NIFTI_EXTS = (".nii.gz", ".nii")


def looks_like_nifti_name(path: str) -> bool:
    """By name only: .nii, .nii.gz, and copies such as 'scan.nii (copy).gz' or 'scan (1).nii.gz'."""
    low = os.path.basename(path).lower()
    return low.endswith(NIFTI_EXTS) or (".nii" in low and low.endswith(".gz"))


def sniff_nifti(path: str) -> bool:
    """True if the file content is a NIfTI-1/2 image (gzip-compressed or not), whatever its name."""
    import gzip
    import struct
    try:
        with open(path, "rb") as fh:
            head = fh.read(2)
        opener = gzip.open if head == b"\x1f\x8b" else open
        with opener(path, "rb") as fh:
            hdr = fh.read(540)
    except (OSError, EOFError):
        return False
    if len(hdr) < 348:
        return False
    for endian in "<>":
        size = struct.unpack(endian + "i", hdr[:4])[0]
        if size == 348 and hdr[344:347] in (b"n+1", b"ni1"):
            return True
        if size == 540 and hdr[4:7] in (b"n+2", b"ni2"):
            return True
    return False


def is_nifti(path: str) -> bool:
    if path.lower().endswith(NIFTI_EXTS):
        return True
    return os.path.isfile(path) and sniff_nifti(path)


def split_ext(path: str) -> tuple[str, str]:
    """Split 'dir/sub.nii.gz' into ('dir/sub', '.nii.gz'); handles the double extension.

    Odd copies are normalised: 'scan.nii (copy).gz' -> ('scan (copy)', '.nii.gz').
    """
    low = path.lower()
    for ext in NIFTI_EXTS:
        if low.endswith(ext):
            return path[: -len(ext)], path[-len(ext):]
    if low.endswith(".gz") and ".nii" in os.path.basename(low):
        base = path[:-3]
        i = base.lower().rfind(".nii")
        return base[:i] + base[i + 4:], ".nii.gz"
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
    """nibabel image; also reads NIfTI files whose name nibabel cannot interpret."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"No such file: {path}")
    try:
        return nib.load(path)
    except (nib.filebasedimages.ImageFileError, OSError, EOFError, ValueError):
        if not sniff_nifti(path):
            raise ValueError(f"{os.path.basename(path)} is not a NIfTI image (or it is damaged)") from None
    # Unusual name (e.g. 'scan.nii (copy).gz'): read the bytes and build the image directly
    import gzip
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    cls = nib.Nifti2Image if raw[:4] in (b"\x1c\x02\x00\x00", b"\x00\x00\x02\x1c") else nib.Nifti1Image
    return cls.from_bytes(raw)


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


# --------------------------------------------------------------------------- preview safety
DEFAULT_PREVIEW_MVOX = 64  # million voxels; ~400 x 400 x 400


def voxel_counts(path_or_img) -> tuple[int, int]:
    """(voxels in one 3D volume, voxels in the whole file), from the header only."""
    img = load(path_or_img) if isinstance(path_or_img, str) else path_or_img
    shape = img.shape
    per_volume = int(np.prod(shape[:3]))
    return per_volume, int(np.prod(shape)) if len(shape) else 0


def preview_skip_reason(path_or_img, limit_mvox: float = DEFAULT_PREVIEW_MVOX,
                        all_volumes: bool = False):
    """Why a preview should be skipped (too many voxels to read quickly), or None if it is fine.

    all_volumes=False: the preview reads one 3D volume (thumbnails, overlays).
    all_volumes=True: it reads every volume (GIF preview of a 4D series).
    """
    if not limit_mvox or limit_mvox <= 0:
        return None
    per_volume, total = voxel_counts(path_or_img)
    n = total if all_volumes else per_volume
    if n > limit_mvox * 1e6:
        return f"preview skipped: {n / 1e6:.0f} M voxels > limit {limit_mvox:g} M (see Settings)"
    return None


def load_strided(img, max_dim: int = 256, volume: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """One 3D volume, reading only every k-th voxel so that no axis exceeds `max_dim`.

    Returns (float32 data, affine of the strided grid). Taking every k-th voxel along an axis
    multiplies that affine column by k; the first voxel stays where it was.
    """
    img = load(img) if isinstance(img, str) else img
    k = max(1, int(np.ceil(max(img.shape[:3]) / max_dim)))
    sl = (slice(None, None, k),) * 3
    if len(img.shape) > 3:
        sl = sl + (volume,) + (0,) * (len(img.shape) - 4)
    data = np.nan_to_num(np.asanyarray(img.dataobj[sl]).astype(np.float32))
    return data, img.affine @ np.diag([k, k, k, 1])
