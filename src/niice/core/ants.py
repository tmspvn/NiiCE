"""ANTs registration: command building, output bookkeeping and progress estimation.

Requires ANTs on PATH (or $ANTSPATH): antsRegistrationSyN.sh, antsRegistrationSyNQuick.sh,
antsApplyTransforms.
"""
from __future__ import annotations

import os
import re
import secrets
import shutil
from dataclasses import dataclass, field
from typing import Optional, Sequence

RECORD_EXT = ".niicereg"                 # file describing a registration (call + apply commands)
RECORD_EXTS = (RECORD_EXT, ".details")  # .details: older NiiCE / neuro-nautilus files
SCRIPTS = {"quick": "antsRegistrationSyNQuick.sh", "full": "antsRegistrationSyN.sh"}

# -t option of antsRegistrationSyN[Quick].sh -> (label, stages, explanation)
TRANSFORMS = {
    "r": ("Rigid", ["Rigid"], "6 degrees of freedom: 3 translations + 3 rotations. "
                              "Same subject, same shape (e.g. T1 -> T2 of one session)."),
    "a": ("Affine", ["Rigid", "Affine"], "12 degrees of freedom: adds scaling and shearing. "
                                         "Different head sizes, coarse alignment to a template."),
    "s": ("Non-linear (SyN)", ["Rigid", "Affine", "SyN"],
          "Rigid + affine, then a deformable warp (SyN) that can bend the anatomy locally. "
          "Subject -> template, or between subjects."),
}

STAGE_HELP = {
    "Rigid": "rotate + shift",
    "Affine": "+ scale + shear",
    "SyN": "local deformation",
}


def find(executable: str) -> Optional[str]:
    path = shutil.which(executable)
    if path:
        return path
    ants = os.environ.get("ANTSPATH")
    if ants:
        cand = os.path.join(ants, executable)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def ants_available() -> bool:
    return find(SCRIPTS["quick"]) is not None and find("antsApplyTransforms") is not None


def new_id() -> str:
    return secrets.token_hex(3)[:5]


def registration_command(fixed: str, moving: str, prefix: str, transform: str = "s",
                         quick: bool = True, threads: int = 3, dim: int = 3) -> list[str]:
    script = find(SCRIPTS["quick" if quick else "full"]) or SCRIPTS["quick" if quick else "full"]
    return [script, "-d", str(dim), "-t", transform, "-f", fixed, "-m", moving,
            "-o", prefix, "-n", str(threads)]


def outputs(prefix: str, transform: str) -> list[tuple[str, str]]:
    """Files written by antsRegistrationSyN[Quick].sh, with explanations."""
    out = [(prefix + "0GenericAffine.mat", "linear part of the transform (moving -> fixed)")]
    if transform == "s":
        out += [(prefix + "1Warp.nii.gz", "deformation field (moving -> fixed)"),
                (prefix + "1InverseWarp.nii.gz", "inverse deformation field (fixed -> moving)")]
    out += [(prefix + "Warped.nii.gz", "moving image resampled into fixed space"),
            (prefix + "InverseWarped.nii.gz", "fixed image resampled into moving space")]
    return out


def transform_chain(prefix: str, transform: str, inverse: bool = False) -> list[tuple[str, bool]]:
    """Transforms for antsApplyTransforms as (file, invert) in the order ANTs expects.

    ANTs applies the listed transforms like a stack: the LAST one is applied first.
      moving -> fixed:  -t 1Warp -t 0GenericAffine
      fixed -> moving:  -t [0GenericAffine, 1] -t 1InverseWarp
    """
    aff = prefix + "0GenericAffine.mat"
    if not inverse:
        return ([(prefix + "1Warp.nii.gz", False)] if transform == "s" else []) + [(aff, False)]
    return [(aff, True)] + ([(prefix + "1InverseWarp.nii.gz", False)] if transform == "s" else [])


def apply_command(moving: str, reference: str, out: str, transforms: Sequence[tuple[str, bool]],
                  interpolation: str = "Linear", dim: int = 3, timeseries: bool = False) -> list[str]:
    exe = find("antsApplyTransforms") or "antsApplyTransforms"
    cmd = [exe, "-d", str(dim)]
    if timeseries:
        cmd += ["-e", "3"]
    cmd += ["-i", moving, "-r", reference, "-o", out, "-n", interpolation, "-v", "1"]
    for path, inv in transforms:
        cmd += ["-t", f"[{path},1]" if inv else path]
    return cmd


def details_text(reg_id: str, fixed: str, moving: str, prefix: str, transform: str,
                 command: Sequence[str]) -> str:
    """Contents of coreg.<id>.niicereg: what was run and how to apply the result."""
    def q(parts):
        return " ".join(f'"{p}"' if " " in p else p for p in parts)

    direct = apply_command(moving, fixed, prefix + "ApplyWarp.Mov_to_Fixed.nii.gz",
                           transform_chain(prefix, transform))
    inverse = apply_command(fixed, moving, prefix + "ApplyINVWarp.Fixed_to_Moving.nii.gz",
                            transform_chain(prefix, transform, inverse=True))
    return "\n".join([
        f"Registration ID: {reg_id}",
        f"Type: {TRANSFORMS[transform][0]} (-t {transform})",
        f"Fixed img: {fixed}",
        f"Moving img: {moving}",
        f"Prefix: {prefix}",
        "Call:",
        q(command),
        "",
        f"Apply transform DIRECT Mov -> Fix: {os.path.basename(moving)}  ->  {os.path.basename(fixed)}",
        q(direct),
        "",
        f"Apply transform INVERSE Fix -> Mov: {os.path.basename(fixed)}  ->  {os.path.basename(moving)}",
        q(inverse),
        "",
    ])


