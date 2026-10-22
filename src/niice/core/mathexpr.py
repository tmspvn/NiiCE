"""Voxel-wise math between images using numpy syntax.

Each selected image gets a letter (a, b, c, ...). The expression is parsed with `ast` and only
array arithmetic is allowed: letters, numbers, operators, comparisons, `np.<function>` calls and a
few bare helpers (mean, where, ...). Imports, attribute access on anything but `np`, lambdas and
dunder names are rejected, so a typo cannot run arbitrary code.
"""
from __future__ import annotations

import ast
import os
import string

import numpy as np

from .io import is_nifti, load, load_data, save_like

LETTERS = string.ascii_lowercase

# Bare names usable without the `np.` prefix.
HELPERS = {
    name: getattr(np, name)
    for name in (
        "abs", "sqrt", "exp", "log", "log10", "log2", "sin", "cos", "tan", "arctan2",
        "where", "clip", "maximum", "minimum", "mean", "median", "std", "var", "sum", "prod",
        "nanmean", "nanmedian", "nanstd", "nansum", "nanmax", "nanmin", "max", "min",
        "isnan", "isfinite", "nan_to_num", "round", "floor", "ceil", "sign", "percentile",
        "logical_and", "logical_or", "logical_not", "logical_xor", "isin", "stack",
        "concatenate", "power",
    )
}
# Methods/properties allowed on arrays, e.g. a.mean(axis=3) or a.T
ARRAY_ATTRS = {
    "mean", "std", "var", "sum", "prod", "max", "min", "T", "astype", "reshape", "squeeze",
    "shape", "ndim", "size", "round", "clip", "any", "all", "ravel", "flatten", "copy",
    "cumsum", "argmax", "argmin", "transpose", "real", "imag", "dtype",
}
CONSTANTS = {"pi": np.pi, "e": np.e, "nan": np.nan, "inf": np.inf, "True": True, "False": False}

_ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.Call, ast.Name,
    ast.Load, ast.Constant, ast.Attribute, ast.Subscript, ast.Slice, ast.Tuple, ast.List,
    ast.IfExp, ast.keyword, ast.operator, ast.unaryop, ast.cmpop, ast.boolop, ast.Starred,
)

EXAMPLES = [
    ("a + b", "sum of two images"),
    ("a - b", "difference"),
    ("a * (b > 0)", "mask a with b"),
    ("(a > 100) & (a < 200)", "binary mask of a range (use & | ~, not and/or/not)"),
    ("np.where(a > 0, a, nan)", "set zeros to NaN"),
    ("a / np.mean(a[b > 0])", "normalise a by its mean inside mask b"),
    ("np.mean(a, axis=3)", "temporal mean of a 4D image"),
    ("np.std(a, axis=3) / np.mean(a, axis=3)", "coefficient of variation (4D)"),
    ("a[..., 0]", "first volume of a 4D image"),
    ("np.mean(a[b > 0])", "a number: mean of a inside mask b (shown, not saved)"),
]


class ExprError(ValueError):
    pass


def validate(expr: str, n_images: int) -> list[str]:
    """Check the expression; return the image letters it uses. Raises ExprError with a hint."""
    expr = expr.strip()
    if not expr:
        raise ExprError("Type an expression, e.g. a + b")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ExprError(f"Syntax error: {exc.msg} (column {exc.offset})") from None
    allowed_letters = set(LETTERS[:n_images])
    used = []
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ExprError(f"'{type(node).__name__}' is not allowed in expressions")
        if isinstance(node, ast.Attribute):
            if not (isinstance(node.value, ast.Name) and node.value.id in ("np", "numpy")):
                if node.attr not in ARRAY_ATTRS:
                    raise ExprError(f"'.{node.attr}' is not allowed (try np.{node.attr}(...))")
            elif node.attr.startswith("_") or not hasattr(np, node.attr):
                raise ExprError(f"numpy has no function '{node.attr}'")
        if isinstance(node, ast.Name):
            name = node.id
            if name in ("np", "numpy") or name in HELPERS or name in CONSTANTS:
                continue
            if len(name) == 1 and name in LETTERS:
                if name not in allowed_letters:
                    last = LETTERS[n_images - 1]
                    raise ExprError(f"'{name}' is not an image: only a..{last} are selected")
                if name not in used:
                    used.append(name)
                continue
            raise ExprError(f"Unknown name '{name}'")
    if not used:
        raise ExprError("The expression does not use any image (a, b, ...)")
    return used


