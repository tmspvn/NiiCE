"""Example NiiCE plugin with its own logic: count non-zero voxels and their volume.

Copy into the plugins folder (NiiCE Settings -> Plugins -> Open folder).
"""
from niice.plugins import tool


@tool(id="example.voxel_count", menu="Info/Voxel count", min_files=1,
      description="Number of non-zero voxels and their volume in mL")
def voxel_count(files):
    import numpy as np
    from niice.core import io
    from niice.gui.common import message

    lines = []
    for f in files:
        img = io.load(f)
        n = int(np.count_nonzero(np.asanyarray(img.dataobj)))
        voxel_ml = float(np.prod(img.header.get_zooms()[:3])) / 1000  # mm^3 -> mL
        lines.append(f"{io.stem(f)}: {n} voxels = {n * voxel_ml:.2f} mL")
    message("Voxel count", "\n".join(lines))
