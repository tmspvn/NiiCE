"""Skull stripping with FreeSurfer SynthStrip (https://surfer.nmr.mgh.harvard.edu/docs/synthstrip/).

Backends, tried in this order when set to 'auto':
  native     `mri_synthstrip` on PATH (FreeSurfer >= 7.3 installed)
  docker     `docker run freesurfer/synthstrip`   (https://hub.docker.com/r/freesurfer/synthstrip)
  podman     same image with podman (rootless, no sudo needed)
  apptainer  `apptainer run docker://freesurfer/synthstrip` (HPC clusters; also 'singularity')
"""
from __future__ import annotations

import os
import shutil
from typing import Optional

DEFAULT_IMAGE = "freesurfer/synthstrip"
BACKENDS = ("native", "docker", "podman", "apptainer")


def available_backends() -> dict[str, Optional[str]]:
    """Map backend -> executable path (None if not found)."""
    found = {
        "native": shutil.which("mri_synthstrip"),
        "docker": shutil.which("docker"),
        "podman": shutil.which("podman"),
        "apptainer": shutil.which("apptainer") or shutil.which("singularity"),
    }
    return found


def pick_backend(preference: str = "auto") -> tuple[Optional[str], Optional[str]]:
    """Return (backend, executable) honouring the preference, or (None, None)."""
    found = available_backends()
    if preference != "auto":
        return (preference, found.get(preference)) if found.get(preference) else (None, None)
    for b in BACKENDS:
        if found[b]:
            return b, found[b]
    return None, None


def build_command(backend: str, exe: str, inp: str, out: str, mask: Optional[str] = None,
                  no_csf: bool = False, border: Optional[float] = None,
                  image: str = DEFAULT_IMAGE, pkexec: bool = False) -> list[str]:
    """Command line for one image. Container backends mount the input/output folders."""
    inp, out = os.path.abspath(inp), os.path.abspath(out)
    opts = []
    if no_csf:
        opts.append("--no-csf")
    if border is not None:
        opts += ["-b", f"{border:g}"]

    if backend == "native":
        cmd = [exe, "-i", inp, "-o", out]
        if mask:
            cmd += ["-m", os.path.abspath(mask)]
        return cmd + opts

    # Containers: mount each distinct folder once at /niice/0, /niice/1, ...
    dirs: list[str] = []

    def inside(path: str) -> str:
        d = os.path.dirname(path)
        if d not in dirs:
            dirs.append(d)
        return f"/niice/{dirs.index(d)}/{os.path.basename(path)}"

    args = ["-i", inside(inp), "-o", inside(out)]
    if mask:
        args += ["-m", inside(os.path.abspath(mask))]
    args += opts

    if backend in ("docker", "podman"):
        cmd = [exe, "run", "--rm"]
        for i, d in enumerate(dirs):
            cmd += ["-v", f"{d}:/niice/{i}"]
        if os.name == "posix" and backend == "docker":
            cmd += ["-u", f"{os.getuid()}:{os.getgid()}"]  # output files owned by the user
        cmd += [image] + args
        if pkexec and backend == "docker":
            cmd = ["pkexec"] + cmd  # graphical password prompt instead of terminal sudo
        return cmd
    if backend == "apptainer":
        src = image if "://" in image or image.endswith(".sif") else f"docker://{image}"
        cmd = [exe, "run", "--cleanenv"]
        for i, d in enumerate(dirs):
            cmd += ["-B", f"{d}:/niice/{i}"]
        return cmd + [src] + args
    raise ValueError(f"Unknown backend {backend!r}")


def is_permission_error(output: str) -> bool:
    low = output.lower()
    return "permission denied" in low and ("docker" in low or "daemon" in low or "socket" in low)


INSTALL_HELP = (
    "SynthStrip was not found. Install one of:\n"
    "  - Docker or Podman, then: docker pull freesurfer/synthstrip\n"
    "  - FreeSurfer >= 7.3 (provides mri_synthstrip)\n"
    "  - Apptainer/Singularity (uses docker://freesurfer/synthstrip)\n"
    "To run Docker without sudo, add yourself to the docker group:\n"
    "  sudo usermod -aG docker $USER   (then log out and in)"
)
