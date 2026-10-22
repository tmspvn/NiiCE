"""Header and image properties, and comparison of several headers."""
from __future__ import annotations

import os
from collections import OrderedDict

import nibabel as nib
import numpy as np

from .io import load

# Short descriptions from nifti1.h (https://nifti.nimh.nih.gov/nifti-1/documentation/nifti1fields/)
FIELD_HELP = {
    "sizeof_hdr": "Must be 348 (NIfTI-1) or 540 (NIfTI-2)",
    "dim_info": "MRI slice ordering (freq/phase/slice dims)",
    "dim": "Data array dimensions; dim[0] = number of dims",
    "intent_p1": "1st intent parameter",
    "intent_p2": "2nd intent parameter",
    "intent_p3": "3rd intent parameter",
    "intent_code": "NIFTI_INTENT_* code (what the values mean)",
    "datatype": "Data type code on disk",
    "bitpix": "Number of bits per voxel",
    "slice_start": "First slice index",
    "pixdim": "Grid spacings; pixdim[1..3] voxel size, pixdim[4] TR",
    "vox_offset": "Offset of the data inside the .nii file",
    "scl_slope": "Data scaling: stored * slope + inter",
    "scl_inter": "Data scaling: offset",
    "slice_end": "Last slice index",
    "slice_code": "Slice timing order",
    "xyzt_units": "Units of pixdim[1..4]",
    "cal_max": "Max display intensity",
    "cal_min": "Min display intensity",
    "slice_duration": "Time for one slice",
    "toffset": "Time axis shift",
    "descrip": "Free text",
    "aux_file": "Auxiliary file name",
    "qform_code": "0 unknown, 1 scanner, 2 aligned, 3 Talairach, 4 MNI, 5 template",
    "sform_code": "0 unknown, 1 scanner, 2 aligned, 3 Talairach, 4 MNI, 5 template",
    "quatern_b": "Quaternion b parameter (qform rotation)",
    "quatern_c": "Quaternion c parameter (qform rotation)",
    "quatern_d": "Quaternion d parameter (qform rotation)",
    "qoffset_x": "qform x shift (mm)",
    "qoffset_y": "qform y shift (mm)",
    "qoffset_z": "qform z shift (mm)",
    "srow_x": "1st row of the sform affine",
    "srow_y": "2nd row of the sform affine",
    "srow_z": "3rd row of the sform affine",
    "intent_name": "Name or meaning of the data",
    "magic": "Must be 'n+1' (single file) or 'ni1' (hdr/img pair)",
}

XFORM_CODES = {0: "unknown", 1: "scanner", 2: "aligned", 3: "talairach", 4: "mni", 5: "template"}


def fmt(value) -> str:
    """Compact, human-readable string for header values (arrays rounded to 4 decimals)."""
    if isinstance(value, bytes):
        return value.decode(errors="replace").strip("\x00")
    arr = np.asarray(value)
    if arr.dtype.kind in "SU":
        return str(arr.item() if arr.ndim == 0 else arr).strip("b'\x00")
    if arr.ndim == 0:
        v = arr.item()
        return f"{v:.6g}" if isinstance(v, float) else str(v)
    if arr.dtype.kind == "f":
        return np.array2string(np.round(arr, 4), separator=", ", suppress_small=True,
                               max_line_width=200, formatter={"float_kind": lambda x: f"{x:.4g}"})
    return np.array2string(arr, separator=", ", max_line_width=200)


def header_fields(img) -> "OrderedDict[str, object]":
    """All raw header fields as stored on disk."""
    hdr = img.header
    return OrderedDict((k, hdr[k]) for k in hdr.keys())


def summary(path: str) -> "OrderedDict[str, str]":
    """Human-friendly properties that do not need to read the voxel data."""
    img = load(path)
    hdr = img.header
    shape = img.shape
    zooms = hdr.get_zooms()
    info = OrderedDict()
    info["File"] = os.path.basename(path)
    info["Folder"] = os.path.dirname(os.path.abspath(path))
    info["Size on disk"] = _human_size(os.path.getsize(path))
    info["Format"] = type(img).__name__.replace("Image", "")
    info["Dimensions"] = " x ".join(str(s) for s in shape)
    info["Voxel size"] = " x ".join(f"{z:.4g}" for z in zooms[:3])
    space_u, time_u = hdr.get_xyzt_units() if hasattr(hdr, "get_xyzt_units") else ("unknown", "unknown")
    info["Spatial units"] = space_u
    if len(shape) > 3:
        info["Volumes"] = str(shape[3])
        info["TR / 4th dim spacing"] = f"{zooms[3]:.4g} {time_u}" if len(zooms) > 3 else "n/a"
    info["Field of view"] = " x ".join(f"{s * z:.1f}" for s, z in zip(shape[:3], zooms[:3]))
    info["Data type (disk)"] = str(hdr.get_data_dtype())
    slope, inter = hdr.get_slope_inter()
    if slope is not None and (slope != 1 or (inter or 0) != 0):
        info["Scaling"] = f"x {slope:g} + {inter or 0:g}"
    info["Orientation"] = "".join(nib.aff2axcodes(img.affine))
    if hasattr(hdr, "get_qform"):
        info["qform code"] = _code(hdr["qform_code"])
        info["sform code"] = _code(hdr["sform_code"])
    info["Affine"] = fmt(img.affine)
    descrip = fmt(hdr["descrip"]) if "descrip" in hdr else ""
    if descrip:
        info["Description"] = descrip
    return info


