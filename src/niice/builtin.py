"""Built-in NiiCE tools. They use the same registry as plugins (see niice.plugins)."""
from .plugins import Tool, register

ANTS = ("antsRegistrationSyNQuick.sh", "antsRegistrationSyN.sh", "antsApplyTransforms")

for t in [
    Tool("info", "Info", "niice.gui.info:run", order=1,
         description="Header and image properties; compares headers when several images are selected."),
    Tool("math", "Math", "niice.gui.mathgui:run", order=2, max_files=26,
         description="Voxel-wise numpy expression between images (a, b, c, ...)."),

    Tool("ops.binarise", "Operations/Binarise", "niice.gui.ops:binarise", order=1,
         description="Mask of voxels above a threshold (default > 0)."),
    Tool("ops.threshold", "Operations/Threshold", "niice.gui.ops:threshold", order=2,
         description="Keep voxels between a lower and/or upper bound (values or percentiles)."),
    Tool("ops.extract_roi", "Operations/Extract ROI", "niice.gui.ops:extract_roi", order=3,
         description="Mask of one or more labels from a label/atlas image."),
    Tool("ops.extract_volume", "Operations/Extract volume", "niice.gui.ops:extract_volume", order=4,
         description="Pick one or more volumes from a 4D image."),
    Tool("ops.resample", "Operations/Resample", "niice.gui.ops:resample", order=5,
         description="Change voxel size or dimensions, or match a reference image."),
    Tool("ops.subtract", "Operations/Subtract", "niice.gui.ops:subtract", order=6, max_files=2,
         description="A minus B."),
    Tool("ops.skullstrip", "Operations/Skull strip (SynthStrip)", "niice.gui.ops:skullstrip", order=7,
         description="Brain extraction with FreeSurfer SynthStrip (native, Docker, Podman or Apptainer)."),

    Tool("plot.gif", "Plot/Make GIF", "niice.gui.plot:gif", order=1,
         description="Animated GIF sweeping through slices (3D) or time (4D)."),
    Tool("plot.histogram", "Plot/Histogram", "niice.gui.plot:histogram", order=2,
         description="Histogram of voxel intensities, optionally within a mask."),

    Tool("reg.register", "Registration/Register (ANTs)", "niice.gui.registration:register", order=1,
         max_files=2, requires=ANTS[:2],
         description="Rigid, affine or SyN registration with antsRegistrationSyN[Quick].sh."),
    Tool("reg.apply", "Registration/Apply transforms (ANTs)", "niice.gui.registration:apply", order=2,
         max_files=3, nifti_only=False, requires=ANTS[2:],
         description="Apply saved transforms (or their inverse) with antsApplyTransforms."),

    Tool("settings", "Settings", "niice.gui.settings:run", order=999, min_files=0, nifti_only=False,
         always_on=True, description="Choose which tools appear in the menu; manage plugins."),
]:
    register(t)
