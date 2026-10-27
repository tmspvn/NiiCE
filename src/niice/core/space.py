"""Do images share the same space? Compare voxel grids and positions in the world.

Background: a NIfTI affine A (4x4) maps a voxel index (i, j, k) to a position in millimetres in
"world" space:  [x, y, z, 1] = A @ [i, j, k, 1].  Two images are on the *same voxel grid* when they
have the same dimensions and the same A: then voxel (i, j, k) is the same place in both, and
voxel-wise math is meaningful. With different affines they can still be in the same *world* space
(e.g. a 1 mm T1 and a 2 mm fMRI of one session); viewers align them, voxel-wise tools do not.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import nibabel as nib
import numpy as np

from .io import load

AFFINE_TOL = 1e-3          # differences below this (mm, or unitless for rotations) are rounding
LOW_OVERLAP = 0.5          # warn when less than half of the smaller field of view is shared
XFORM_NAMES = {0: "unknown", 1: "scanner", 2: "aligned", 3: "Talairach", 4: "MNI", 5: "template"}
TEMPLATE_CODES = {3, 4, 5}


@dataclass
class SpaceComparison:
    level: str                       # 'same' | 'grid' (same world, other grid) | 'warning'
    summary: str
    issues: list = field(default_factory=list)
    overlap: float = 1.0             # fraction of the smaller field of view shared by both

    @property
    def same_grid(self) -> bool:
        return self.level == "same"


def fov_box(shape, affine) -> np.ndarray:
    """Axis-aligned bounding box (2 x 3: min, max in mm) of the field of view.

    Uses the outer voxel edges, i.e. voxel coordinates -0.5 and n - 0.5 on each axis.
    """
    lo, hi = -0.5, np.array(shape[:3]) - 0.5
    corners = np.array([[a, b, c, 1] for a in (lo, hi[0]) for b in (lo, hi[1]) for c in (lo, hi[2])])
    world = (np.asarray(affine) @ corners.T).T[:, :3]
    return np.stack([world.min(0), world.max(0)])


def overlap_fraction(shape_a, aff_a, shape_b, aff_b) -> float:
    """Volume of the intersection of the two boxes / volume of the smaller box (0..1)."""
    a, b = fov_box(shape_a, aff_a), fov_box(shape_b, aff_b)
    inter = np.clip(np.minimum(a[1], b[1]) - np.maximum(a[0], b[0]), 0, None).prod()
    smaller = min(np.prod(a[1] - a[0]), np.prod(b[1] - b[0]))
    return float(inter / smaller) if smaller > 0 else 0.0


def canonical_affine(shape, affine) -> np.ndarray:
    """Affine after reordering/flipping voxel axes to the closest RAS orientation."""
    ornt = nib.io_orientation(affine)
    return np.asarray(affine) @ nib.orientations.inv_ornt_aff(ornt, shape[:3])


def tilt_degrees(shape_a, aff_a, shape_b, aff_b) -> float:
    """Angle between the two grids' axes, ignoring axis order and flips.

    With D_a, D_b the 3x3 direction matrices (affine columns divided by their length), the
    relative rotation is R = D_a^T D_b and its angle is arccos((trace(R) - 1) / 2).
    """
    def directions(shape, aff):
        m = canonical_affine(shape, aff)[:3, :3]
        return m / np.linalg.norm(m, axis=0)
    r = directions(shape_a, aff_a).T @ directions(shape_b, aff_b)
    return float(np.degrees(np.arccos(np.clip((np.trace(r) - 1) / 2, -1, 1))))


def centre(shape, affine) -> np.ndarray:
    """World position (mm) of the centre of the field of view."""
    return (np.asarray(affine) @ np.r_[(np.array(shape[:3]) - 1) / 2, 1])[:3]


def space_code(img) -> int:
    hdr = img.header
    if "sform_code" not in hdr:
        return 0
    return int(hdr["sform_code"]) or int(hdr["qform_code"])


def qform_sform_issue(img):
    """Message if the image's own qform and sform both exist but place it differently."""
    hdr = img.header
    if "sform_code" not in hdr or not (int(hdr["sform_code"]) and int(hdr["qform_code"])):
        return None
    q, s = img.get_qform(), img.get_sform()
    shape = img.shape[:3]
    corners = np.array([[a, b, c, 1] for a in (0, shape[0] - 1) for b in (0, shape[1] - 1)
                        for c in (0, shape[2] - 1)]).T
    dist = float(np.abs((q @ corners) - (s @ corners))[:3].max())
    if dist < 0.01:
        return None
    return (f"qform and sform disagree (they place voxels up to {dist:.2f} mm apart). "
            "Programs that read different fields will put this image in different places.")