def evaluate(expr: str, paths: list[str], progress=None):
    """Evaluate `expr` with a, b, ... bound to the images' data (float32)."""
    used = validate(expr, len(paths))
    namespace = {"np": np, "numpy": np, **HELPERS, **CONSTANTS}
    for i, letter in enumerate(used):
        if progress:
            progress(i / (len(used) + 1), f"Loading {letter}")
        namespace[letter] = load_data(load(paths[LETTERS.index(letter)]))
    if progress:
        progress(len(used) / (len(used) + 1), "Computing")
    with np.errstate(divide="ignore", invalid="ignore"):
        result = eval(compile(ast.parse(expr.strip(), mode="eval"), "<expr>", "eval"),
                      {"__builtins__": {}}, namespace)
    return np.asarray(result)


def _check_grid(result: np.ndarray, ref) -> None:
    if result.ndim < 3 or result.shape[:3] != ref.shape[:3]:
        raise ExprError(
            f"The result has shape {result.shape}, which does not fit the grid of the reference "
            f"image {ref.shape[:3]}. Only results on the same voxel grid can be saved."
        )


def _is_scalar(result: np.ndarray) -> bool:
    return result.ndim == 0 or (result.size == 1 and result.ndim <= 1)


def _save_result(result: np.ndarray, ref, out: str, dtype=None) -> str:
    _check_grid(result, ref)
    if result.dtype.kind == "f":
        result = result.astype(np.float32, copy=False)
    return save_like(result, ref, out, dtype=dtype)


def run(expr: str, paths: list[str], out: str, dtype=None, ref_index: int = 0, progress=None):
    """Evaluate and save. Returns ('image', out_path) or ('value', result) for scalar results."""
    result = evaluate(expr, paths, progress)
    if _is_scalar(result):
        return "value", result.item()
    return "image", _save_result(result, load(paths[ref_index]), out, dtype)


# --------------------------------------------------------------------------- multi-line code
CODE_HELP = (
    "Python code. Images are a, b, c ... (float32 numpy arrays); np = numpy, ndi = scipy.ndimage, "
    "nib = nibabel, ref_img = the reference image (e.g. ref_img.header.get_zooms()). "
    "Set result = ... to save it as 'Save as' (or end with an expression, like a notebook cell). "
    "save(array, 'name') writes more images on the reference grid; print() goes to the log."
)

CODE_EXAMPLES = [
    ("z-score inside a mask", """\
mask = b > 0
mu, sd = a[mask].mean(), a[mask].std()
print("mean", mu, "sd", sd)
result = np.where(mask, (a - mu) / sd, 0)
"""),
    ("several outputs with save()", """\
mask = a > np.percentile(a[a > 0], 50)
save(mask, "upper_half_mask")          # extra file next to 'Save as'
save(ndi.binary_erosion(mask), "eroded_mask")
result = a * mask
"""),
    ("smooth with a 4 mm FWHM Gaussian", """\
fwhm_mm = 4
zooms = np.array(ref_img.header.get_zooms()[:3])
sigma = fwhm_mm / 2.3548 / zooms        # FWHM = 2.3548 * sigma, converted mm -> voxels
result = ndi.gaussian_filter(a, sigma)
"""),
    ("largest connected cluster of a mask", """\
labels, n = ndi.label(a > 0)
sizes = np.bincount(labels.ravel())[1:]
print(n, "clusters; largest has", sizes.max(), "voxels")
result = labels == (np.argmax(sizes) + 1)
"""),
    ("temporal SNR of a 4D series", """\
mean = a.mean(axis=3)
sd = a.std(axis=3)
result = np.divide(mean, sd, out=np.zeros_like(mean), where=sd > 0)
"""),
]


