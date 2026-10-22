"""Example NiiCE plugin: Gaussian smoothing.

Files in this folder are loaded at start-up. Edit, rename or delete freely, then open
NiiCE Settings and press "Save & update menu" so the context menu picks up the change.
"""
from niice.plugins import image_filter, Float, Choice


@image_filter(
    id="example.smooth",
    menu="Operations/Gaussian smooth",
    suffix="_smooth",
    params={
        "fwhm_mm": Float(4.0, min=0, max=50, step=0.5, label="FWHM (mm)",
                         help="Full width at half maximum of the Gaussian kernel"),
        "mode": Choice(choices=["nearest", "constant", "reflect"], help="Border handling"),
    },
)
def smooth(data, img, fwhm_mm, mode):
    """Smooth each 3D volume with a Gaussian kernel of the given FWHM."""
    import numpy as np
    from scipy.ndimage import gaussian_filter

    # FWHM = 2*sqrt(2*ln 2) * sigma ~= 2.355 * sigma; sigma is converted from mm to voxels per axis
    zooms = np.array(img.header.get_zooms()[:3])
    sigma = list(fwhm_mm / 2.3548 / zooms) + [0] * (data.ndim - 3)
    return gaussian_filter(data, sigma=sigma, mode=mode)
