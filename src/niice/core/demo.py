"""Small synthetic images for trying the tools (`niice demo`)."""
from __future__ import annotations

import os

import nibabel as nib
import numpy as np


def _head(shape=(80, 96, 80), shift=(0, 0, 0), seed=0) -> np.ndarray:
    """Simple head phantom: bright skull shell, grey brain, brighter core, two dark ventricles."""
    x, y, z = np.meshgrid(*[np.arange(s) - s / 2 for s in shape], indexing="ij")
    x, y, z = x - shift[0], y - shift[1], z - shift[2]
    r = (x / 30) ** 2 + (y / 38) ** 2 + (z / 30) ** 2
    img = np.zeros(shape, np.float32)
    img[r < 1.25] = 300
    img[r < 1.0] = 0
    img[r < 0.9] = 120
    img[r < 0.6] = 160
    img[((np.abs(x) - 5) / 4) ** 2 + (y / 14) ** 2 + (z / 6) ** 2 < 1] = 30
    img += np.random.default_rng(seed).normal(0, 5, shape).astype(np.float32)
    return np.clip(img, 0, None)


def write_demo(folder: str) -> list[tuple[str, str]]:
    os.makedirs(folder, exist_ok=True)
    aff = np.diag([2.0, 2.0, 2.0, 1.0])
    aff[:3, 3] = [-80, -96, -80]
    t1 = _head()
    out = []

    def save(name, data, affine, what, zooms=None):
        img = nib.Nifti1Image(data, affine)
        img.header.set_xyzt_units("mm", "sec")
        if zooms:
            img.header.set_zooms(zooms)
        path = os.path.join(folder, name)
        nib.save(img, path)
        out.append((path, what))

    save("T1w.nii.gz", t1, aff, "3D 'T1', 2 mm")
    shifted = aff.copy()
    shifted[:3, 3] += [8, -6, 4]
    save("T2w_shifted.nii.gz", _head(shift=(3, 0, 0), seed=1) * 0.8, shifted,
         "same size as T1w, header shifted ~11 mm (alignment warning)")
    labels = np.zeros(t1.shape, np.int16)
    labels[20:40, 30:60, 30:50] = 17
    labels[40:60, 30:60, 30:50] = 53
    save("labels.nii.gz", labels, aff, "label image (17, 53) on the T1w grid")
    small = _head()[::2, ::2, ::2]
    bold = np.stack([small * (1 + 0.02 * np.sin(t / 3)) for t in range(40)], -1).astype(np.float32)
    bold_aff = np.diag([4.0, 4.0, 4.0, 1.0])
    bold_aff[:3, 3] = [-80, -96, -80]
    save("bold.nii.gz", bold, bold_aff, "4D series, 40 volumes, 4 mm, TR 2 s", zooms=(4, 4, 4, 2.0))
    return out