def compare(img_a, img_b) -> SpaceComparison:
    """Compare image B against reference image A."""
    sa, sb = img_a.shape[:3], img_b.shape[:3]
    aa, ab = img_a.affine, img_b.affine
    if sa == sb and np.allclose(aa, ab, atol=AFFINE_TOL):
        return SpaceComparison("same", "Same voxel grid: each voxel is the same location in both images.")

    issues = []
    za = np.sqrt((aa[:3, :3] ** 2).sum(0))
    zb = np.sqrt((ab[:3, :3] ** 2).sum(0))
    if sa != sb:
        issues.append(f"Dimensions differ: {' x '.join(map(str, sa))} vs {' x '.join(map(str, sb))}")
    if not np.allclose(za, zb, atol=AFFINE_TOL):
        issues.append(f"Voxel size differs: {' x '.join(f'{z:.3g}' for z in za)} vs "
                      f"{' x '.join(f'{z:.3g}' for z in zb)} mm")
    oa, ob = "".join(nib.aff2axcodes(aa)), "".join(nib.aff2axcodes(ab))
    if oa != ob:
        issues.append(f"Voxel axes are ordered differently: {oa} vs {ob} "
                      "(fine for viewers; voxel-wise tools would mix up directions)")
    tilt = tilt_degrees(sa, aa, sb, ab)
    if tilt > 0.1:
        issues.append(f"Grids are rotated by {tilt:.1f} degrees relative to each other (oblique slices)")
    shift = float(np.linalg.norm(centre(sa, aa) - centre(sb, ab)))
    if shift > 0.5:
        issues.append(f"Centres of the fields of view are {shift:.1f} mm apart")
    ca, cb = space_code(img_a), space_code(img_b)
    codes_conflict = ca != cb and (ca in TEMPLATE_CODES or cb in TEMPLATE_CODES) and ca and cb
    if ca != cb:
        issues.append(f"Space labels differ: {XFORM_NAMES.get(ca, ca)} vs {XFORM_NAMES.get(cb, cb)}"
                      + (" (subject vs template space?)" if codes_conflict else ""))
    ov = overlap_fraction(sa, aa, sb, ab)

    warning = None
    if ov == 0:
        warning = ("The fields of view do not overlap at all: these images are not in the same "
                   "space, or a header is wrong.")
    elif ov < LOW_OVERLAP:
        warning = (f"Only {100 * ov:.0f}% of the smaller field of view overlaps: probably different "
                   "spaces (not registered), or a wrong header.")
    elif sa == sb:
        warning = ("Same dimensions but different affines: voxel-wise math would run without error, "
                   "yet voxel (i, j, k) is NOT the same location in both images.")
    elif codes_conflict:
        warning = "One image is labelled as template space and the other is not: check they were registered."
    if warning:
        return SpaceComparison("warning", warning, issues, ov)
    return SpaceComparison(
        "grid", "Same world space, different voxel grid: viewers align them, but voxel-wise tools "
                "(Math, Subtract) need Resample -> 'Match a reference image' first.", issues, ov)


def compare_paths(paths) -> list[tuple[str, SpaceComparison]]:
    """Each image after the first compared against the first."""
    ref = load(paths[0])
    return [(p, compare(ref, load(p))) for p in paths[1:]]


# --------------------------------------------------------------------------- visual alignment
def _corners(shape, affine) -> np.ndarray:
    lo, hi = -0.5, np.array(shape[:3]) - 0.5
    pts = np.array([[a, b, c, 1] for a in (lo, hi[0]) for b in (lo, hi[1]) for c in (lo, hi[2])])
    return (np.asarray(affine) @ pts.T)  # 4 x 8, world


def _norm(x: np.ndarray) -> np.ndarray:
    nz = x[x != 0]
    if not nz.size:
        return np.zeros_like(x)
    lo, hi = np.percentile(nz, [1, 99])
    return np.clip((x - lo) / max(hi - lo, 1e-6), 0, 1)


