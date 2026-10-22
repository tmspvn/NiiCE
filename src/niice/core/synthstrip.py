"""Skull stripping with FreeSurfer SynthStrip (https://surfer.nmr.mgh.harvard.edu/docs/synthstrip/).

Backends, tried in this order when set to 'auto':
  python     nipreps-synthstrip installed in the same Python as NiiCE (pip install "niice[synthstrip]");
             needs PyTorch and the model weights, which NiiCE downloads once (~30 MB) from the
             SynthStrip website (weights licensed MIT / CC BY 4.0)
  native     `mri_synthstrip` on PATH (FreeSurfer >= 7.3 installed)
  docker     `docker run freesurfer/synthstrip`   (https://hub.docker.com/r/freesurfer/synthstrip)
  podman     same image with podman (rootless, no sudo needed)
  apptainer  `apptainer run docker://freesurfer/synthstrip` (HPC clusters; also 'singularity')
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
import sys
import urllib.request
from typing import Callable, Optional

DEFAULT_IMAGE = "freesurfer/synthstrip"
BACKENDS = ("python", "native", "docker", "podman", "apptainer")

# Official weights from https://surfer.nmr.mgh.harvard.edu/docs/synthstrip/ ("Code/Weights").
# The SHA-256 fingerprints let us detect an incomplete or altered download.
WEIGHTS_URL = "https://surfer.nmr.mgh.harvard.edu/docs/synthstrip/requirements/{name}"
WEIGHTS = {
    False: ("synthstrip.1.pt", "37417f802196186441aae3e7f385d94f8a98c64a88acaeaa2723af995c653e33"),
    True: ("synthstrip.nocsf.1.pt", "62bf01137c45b5f0cc04d59dbaed5b9ac138b3f25b766c062a7c1a0d696ecb28"),
}
WEIGHTS_MB = 30


def python_backend_installed() -> bool:
    """True if nipreps-synthstrip and torch can be imported by this Python (not imported here)."""
    try:
        return (importlib.util.find_spec("nipreps.synthstrip") is not None
                and importlib.util.find_spec("torch") is not None)
    except (ImportError, ValueError):
        return False


def weights_dir() -> str:
    from ..settings import config_dir
    return os.path.join(config_dir(), "models")


def find_weights(no_csf: bool = False) -> Optional[str]:
    """Weights file for the python backend: NiiCE's models folder, or FreeSurfer's if installed."""
    name = WEIGHTS[no_csf][0]
    candidates = [os.path.join(weights_dir(), name)]
    if os.environ.get("FREESURFER_HOME"):
        candidates.append(os.path.join(os.environ["FREESURFER_HOME"], "models", name))
    return next((c for c in candidates if os.path.isfile(c)), None)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download_weights(no_csf: bool = False, progress: Optional[Callable[[float, str], None]] = None,
                     cancelled: Callable[[], bool] = lambda: False) -> str:
    """Download the weights into weights_dir() and check the fingerprint. Returns the path."""
    name, sha = WEIGHTS[no_csf]
    os.makedirs(weights_dir(), exist_ok=True)
    final = os.path.join(weights_dir(), name)
    tmp = final + ".part"
    url = WEIGHTS_URL.format(name=name)
    with urllib.request.urlopen(url, timeout=60) as resp, open(tmp, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            if cancelled():
                out.close()
                os.remove(tmp)
                raise InterruptedError("Download cancelled")
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            out.write(chunk)
            done += len(chunk)
            if progress and total:
                progress(done / total, f"Downloading {name}: {done / 1e6:.1f} / {total / 1e6:.1f} MB")
    got = _sha256(tmp)
    if got != sha:
        os.remove(tmp)
        raise RuntimeError(f"{name} from {url} has an unexpected fingerprint ({got[:12]}...); "
                           "not used. The file may have been updated or the download damaged.")
    os.replace(tmp, final)
    return final


def available_backends() -> dict[str, Optional[str]]:
    """Map backend -> executable path (None if not found)."""
    found = {
        "python": sys.executable if python_backend_installed() else None,
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
                  image: str = DEFAULT_IMAGE, pkexec: bool = False,
                  threads: Optional[int] = None) -> list[str]:
    """Command line for one image. Container backends mount the input/output folders."""
    inp, out = os.path.abspath(inp), os.path.abspath(out)
    opts = []
    if no_csf:
        opts.append("--no-csf")
    if border is not None:
        opts += ["-b", f"{border:g}"]

    if backend == "python":
        # nipreps-synthstrip has no --no-csf flag: the no-CSF variant is a separate weights file,
        # and its border option takes whole millimetres.
        weights = find_weights(no_csf)
        if weights is None:
            raise FileNotFoundError(f"SynthStrip weights {WEIGHTS[no_csf][0]} not downloaded yet")
        cmd = [exe, "-m", "nipreps.synthstrip", "-i", inp, "-o", out, "--model", weights]
        if mask:
            cmd += ["-m", os.path.abspath(mask)]
        if border is not None:
            cmd += ["-b", str(int(round(border)))]
        if threads:
            cmd += ["-n", str(int(threads))]
        return cmd

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
    "  - Python only (no container): pip install \"niice[synthstrip]\" in NiiCE's environment\n"
    "    (tip: first pip install torch --index-url https://download.pytorch.org/whl/cpu)\n"
    "  - Docker or Podman, then: docker pull freesurfer/synthstrip\n"
    "  - FreeSurfer >= 7.3 (provides mri_synthstrip)\n"
    "  - Apptainer/Singularity (uses docker://freesurfer/synthstrip)\n"
    "To run Docker without sudo, add yourself to the docker group:\n"
    "  sudo usermod -aG docker $USER   (then log out and in)"
)
