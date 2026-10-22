"""GIF animations and intensity histograms."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

import nibabel as nib
import numpy as np
from scipy.ndimage import zoom

from .io import load

VIEWS = ("sagittal", "coronal", "axial")
MAX_GIF_FPS = 50  # GIF delays are in 1/100 s; browsers show delays < 2/100 s as 1/10 s


@dataclass
class GifOptions:
    view: str = "sagittal"          # which axis to sweep (3D) / which plane to show (4D)
    reorient: bool = True           # reorient to RAS so slices come out upright
    slice_index: Optional[int] = None  # 4D only: slice of the plane shown (None = middle)
    rotate: int = 0                 # extra in-plane rotation, multiples of 90 degrees
    flip: str = "none"              # 'none' | 'horizontal' | 'vertical'
    rewind: bool = True             # play forwards then backwards
    winsorize: tuple = (1.0, 98.0)  # intensity percentiles mapped to black/white
    frame_norm: bool = False        # normalise each frame separately
    timebar: bool = True            # progress bar at the bottom
    crosshair: Optional[Sequence[int]] = None  # voxel (x, y, z) in the file's own voxel space
    scale: float = 2.0              # magnification (voxel aspect ratio is also corrected)
    cmap: Optional[str] = None      # matplotlib colormap name, None = grey
    crop: bool = True               # crop empty (zero) borders
    fps: int = 25
    step: int = 1                   # use every n-th slice/volume
    layout: str = "horizontal"      # how several images are placed next to each other
    extra: dict = field(default_factory=dict)


def _prepare(path: str, o: GifOptions):
    """Load an image as (data, zooms, crosshair_voxel) in display orientation."""
    img = load(path)
    xh = np.array(o.crosshair, float) if o.crosshair is not None else None
    if o.reorient:
        can = nib.as_closest_canonical(img)
        if xh is not None:  # file voxel -> world -> canonical voxel
            m = np.linalg.inv(can.affine) @ img.affine
            xh = (m @ np.r_[xh, 1])[:3]
        img = can
    data = np.nan_to_num(np.asanyarray(img.dataobj).astype(np.float32))
    zooms = np.array(img.header.get_zooms()[:3], float)
    if o.crop:
        spatial = data if data.ndim == 3 else np.abs(data).sum(axis=3)
        nz = np.nonzero(spatial)
        if nz[0].size:
            lo = [int(n.min()) for n in nz]
            hi = [int(n.max()) + 1 for n in nz]
            data = data[lo[0]:hi[0], lo[1]:hi[1], lo[2]:hi[2], ...]
            if xh is not None:
                xh = xh - np.array(lo)
    return data, zooms, xh


def _plane(vol3: np.ndarray, axis: int, idx: int) -> np.ndarray:
    """2D slice of a RAS volume, oriented so that superior/anterior is up."""
    if axis == 0:   # sagittal: plane (y, z) -> rows z (top = superior), cols y (anterior right)
        sl = vol3[idx, :, :]
    elif axis == 1:  # coronal: plane (x, z)
        sl = vol3[:, idx, :]
    else:            # axial: plane (x, y) -> rows y (top = anterior)
        sl = vol3[:, :, idx]
    return np.flipud(sl.T)


def _plane_zooms(zooms, axis):
    rest = [z for i, z in enumerate(zooms) if i != axis]
    return rest[1], rest[0]  # (row spacing, column spacing) after the transpose


def render_frames(paths: Sequence[str], o: GifOptions, progress=None) -> np.ndarray:
    """Return frames as uint8 array (n, H, W) for grey or (n, H, W, 3) for a colormap."""
    axis = VIEWS.index(o.view) if isinstance(o.view, str) else int(o.view)
    movies = []
    for k, path in enumerate(paths):
        if progress:
            progress(k / len(paths), f"Rendering {k + 1}/{len(paths)}")
        data, zooms, xh = _prepare(path, o)
        if data.ndim == 4:
            idx = data.shape[axis] // 2 if o.slice_index is None else int(o.slice_index)
            idx = int(np.clip(idx, 0, data.shape[axis] - 1))
            frames = [_plane(data[..., t], axis, idx) for t in range(0, data.shape[3], o.step)]
        else:
            frames = [_plane(data, axis, i) for i in range(0, data.shape[axis], o.step)]
        movie = np.stack(frames).astype(np.float32)

        # Intensity window: percentiles of non-zero voxels -> 0..1
        if o.frame_norm:
            for i in range(len(movie)):
                movie[i] = _window(movie[i], o.winsorize)
        else:
            movie = _window(movie, o.winsorize)

        # Crosshair (in-plane voxel coordinates, before magnification)
        if xh is not None:
            in_plane = [c for i, c in enumerate(xh) if i != axis]  # (col_axis, row_axis) coords
            col = int(round(in_plane[0]))
            row = movie.shape[1] - 1 - int(round(in_plane[1]))
            if 0 <= row < movie.shape[1]:
                movie[:, row, :] = 1.0
            if 0 <= col < movie.shape[2]:
                movie[:, :, col] = 1.0

        # Magnify and correct for non-square pixels
        rz, cz = _plane_zooms(zooms, axis)
        base = min(rz, cz)
        fr, fc = o.scale * rz / base, o.scale * cz / base
        if abs(fr - 1) > 1e-3 or abs(fc - 1) > 1e-3:
            movie = np.stack([zoom(f, (fr, fc), order=1) for f in movie])
        movies.append(np.clip(movie, 0, 1))

    # Several images: pad to a common size and number of frames, then concatenate
    n = max(len(m) for m in movies)
    movies = [np.concatenate([m, np.repeat(m[-1:], n - len(m), 0)]) if len(m) < n else m for m in movies]
    cat_axis = 2 if o.layout == "horizontal" else 1
    other = 1 if cat_axis == 2 else 2
    size = max(m.shape[other] for m in movies)
    padded = []
    for m in movies:
        pad = size - m.shape[other]
        widths = [(0, 0), (0, 0), (0, 0)]
        widths[other] = (pad // 2, pad - pad // 2)
        padded.append(np.pad(m, widths))
    movie = np.concatenate(padded, axis=cat_axis)

    if o.timebar and len(movie) > 1:
        h, w = movie.shape[1], movie.shape[2]
        bar = max(1, h // 100)
        for i in range(len(movie)):
            movie[i, h - bar:, : int(round(w * (i + 1) / len(movie)))] = 1.0
    if o.rotate % 4:
        movie = np.rot90(movie, k=o.rotate % 4, axes=(1, 2))
    if o.flip == "horizontal":
        movie = movie[:, :, ::-1]
    elif o.flip == "vertical":
        movie = movie[:, ::-1, :]
    if o.rewind and len(movie) > 2:
        movie = np.concatenate([movie, movie[-2:0:-1]])

    if o.cmap and o.cmap.lower() not in ("grey", "gray", "none"):
        import matplotlib
        cmap = matplotlib.colormaps[o.cmap]
        return (cmap(movie)[..., :3] * 255).astype(np.uint8)
    return (movie * 255).astype(np.uint8)


def _window(x: np.ndarray, pct) -> np.ndarray:
    vals = x[x != 0]
    if vals.size == 0:
        return np.zeros_like(x)
    lo, hi = np.percentile(vals, pct)
    if hi <= lo:
        hi = lo + 1e-6
    return np.clip((x - lo) / (hi - lo), 0, 1)


def save_gif(frames: np.ndarray, out: str, fps: int = 25) -> str:
    from PIL import Image
    fps = int(np.clip(fps, 1, MAX_GIF_FPS))
    mode = "RGB" if frames.ndim == 4 else "L"
    imgs = [Image.fromarray(f, mode=mode) for f in frames]
    imgs[0].save(out, save_all=True, append_images=imgs[1:], duration=int(round(1000 / fps)),
                 loop=0, optimize=False)
    return out


def make_gif(paths: Sequence[str], out: str, options: Optional[GifOptions] = None, progress=None) -> str:
    o = options or GifOptions()
    frames = render_frames(paths, o, progress)
    if progress:
        progress(0.95, "Writing GIF")
    return save_gif(frames, out, o.fps)


# --------------------------------------------------------------------------- histogram
def histogram_values(path: str, mask: Optional[str] = None, exclude_zeros: bool = True) -> np.ndarray:
    """Finite voxel values, optionally only inside `mask` (> 0) and/or excluding zeros."""
    data = np.asanyarray(load(path).dataobj).astype(np.float64)
    sel = np.isfinite(data)
    if mask:
        m = np.asanyarray(load(mask).dataobj) > 0
        if m.shape[:3] != data.shape[:3]:
            raise ValueError(f"Mask grid {m.shape[:3]} differs from image grid {data.shape[:3]}")
        if data.ndim == 4 and m.ndim == 3:
            m = np.broadcast_to(m[..., None], data.shape)
        sel &= m
    if exclude_zeros:
        sel &= data != 0
    return data[sel]


def describe(values: np.ndarray) -> dict:
    if values.size == 0:
        return {"N": 0}
    p = np.percentile(values, [2, 25, 50, 75, 98])
    return {"N": int(values.size), "Mean": float(values.mean()), "SD": float(values.std()),
            "Min": float(values.min()), "P2": p[0], "Q1": p[1], "Median": p[2], "Q3": p[3],
            "P98": p[4], "Max": float(values.max())}