def parse_details(path: str) -> dict:
    """Read back a .niicereg file (or an older .details file from NiiCE / neuro-nautilus)."""
    info = {}
    with open(path) as f:
        for line in f:
            for key, name in (("Registration ID:", "id"), ("Fixed img:", "fixed"),
                              ("Moving img:", "moving"), ("Prefix:", "prefix")):
                if line.startswith(key):
                    info[name] = line[len(key):].strip()
            if line.startswith("Type:"):
                m = re.search(r"-t (\w)", line)
                if m:
                    info["transform"] = m.group(1)
    if "prefix" not in info:  # neuro-nautilus files: prefix = path without "details"
        ext = next((e for e in RECORD_EXTS if path.endswith(e)), "")
        info["prefix"] = path[: len(path) - len(ext) + 1] if ext else path
    if "transform" not in info:
        info["transform"] = "s" if os.path.exists(info["prefix"] + "1Warp.nii.gz") else "a"
    return info


# --------------------------------------------------------------------------- progress
_RE_STAGE = re.compile(r"\*\*\* Running (\S+) registration")
_RE_LEVEL = re.compile(r"Current level = (\d+) of (\d+)")
_RE_ITERS = re.compile(r"number of iterations = (\d+)")
_RE_DIAG = re.compile(r"DIAGNOSTIC,\s*(\d+),")
_RE_TRANSFORM = re.compile(r"--transform\s+(\w+)\s*\[")
_RE_CONV = re.compile(r"--convergence\s+\[\s*([\dx]+)")
_RE_SHRINK = re.compile(r"--shrink-factors\s+([\dx]+)")

# Relative cost of one iteration at full resolution. SyN updates a dense field (and the CC
# metric is expensive), so it is much slower per iteration than the linear stages.
_STAGE_COST = {"Rigid": 1.0, "Translation": 1.0, "Affine": 1.0, "SyN": 6.0, "BSplineSyN": 6.0}


@dataclass
class Stage:
    name: str
    iterations: list[int]
    shrink: list[int]

    def weights(self) -> list[float]:
        # Cost of a level ~ iterations x voxels; voxels shrink with the cube of the factor.
        # +1 iteration per level covers smoothing/initialisation (levels with 0 iterations).
        c = _STAGE_COST.get(self.name, 1.0)
        return [c * (it + 1) / (s ** 3) for it, s in zip(self.iterations, self.shrink)]


@dataclass
class RegistrationProgress:
    """Feed it the output lines of antsRegistrationSyN*.sh; read .fraction and .status.

    The schedule (stages, iterations per level, shrink factors) is taken from the antsRegistration
    command the script prints. Within a level progress grows with the iteration count; when a
    level converges early, the rest of it is skipped at the start of the next level. The result is
    an estimate of the fraction of compute done, not of wall time.
    """
    stages: list[Stage] = field(default_factory=list)
    stage: int = -1
    level: int = 0
    n_levels: int = 0
    iteration: int = 0
    level_iters: int = 0
    fraction: float = 0.0
    status: str = "Starting ANTs"

    def feed(self, line: str) -> None:
        if not self.stages and "--transform" in line and "--convergence" in line:
            self._parse_command(line)
        m = _RE_STAGE.search(line)
        if m:
            self.stage += 1
            self.level, self.n_levels, self.iteration, self.level_iters = 0, 0, 0, 0
            self._update()
            return
        m = _RE_LEVEL.search(line)
        if m:
            self.level, self.n_levels = int(m.group(1)) - 1, int(m.group(2))
            self.iteration, self.level_iters = 0, 0
            self._update()
            return
        m = _RE_ITERS.search(line)
        if m:
            self.level_iters = int(m.group(1))
            return
        m = _RE_DIAG.search(line)
        if m:
            self.iteration = int(m.group(1))
            self._update()

    def _parse_command(self, line: str) -> None:
        names = _RE_TRANSFORM.findall(line)
        convs = _RE_CONV.findall(line)
        shrinks = _RE_SHRINK.findall(line)
        if names and len(names) == len(convs) == len(shrinks):
            self.stages = [Stage(n, [int(v) for v in c.split("x")], [int(v) for v in s.split("x")])
                           for n, c, s in zip(names, convs, shrinks)]

    def _update(self) -> None:
        if self.stage < 0:
            return
        name = self.stages[self.stage].name if self.stage < len(self.stages) else "stage"
        lvl = f" - level {self.level + 1}/{self.n_levels}" if self.n_levels else ""
        it = f" - iteration {self.iteration}/{self.level_iters}" if self.level_iters else ""
        self.status = f"Stage {self.stage + 1}/{len(self.stages) or '?'}: {name}{lvl}{it}"
        if not self.stages:  # schedule unknown: equal share per stage seen so far
            self.fraction = min(0.99, (self.stage + 0.5) / (self.stage + 2))
            return
        weights = [s.weights() for s in self.stages]
        total = sum(sum(w) for w in weights)
        done = sum(sum(w) for w in weights[: self.stage])
        if self.stage < len(weights):
            w = weights[self.stage]
            done += sum(w[: self.level])
            if self.level < len(w):
                iters = max(self.level_iters or self.stages[self.stage].iterations[self.level], 1)
                done += w[self.level] * min(self.iteration / iters, 1.0)
        self.fraction = max(self.fraction, min(done / total, 0.999))