def resample_pair(ref, mov, by: str = "world", max_dim: int = 160, union: bool = True) -> dict:
    """Put a reference and a moving image on one display grid, for looking at their alignment.

    by='world':  each image is placed by its own affine (what viewers and ANTs see).
    by='voxel':  the headers are ignored and voxel (i, j, k) of one image is put on voxel (i, j, k)
                 of the other (stretched if the dimensions differ) - what voxel-wise math assumes.

    The display grid has the reference's voxel axes reordered to RAS (so slices come out
    upright), covers both fields of view when union=True, and has at most ~max_dim voxels per axis.
    Returns intensities scaled to 0..1 ('ref', 'mov'), field-of-view masks ('ref_fov', 'mov_fov'),
    the grid 'affine' and voxel size 'zooms'.
    """
    from scipy import ndimage

    ref = load(ref) if isinstance(ref, str) else ref
    mov = load(mov) if isinstance(mov, str) else mov
    from .io import load_strided
    r_data, r_aff = load_strided(ref, max_dim)
    m_data, m_aff = load_strided(mov, max_dim)
    if by == "voxel":
        # full-resolution moving voxel v -> reference voxel s*(v + 0.5) - 0.5, s = shape ratio
        s = np.array(ref.shape[:3], float) / np.array(mov.shape[:3], float)
        stretch = np.eye(4)
        stretch[:3, :3] = np.diag(s)
        stretch[:3, 3] = (s - 1) / 2
        m_aff = ref.affine @ stretch @ (np.linalg.inv(mov.affine) @ m_aff)
        mov_affine_full = ref.affine @ stretch
    else:
        mov_affine_full = mov.affine

    can = canonical_affine(ref.shape, ref.affine)
    pts = _corners(ref.shape, ref.affine)
    if union:
        pts = np.concatenate([pts, _corners(mov.shape, mov_affine_full)], axis=1)
    vox = (np.linalg.inv(can) @ pts)[:3]
    lo, hi = np.floor(vox.min(1) + 0.5), np.ceil(vox.max(1) - 0.5)
    step = max(1.0, float(np.ceil((hi - lo).max() / max_dim)))
    shape = tuple(int(n) for n in np.floor((hi - lo) / step) + 1)
    grid = can @ np.array([[step, 0, 0, lo[0]], [0, step, 0, lo[1]], [0, 0, step, lo[2]], [0, 0, 0, 1]])

    def onto(data, aff, order=1):
        m = np.linalg.inv(aff) @ grid  # display voxel -> image voxel
        return ndimage.affine_transform(data, m[:3, :3], offset=m[:3, 3], output_shape=shape,
                                        order=order, cval=0.0)

    return {
        "ref": _norm(onto(r_data, r_aff)),
        "mov": _norm(onto(m_data, m_aff)),
        "ref_fov": onto(np.ones(r_data.shape, np.float32), r_aff, order=0) > 0.5,
        "mov_fov": onto(np.ones(m_data.shape, np.float32), m_aff, order=0) > 0.5,
        "affine": grid,
        "zooms": np.sqrt((grid[:3, :3] ** 2).sum(0)),
    }


def plane(vol: np.ndarray, axis: int, idx: int) -> np.ndarray:
    """2D slice of a RAS-ordered volume, superior/anterior up (axis 0 sagittal, 1 coronal, 2 axial)."""
    sl = (vol[idx, :, :], vol[:, idx, :], vol[:, :, idx])[axis]
    return np.flipud(sl.T)


def plane_zooms(zooms, axis) -> tuple[float, float]:
    """(row spacing, column spacing) in mm of plane(vol, axis, ...)."""
    rest = [z for i, z in enumerate(zooms) if i != axis]
    return rest[1], rest[0]


def composite(ref2d, mov2d, mode: str = "overlay", alpha: float = 0.5, tile: int = 16,
              ref_fov=None, mov_fov=None) -> np.ndarray:
    """RGB (H, W, 3) float 0..1 picture of two aligned slices.

    alpha (0..1) balances the two images: 0 shows only the reference, 1 only the moving image,
    0.5 both at full strength (overlay, contour) or an equal mix (blend). Weights:
    w_ref = min(1, 2 (1 - alpha)), w_mov = min(1, 2 alpha).
    overlay:  reference in magenta (red + blue), moving in green; where both are bright the sum
              is grey/white, so misalignment shows as coloured fringes.
    checker:  alternating tiles of the two images; edges should continue across tile borders.
    contour:  reference in grey with the moving image's edges in yellow.
    blend:    (1 - alpha) * reference + alpha * moving.
    The outlines of the two fields of view are drawn in magenta / green when given.
    """
    r, m = ref2d, mov2d
    w_ref, w_mov = min(1.0, 2 * (1 - alpha)), min(1.0, 2 * alpha)
    if mode == "overlay":
        rgb = np.stack([w_ref * r, w_mov * m, w_ref * r], -1)
    elif mode == "checker":
        rows, cols = np.indices(r.shape)
        pick = ((rows // tile + cols // tile) % 2).astype(bool)
        g = np.where(pick, m, r)
        rgb = np.stack([g, g, g], -1)
    elif mode == "contour":
        gy, gx = np.gradient(m)
        mag = np.hypot(gx, gy)
        nz = mag[mag > 0]
        edge = mag > (np.percentile(nz, 85) if nz.size else 1)
        rgb = np.stack([w_ref * r] * 3, -1)
        rgb[edge] = (1 - w_mov) * rgb[edge] + w_mov * np.array([1.0, 0.85, 0.0])
    else:
        g = (1 - alpha) * r + alpha * m
        rgb = np.stack([g, g, g], -1)
    for fov, colour in ((ref_fov, (0.9, 0.2, 0.7)), (mov_fov, (0.2, 0.9, 0.3))):
        if fov is not None and fov.any():
            inner = fov.copy()
            inner[1:-1, 1:-1] = fov[1:-1, 1:-1] & fov[:-2, 1:-1] & fov[2:, 1:-1] & fov[1:-1, :-2] & fov[1:-1, 2:]
            rgb[fov & ~inner] = colour
    return np.clip(rgb, 0, 1)
