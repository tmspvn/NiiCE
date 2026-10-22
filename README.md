# NiiCE — NIfTI Context Extension

Right-click a NIfTI file (`.nii` / `.nii.gz`) in your file manager and get quick, GUI-driven tools:

| Menu | What it does | Needs |
|---|---|---|
| **Info** | Header and image properties (dimensions, voxel size, orientation, affine, intensity statistics, full raw header with field explanations). Select several images → **header comparison** table that highlights the fields that differ. | — |
| **Math** | Voxel-wise numpy expression between images, which are named `a`, `b`, `c`… Live syntax check, examples, single-number results are displayed instead of saved. | — |
| **Operations ▸** Binarise, Threshold, Extract ROI, Extract volume, Resample, Subtract | Plain numpy/scipy implementations (no FSL needed). Batch over all selected files. | — |
| **Operations ▸** Skull strip (SynthStrip) | FreeSurfer SynthStrip brain extraction. | Docker, Podman, Apptainer *or* FreeSurfer ≥ 7.3 |
| **Plot ▸** Make GIF, Histogram | Animated GIF with live preview; interactive histogram with optional mask and statistics. | — |
| **Registration ▸** Register, Apply transforms | ANTs `antsRegistrationSyN[Quick].sh` with an overview of what will happen, a starting-overlap preview and a progress bar; `antsApplyTransforms` with ready-made direct/inverse chains. | ANTs |
| **Settings** | Choose which tools appear in the menu, general options, plugins, which file managers get the menu. | — |

