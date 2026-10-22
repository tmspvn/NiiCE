# NiiCE — NIfTI Context Extension

Right-click a NIfTI file (`.nii` / `.nii.gz`) in your file manager and get quick, GUI-driven tools:

| Menu | What it does | Needs |
|---|---|---|
| **Info** | Header and image properties (dimensions, voxel size, orientation, affine, intensity statistics, full raw header with field explanations). Select several images → **header comparison** table that highlights the fields that differ. | — |
| **Math** | Python/numpy code between images, which are named `a`, `b`, `c`…: one line (`a - b`) or a whole pasted script. Live syntax check, examples, single-number results are displayed instead of saved. | — |
| **Operations ▸** Binarise, Threshold, Extract ROI, Extract volume, Resample, Subtract | Plain numpy/scipy implementations (no FSL needed). Batch over all selected files. | — |
| **Operations ▸** Skull strip (SynthStrip) | FreeSurfer SynthStrip brain extraction. | `pip install ".[synthstrip]"` (no container), *or* Docker / Podman / Apptainer / FreeSurfer ≥ 7.3 |
| **Plot ▸** Make GIF, Histogram, Visualise alignment | Animated GIF with live preview; interactive histogram with optional mask and statistics; overlay of two images in world space with a same-space check. | — |
| **Registration ▸** Register, Apply transforms | ANTs `antsRegistrationSyN[Quick].sh` with an overview of what will happen, a starting-overlap preview and a progress bar; `antsApplyTransforms` with ready-made direct/inverse chains. | ANTs |
| **Settings** | Choose which tools appear in the menu, general options, plugins, which file managers get the menu. | — |