def intensity_stats(path: str) -> "OrderedDict[str, str]":
    """Statistics that require reading the data (can be slow for big 4D files)."""
    img = load(path)
    data = np.asanyarray(img.dataobj)  # scl_slope/inter already applied
    n_nan = int(np.isnan(data).sum()) if data.dtype.kind == "f" else 0
    finite = data[np.isfinite(data)] if data.dtype.kind == "f" else data.ravel()
    finite = finite.astype(np.float64)
    st = OrderedDict()
    if finite.size == 0:
        st["Values"] = "no finite voxels"
        return st
    st["Min"] = f"{finite.min():.6g}"
    st["Max"] = f"{finite.max():.6g}"
    st["Mean"] = f"{finite.mean():.6g}"
    st["Std"] = f"{finite.std():.6g}"
    st["Median"] = f"{np.median(finite):.6g}"
    nz = int(np.count_nonzero(finite))
    st["Non-zero voxels"] = f"{nz} ({100 * nz / data.size:.1f}%)"
    st["NaN voxels"] = str(n_nan)
    if np.all(np.mod(finite, 1) == 0):
        uniq = np.unique(finite)
        if uniq.size <= 1000:
            st["Integer image"] = f"{uniq.size} distinct values (labels/mask?)"
    return st


def compare(paths: list[str], include_stats: bool = False):
    """Table for several images.

    Returns (rows, differs): rows is a list of (field, [value per image]) and differs a list of
    booleans telling whether the field differs between images. Numeric arrays are compared with
    a tolerance (np.allclose, rtol=1e-5, atol=1e-6) so rounding noise is not flagged.
    """
    summaries = [summary(p) for p in paths]
    headers = [header_fields(load(p)) for p in paths]
    stats = [intensity_stats(p) for p in paths] if include_stats else None
    rows, differs = [], []

    def add(name, raw_values, shown):
        rows.append((name, shown))
        differs.append(_differ(raw_values))

    skip = {"File", "Folder"}
    keys = list(OrderedDict.fromkeys(k for s in summaries for k in s))
    for k in keys:
        if k in skip:
            continue
        vals = [s.get(k, "-") for s in summaries]
        raw = [load(p).affine for p in paths] if k == "Affine" else vals
        add(k, raw, vals)
    if stats:
        for k in OrderedDict.fromkeys(k for s in stats for k in s):
            vals = [s.get(k, "-") for s in stats]
            add(k, vals, vals)
    hkeys = list(OrderedDict.fromkeys(k for h in headers for k in h))
    for k in hkeys:
        raw = [h.get(k) for h in headers]
        add("hdr." + k, raw, [fmt(v) if v is not None else "-" for v in raw])
    return rows, differs


def text_report(paths: list[str]) -> str:
    """Plain-text output for terminals (`niice info --text`)."""
    lines = []
    if len(paths) == 1:
        for k, v in summary(paths[0]).items():
            lines.append(f"{k:<22} {v}")
        for k, v in intensity_stats(paths[0]).items():
            lines.append(f"{k:<22} {v}")
        lines.append("")
        for k, v in header_fields(load(paths[0])).items():
            lines.append(f"{k:<16} {fmt(v)}")
        return "\n".join(lines)
    rows, differs = compare(paths)
    names = [os.path.basename(p) for p in paths]
    lines.append("Fields that differ (* = differs):")
    lines.append(" | ".join(["field"] + names))
    for (field, vals), d in zip(rows, differs):
        if d:
            lines.append("* " + field + ": " + " | ".join(v.replace("\n", " ") for v in vals))
    same = sum(1 for d in differs if not d)
    lines.append(f"\n{same} other fields are identical.")
    return "\n".join(lines)


def _differ(values) -> bool:
    first = values[0]
    for v in values[1:]:
        if v is None or first is None:
            if v is not first:
                return True
            continue
        a, b = np.asarray(first), np.asarray(v)
        if a.dtype.kind in "fiu" and b.dtype.kind in "fiu":
            if a.shape != b.shape:
                return True
            if not np.allclose(a, b, rtol=1e-5, atol=1e-6, equal_nan=True):
                return True
        elif fmt(first) != fmt(v):
            return True
    return False


def _code(c) -> str:
    c = int(c)
    return f"{c} ({XFORM_CODES.get(c, '?')})"


def _human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
