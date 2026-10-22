"""Voxel-wise math between images using numpy syntax.

Each selected image gets a letter (a, b, c, ...). The expression is parsed with `ast` and only
array arithmetic is allowed: letters, numbers, operators, comparisons, `np.<function>` calls and a
few bare helpers (mean, where, ...). Imports, attribute access on anything but `np`, lambdas and
dunder names are rejected, so a typo cannot run arbitrary code.
"""
from __future__ import annotations

import ast
import string

import numpy as np

from .io import load, load_data, save_like

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


def run(expr: str, paths: list[str], out: str, dtype=None, ref_index: int = 0, progress=None):
    """Evaluate and save. Returns ('image', out_path) or ('value', result) for scalar results."""
    result = evaluate(expr, paths, progress)
    if result.ndim == 0 or result.size == 1 and result.ndim <= 1:
        return "value", result.item()
    ref = load(paths[ref_index])
    if result.ndim < 3 or result.shape[:3] != ref.shape[:3]:
        raise ExprError(
            f"The result has shape {result.shape}, which does not fit the grid of the reference "
            f"image {ref.shape[:3]}. Only results on the same voxel grid can be saved."
        )
    if result.dtype.kind == "f":
        result = result.astype(np.float32, copy=False)
    save_like(result, ref, out, dtype=dtype)
    return "image", out