It is the successor of [neuro-nautilus](https://github.com/tmspvn/neuro-nautilus): one Python package
instead of bash scripts calling FSL, zenity and xterm.

## Install

On Linux, from the unzipped/cloned NiiCE folder:

```bash
./install.sh
```

It checks for missing system packages (Python venv support, the Qt library, and
`python3-nautilus`/`python-nemo` for a top-level right-click menu) and offers to install them with
apt, dnf or pacman (asking before it uses sudo). It then creates `~/.venvs/niice`, installs NiiCE
with skull stripping (CPU-only PyTorch), links the `niice` command into `~/.local/bin`, offers to
download the SynthStrip weights, and adds the right-click menu. The folder can be deleted
afterwards: NiiCE is copied into the environment.

* **Update:** unzip/pull the new version and run `./install.sh` again (reuses the environment).
* **Remove:** `./install.sh --uninstall` (add `--purge` to delete settings, plugins and weights too).
* Options: `--no-synthstrip` (skips PyTorch, ~1 GB less), `--gpu` (CUDA PyTorch), `--no-menu`,
  `--venv PATH`, `--dev` (editable install with test tools), `-y` (no questions), `--help`.

<details><summary>Manual installation</summary>

```bash
python3 -m venv ~/.venvs/niice && source ~/.venvs/niice/bin/activate
pip install --upgrade pip
pip install torch --index-url https://download.pytorch.org/whl/cpu   # only for skull stripping, see below
pip install ".[synthstrip]"             # from the NiiCE folder; drop "[synthstrip]" to skip it
niice install                           # adds the right-click menu to detected file managers
nautilus -q                             # restart Nautilus so it picks the menu up
```

</details>

**Skull stripping without containers.** The `synthstrip` extra installs
[nipreps-synthstrip](https://pypi.org/project/nipreps-synthstrip/) and PyTorch, so SynthStrip runs
directly in Python. The torch line above installs the CPU-only build (~200 MB); without it pip may
fetch the CUDA build, which is several GB. The trained weights are not part of that package: on
first use NiiCE asks to download them (~30 MB each, from the
[SynthStrip site](https://surfer.nmr.mgh.harvard.edu/docs/synthstrip/), MIT / CC BY 4.0) into
`~/.config/niice/models/` and checks their SHA-256 fingerprint. `niice synthstrip-weights` downloads
them in advance. A CPU run takes about a minute per image.

Use a virtual environment or conda env if you like: the menu entries call the Python that ran
`niice install`. Run `niice install` again after moving that environment.

`niice install --targets nautilus nemo caja dolphin` chooses file managers explicitly;
`niice uninstall` removes everything. On Ubuntu, Qt may need `sudo apt install libxcb-cursor0`.

### Platform support

| Platform | Status |
|---|---|
| Linux — Nautilus (GNOME Files), Nemo | Supported. **Top-level "NiiCE" menu** when the Python-extension plugin is installed (`sudo apt install python3-nautilus` / `python-nemo`, then `niice install` and `nautilus -q`); otherwise under *Scripts ▸ NiiCE*. |
| Linux — Caja | Supported, under *Scripts ▸ NiiCE*. |
| Linux — Dolphin (KDE) | Supported: service menu + a MIME type for `.nii`/`.nii.gz`. |
| Windows — Explorer | **Experimental, not yet tested on a real machine.** Cascading menu in the current user's registry (no admin). Shows on `.nii` and all `.gz` files (Windows only sees the last extension). |
| macOS | No installer yet. Create an Automator *Quick Action* (“Run Shell Script”, pass input *as arguments*) containing `/path/to/python -m niice run info "$@"`, one per tool. |

Every tool also runs from a terminal: `niice run <tool-id> files…` or the short form
`niice info my_t1.nii.gz my_t2.nii.gz`. No data at hand? `niice demo` writes four small example
images (a T1-like volume, a copy with a shifted header, a label image and a 4D series) into
`./niice_demo` and prints commands to try. `niice list` shows the ids. `niice info --text files…` prints the
header report without a window.

## Tools in more detail

### Info
One image: summary (with orthogonal thumbnails), intensity statistics (computed in the
background), full header with what each field means. Several images: one column per image,
differing rows highlighted, *show only differences* toggle. Numbers are compared with a tolerance
(`np.allclose`, relative 1e-5), so float rounding in the affine is not reported as a difference.
Copy or export to CSV.

**Same affine space?** With several images a one-line banner on top says whether they are on the
same voxel grid (each image compared with the first); hover over it for the details:

* green: same dimensions and affine, so voxel (i, j, k) is the same place in every image;
* blue: same world space, different grid (e.g. 1 mm T1 and 2 mm fMRI of one session). Viewers align
  them, but voxel-wise tools need *Resample → Match a reference image* first;
* amber warning: the fields of view barely or never overlap (not registered, or a broken header),
  **same dimensions but different affines** (voxel-wise math would run without error yet combine
  different locations), or one image is labelled as template space and the other is not.

The banner lists what differs: dimensions, voxel size, axis order (e.g. RAS vs LAS), rotation between
the grids in degrees, distance between the centres of the fields of view, and how much of the
smaller field of view overlaps. *Visualise alignment…* opens the overlay below. A single image gets
a warning when its own qform and sform disagree, because then different programs place it
differently. Math and Subtract show the same warning when their inputs are not on one grid.

### Math
Images are `a`, `b`, … in list order (drag to reorder). Write Python in the code box (dark
background, line numbers, auto-indent, Tab = 4 spaces, Ctrl+Enter runs). One line is enough:

```python
(a - b) / b * 100
```

or paste a whole script:

```python
mask = b > 0
mu, sd = a[mask].mean(), a[mask].std()
print("mean", mu, "sd", sd)                 # shown in the log
save(mask, "mask_only")                     # extra file next to 'Save as'
result = np.where(mask, (a - mu) / sd, 0)   # saved as 'Save as'
```

* Available (the **?** button lists them): `a`, `b`, … the images (float32 arrays), `np` (numpy),
  `nib` (nibabel), `ndi` (scipy.ndimage), `ref_img` (the reference image as a nibabel image, e.g.
  `ref_img.header.get_zooms()`); `import` works as usual.
* `result = …` is saved under *Save as*; without it, the value of the last line is used, like a
  notebook cell. A single number (e.g. `a[b > 0].mean()`) is shown instead of saved.
* `save(array, "name")` writes additional images on the reference grid; `print()` goes to the log.
* Combine masks with `&`, `|`, `~` (not `and`/`or`/`not`). Results are saved on the grid of the
  *reference* image; a warning appears if the images used are on different grids.
* Errors give the line number and highlight that line. The last code is remembered for next time;
  *Examples* has one-liners and longer snippets (z-score in a mask, several outputs, smoothing by
  FWHM, largest cluster, temporal SNR).

The code runs as normal, unrestricted Python on your computer, like running a script yourself:
only run code you trust.

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
* **Skull strip** — SynthStrip; also saves `_brainmask`; options *exclude CSF* and border. The
  backend is chosen automatically (Python `nipreps-synthstrip`, then native `mri_synthstrip`, then
  Docker, Podman, Apptainer) or fixed in Settings. With the Python backend *exclude CSF* uses the
  separate no-CSF weights and the border is in whole millimetres. Docker runs **without sudo** when you are in the `docker` group
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
* **Visualise alignment** — select two or more images (the first is the reference). Shows the
  same-space verdict, then sagittal/coronal/axial views of both images resampled onto one grid
  that covers *both* fields of view, so an image lying outside the other is still visible. Display
  as colour overlay (reference magenta, other image green; grey/white where both are bright, colour
  fringes where they are not), checkerboard, edges of the second image on the first, or a blend
  slider. Outlines show each field of view. Click a view to move the other two slices; the
  position is shown in mm. *Align by*: **world coordinates** places each image by its header (what
  viewers and ANTs see); **voxel indices** ignores the headers and puts voxel (i, j, k) on
  voxel (i, j, k), which is what voxel-wise math assumes. If the images match in one mode but not
  the other, the header is the problem, not the data.

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
* **General** — appearance (theme *System*/*Light*/*Dark* and an accent colour: swatches or any colour
  via *Custom…*, previewed live), output format (`same`/`.nii.gz`/`.nii`), open folder when done,
  default ANTs threads, preview size limit, SynthStrip backend/image, Docker via pkexec.
* **Plugins** — plugin folder, *Create example*, *Reload*, and load errors (a broken plugin never
  breaks the other tools).
* **Context menu** — which file managers get the menu.

*Save & update menu* rewrites the menu entries so they match the enabled tools.

**Large images.** Thumbnails, the GIF live preview and the registration overlap preview are skipped
for images above *Skip previews above* (default 64 million voxels per 3D volume, about
400 × 400 × 400; a 4D GIF preview counts all volumes). The tools themselves still work. Info only
computes intensity statistics after a click when an image has more than 4× that many voxels.
Previews that are drawn read every k-th voxel only (k chosen from the preview size), so even large
images stay light on memory.
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