It is the successor of [neuro-nautilus](https://github.com/tmspvn/neuro-nautilus): one Python package
instead of bash scripts calling FSL, zenity and xterm.

## Install

```bash
pip install git+https://github.com/tmspvn/NiiCE    # or: pip install .  from a clone
niice install                                     # adds the right-click menu to detected file managers
nautilus -q                                       # restart Nautilus so it picks the menu up
```

Use a virtual environment or conda env if you like: the menu entries call the Python that ran
`niice install`. Run `niice install` again after moving that environment.

`niice install --targets nautilus nemo caja dolphin` chooses file managers explicitly;
`niice uninstall` removes everything. On Ubuntu, Qt may need `sudo apt install libxcb-cursor0`.

### Platform support

| Platform | Status |
|---|---|
| Linux — Nautilus (GNOME Files), Nemo, Caja | Supported: entries live in the file manager's `scripts` folder, under `NiiCE/`. |
| Linux — Dolphin (KDE) | Supported: service menu + a MIME type for `.nii`/`.nii.gz`. |
| Windows — Explorer | **Experimental, not yet tested on a real machine.** Cascading menu in the current user's registry (no admin). Shows on `.nii` and all `.gz` files (Windows only sees the last extension). |
| macOS | No installer yet. Create an Automator *Quick Action* (“Run Shell Script”, pass input *as arguments*) containing `/path/to/python -m niice run info "$@"`, one per tool. |

Every tool also runs from a terminal: `niice run <tool-id> files…` or the short form
`niice info a.nii.gz b.nii.gz`. `niice list` shows the ids. `niice info --text files…` prints the
header report without a window.

## Tools in more detail

### Info
One image: summary (with orthogonal thumbnails), intensity statistics (computed in the
background), full header with what each field means. Several images: one column per image,
differing rows highlighted, *show only differences* toggle. Numbers are compared with a tolerance
(`np.allclose`, relative 1e-5), so float rounding in the affine is not reported as a difference.
Copy or export to CSV.

### Math
Images are `a`, `b`, … in list order (drag to reorder). Any `np.<function>` works, plus bare
helpers (`mean`, `where`, `sqrt`, …) and constants `pi, e, nan, inf`. Use `&`, `|`, `~` to combine
masks (not `and`/`or`/`not`). The expression is checked structurally before running (no imports,
no file access, no attribute access except a whitelist such as `a.mean()`), so a typo cannot run
arbitrary code. The result is saved on the grid of the *reference* image; if it is a single
number (e.g. `np.mean(a[b > 0])`) it is shown instead. Data are loaded as float32.

### Operations
* **Binarise** — `value > threshold` → 1, else 0 (NaN → 0). Saved as uint8 `_mask`.
* **Threshold** — keep voxels in `[lower, upper]` (each bound optional, both inclusive), as image
  values or as percentiles of the non-zero voxels; keep values or binarise.
* **Extract ROI** — lists the labels in the image with voxel counts; tick labels (or type `17, 53`);
  one mask per label or all in one; optionally keep the label values.
* **Extract volume** — indices such as `0`, `0, 5, 10-20`, `-1` (last); one 4D file or one file per volume.
* **Resample** — to a voxel size, to a matrix size, or onto another image's grid. Interpolation:
  nearest neighbour (keeps integer labels), linear, cubic B-spline. For voxel/matrix size the
  field of view is kept: on each axis with scale `s = new voxel size / old voxel size`, new voxel
  `i` samples old voxel coordinate `s·i + (s − 1)/2`, so the outer edges of the first and last
  voxels stay in place (the same convention as most resampling tools).
* **Subtract** — `A − B` with a swap button; both on the same voxel grid.
* **Skull strip** — SynthStrip; also saves `_brainmask`; options `--no-csf` and border. The backend
  is chosen automatically (native `mri_synthstrip`, then Docker, Podman, Apptainer) or fixed in
  Settings. Docker runs **without sudo** when you are in the `docker` group
  (`sudo usermod -aG docker $USER`); otherwise NiiCE retries through `pkexec` (graphical password
  prompt). Podman needs no root at all.

Outputs go next to the input with a suffix (`sub-01_T1w_mask.nii.gz`). Existing files are never
overwritten silently: a `_1`, `_2`… is appended (or you are asked, when you typed the name).

### Plot
* **Make GIF** — 3D: sweep through slices of the chosen view; 4D: play time at one slice. Images are
  reoriented to RAS first so slices come out upright (turn off if you want raw voxel order);
  the in-plane voxel aspect ratio is respected. Options: intensity window (percentiles), colormap,
  magnification, rotation/flip, crop empty border, rewind, time bar, crosshair at a voxel
  `x, y, z` of the original file, every n-th frame, side-by-side layout for several images.
  The preview animates while you change settings. FPS is capped at 50 because GIF frame delays are
  in 1/100 s and browsers slow down anything faster.
* **Histogram** — one or more images overlaid; ignore zeros, log counts, density, percentile range,
  mask (any selected image or a file). Bin count defaults to the Freedman–Diaconis rule
  (bin width = 2·IQR·N^(−1/3), where IQR is the inter-quartile range and N the number of voxels).
  Table of N, mean, SD, percentiles. Save the figure from the toolbar.

### Registration (ANTs)
Select the fixed image first and the moving image second (or swap in the dialog). Before
running, the dialog shows:

* both images with thumbnails, size, voxel size and orientation;
* which stages will run — **Rigid** (6 parameters: 3 translations + 3 rotations) →
  **Affine** (12: adds scaling and shear) → **SyN** (a dense deformation field) — and when each is
  appropriate;
* the **starting overlap**: fixed (magenta) and moving (green) placed by their headers only. Grey
  means they overlap; colour fringes show the initial misalignment ANTs has to recover;
* every file that will be written, and the exact command.

Types: rigid (`-t r`), affine (`-t a`), SyN (`-t s`); Quick (`antsRegistrationSyNQuick.sh`) or
Thorough (`antsRegistrationSyN.sh`). Each run gets an ID and its own folder `coreg.<id>/`,
including `coreg.<id>.details` with the call and ready-to-paste `antsApplyTransforms` commands
for both directions.

**Progress bar.** ANTs does not report a percentage, so NiiCE estimates one. It reads the schedule
from the `antsRegistration` command the script prints: for every stage the iterations per level
(e.g. `1000x500x250x0`) and shrink factors (e.g. `8x4x2x1`). The cost of a level is taken as
`stage factor × (iterations + 1) / shrink³`, because shrinking by `f` divides the voxel count by
`f³`; SyN iterations are weighted 6× the linear ones. While ANTs runs, the bar follows
the `Current level = i of n` and per-iteration `DIAGNOSTIC` lines. A level that converges early is
credited in full when the next one starts, so the bar can jump forward but never goes backwards.
Treat the time remaining as a rough guide.

**Apply transforms** — input + reference image, a list of transforms with *invert* tick boxes, and
**Load from a registration…** which reads a `.details` file and fills in the direct
(moving → fixed) or inverse (fixed → moving) chain. ANTs applies the list bottom-up (the last
transform first). Use `GenericLabel`/`NearestNeighbor` for masks and atlases; 4D inputs are
detected and transformed volume by volume (`-e 3`). Selecting an image together with a
`.details` file pre-fills everything.

## Settings

`niice settings` or the **Settings…** menu entry:

* **Tools** — tick/untick each tool (built-ins and plugins); shows missing requirements (e.g. ANTs).
* **General** — output format (`same`/`.nii.gz`/`.nii`), open folder when done, default ANTs threads,
  SynthStrip backend/image, Docker via pkexec.
* **Plugins** — plugin folder, *Create example*, *Reload*, and load errors (a broken plugin never
  breaks the other tools).
* **Context menu** — which file managers get the menu.

*Save & update menu* rewrites the menu entries so they match the enabled tools.
Settings are stored in `~/.config/niice/settings.json` (Windows: `%APPDATA%\niice`).

## Plugins

A plugin is a Python file in the plugins folder (`~/.config/niice/plugins/`); the built-in tools are
registered with the same API (`src/niice/builtin.py`). There are two kinds:

**1. Image filter: NiiCE builds the dialog.** You write a function from image to image:

```python
from niice.plugins import image_filter, Float, Choice

@image_filter(id="mylab.smooth", menu="Operations/Gaussian smooth", suffix="_smooth",
              params={"fwhm_mm": Float(4.0, min=0, max=50, label="FWHM (mm)"),
                      "mode": Choice(choices=["nearest", "reflect"])})
def smooth(data, img, fwhm_mm, mode):
    import numpy as np
    from scipy.ndimage import gaussian_filter
    sigma = fwhm_mm / 2.3548 / np.array(img.header.get_zooms()[:3])  # FWHM = 2.3548 σ
    return gaussian_filter(data, list(sigma) + [0] * (data.ndim - 3), mode=mode)
```

`data` is the float32 voxel array, `img` the nibabel image. Return an array on the same grid (saved
with the input's header) or a nibabel image. Parameter types: `Float`, `Int`, `Bool`, `Choice`,
`Text`, `File` (each takes `label=` and `help=`). You get a file list, widgets, output suffix,
batch processing, a progress bar and error reporting without writing any of it.

**2. Tool: full control.** Your function receives the selected paths:

```python
from niice.plugins import tool

@tool(id="mylab.voxel_count", menu="Info/Voxel count", min_files=1)
def voxel_count(files):
    from niice.gui.common import message
    ...
    message("Voxel count", text)
```

Options for both: `min_files`, `max_files`, `nifti_only` (default True), `requires` (executables
shown as missing in Settings), `order` (position in its submenu). For dialogs, subclass
`niice.gui.common.ToolDialog` (`build()`, `validate()`, `task(report)`, which runs in a background
thread with a progress bar and cancel button) like the built-in tools do.
`niice.core.*` contains plain functions (`io.load`, `io.save_like`, `io.out_path`, `ops.resample`, …)
that plugins can reuse.

After adding a file: Settings → Plugins → **Reload**, then **Save & update menu**. Packages can
ship plugins too, through an entry point:

```toml
[project.entry-points."niice.plugins"]
mylab = "mylab_niice"     # module imported at start-up (or a function, which is called)
```

Examples: [`examples/plugins/`](examples/plugins).

## Development

```bash
pip install -e ".[test]"
pytest
```

The core (`src/niice/core`) has no GUI dependency and is unit-tested on small synthetic images;
the GUI lives in `src/niice/gui`, one module per menu section.