def prepare_code(code: str, n_images: int):
    """Parse multi-line code. Returns (tree, letters to load, sets_result).

    If `result` is never assigned and the last statement is an expression, that expression
    becomes the result (like the last line of a notebook cell).
    Image letters that are read but never assigned must be among the selected images.
    """
    if not code.strip():
        raise ExprError("Write some code")
    try:
        tree = ast.parse(code, "<code>", "exec")
    except SyntaxError as exc:
        raise ExprError(f"Syntax error on line {exc.lineno}: {exc.msg}") from None
    loads, stores = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            (stores if isinstance(node.ctx, (ast.Store, ast.Del)) else loads).add(node.id)
        elif isinstance(node, ast.arg):
            stores.add(node.arg)
    sets_result = "result" in stores
    if not sets_result and tree.body and isinstance(tree.body[-1], ast.Expr):
        last = tree.body[-1]
        tree.body[-1] = ast.copy_location(
            ast.Assign(targets=[ast.Name("result", ast.Store())], value=last.value), last)
        ast.fix_missing_locations(tree)
        sets_result = True
    letters = sorted(n for n in loads if len(n) == 1 and n in LETTERS)
    missing = [l for l in letters if LETTERS.index(l) >= n_images and l not in stores]
    if missing:
        last = LETTERS[n_images - 1] if n_images else "a"
        raise ExprError(f"'{missing[0]}' is used but is not an image: only a..{last} are selected")
    to_load = [l for l in letters if LETTERS.index(l) < n_images]
    return tree, to_load, sets_result


def run_code(code: str, paths: list[str], out: str, dtype=None, ref_index: int = 0,
             progress=None, log=print) -> dict:
    """Run multi-line code. Returns {'value': scalar or None, 'files': [written paths]}."""
    import traceback

    import nibabel as nib
    from scipy import ndimage

    tree, to_load, _ = prepare_code(code, len(paths))
    ref = load(paths[ref_index])
    outdir = os.path.dirname(os.path.abspath(out))
    files: list[str] = []

    def save(array, name, dtype=None):
        """Save an array on the reference grid; `name` relative to the output folder."""
        path = name if os.path.isabs(name) else os.path.join(outdir, name)
        if not is_nifti(path):
            path += ".nii.gz"
        written = _save_result(np.asarray(array), ref, path, dtype)
        files.append(written)
        log(f"saved {written}")
        return written

    def _print(*args, sep=" ", end="", **_):
        log(sep.join(str(a) for a in args) + (end if end != "\n" else ""))

    ns = {"np": np, "numpy": np, "ndi": ndimage, "nib": nib, "ref_img": ref, "save": save,
          "print": _print, "pi": np.pi, "nan": np.nan, "inf": np.inf, "__name__": "__niice__"}
    for i, letter in enumerate(to_load):
        if progress:
            progress(i / (len(to_load) + 1), f"Loading {letter}")
        ns[letter] = load_data(load(paths[LETTERS.index(letter)]))
    if progress:
        progress(len(to_load) / (len(to_load) + 1), "Running code")
    try:
        with np.errstate(divide="ignore", invalid="ignore"):
            exec(compile(tree, "<code>", "exec"), ns)
    except ExprError:
        raise
    except Exception as exc:
        frames = [f for f in traceback.extract_tb(exc.__traceback__) if f.filename == "<code>"]
        where = f"line {frames[-1].lineno}: " if frames else ""
        line = code.splitlines()[frames[-1].lineno - 1].strip() if frames else ""
        raise ExprError(f"{where}{type(exc).__name__}: {exc}" + (f"\n    {line}" if line else "")) from exc

    result = ns.get("result")
    if result is None:
        if not files:
            raise ExprError("Nothing to save: set result = ... (or call save(array, 'name'))")
        return {"value": None, "files": files}
    arr = np.asarray(result)
    if _is_scalar(arr):
        return {"value": arr.item(), "files": files}
    files.insert(0, _save_result(arr, ref, out, dtype))
    return {"value": None, "files": files}
