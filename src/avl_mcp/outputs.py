"""Strict readers for the upstream AVL 3.52 MRF VERSION 1.0 records.

Raw files remain the authority. Labels preserve case (CL is lift; Cl is roll).
"""

import math
import re
from pathlib import Path

from .models import AVLFailure


def _lines(path: Path, kind: str) -> list[str]:
    if not path.is_file() or not path.stat().st_size:
        raise AVLFailure("MISSING_OUTPUT", f"Missing output: {path.name}")
    if path.stat().st_size > 64_000_000:
        raise AVLFailure("OUTPUT_TOO_LARGE", f"Output exceeds parser limit: {path.name}")
    lines = path.read_text(errors="strict").splitlines()
    if len(lines) < 3 or lines[0].strip() != kind or lines[1].strip() != "VERSION 1.0":
        raise AVLFailure("OUTPUT_FORMAT", f"Unsupported {kind} format in {path.name}")
    return lines


def numeric(text: str, count: int | None = None) -> list[float]:
    try:
        values = [float(v.replace("D", "E").replace("d", "e")) for v in text.split()]
        if not values or not all(math.isfinite(v) for v in values):
            raise ValueError
        if count is not None and len(values) != count:
            raise ValueError
    except ValueError as exc:
        raise AVLFailure(
            "INVALID_OUTPUT", f"Malformed or non-finite numeric record: {text[:120]}"
        ) from exc
    return values


def _records(lines):
    for i, line in enumerate(lines):
        if "|" in line:
            values, label = line.split("|", 1)
            if values.strip():
                yield i, numeric(values), label.strip()


def parse_total(path: Path, kind="TOT") -> dict:
    lines = _lines(path, kind)
    # The shared TOT preamble ends at DESIGN, before derivative tables.
    try:
        end = next(i for i, line in enumerate(lines) if line.strip() == "DESIGN")
        ncontrol_line = next(i for i, line in enumerate(lines) if line.strip() == "CONTROL")
    except StopIteration as exc:
        raise AVLFailure("OUTPUT_FORMAT", "Missing CONTROL/DESIGN records.") from exc
    records = list(_records(lines[:end]))
    fields = {}
    counts = {}
    for _, values, label in records:
        if label.startswith("# "):
            counts[label[2:]] = int(values[0])
        else:
            if label.startswith("Y Symmetry") or label.startswith("Z Symmetry"):
                continue
            keys = [v.strip() for v in label.removeprefix("Trefftz Plane:").strip().split(",")]
            if len(keys) != len(values):
                raise AVLFailure("OUTPUT_FORMAT", f"Unexpected record width: {label}")
            for key, value in zip(keys, values):
                if key in fields:
                    raise AVLFailure("OUTPUT_FORMAT", f"Duplicate total field: {key}")
                fields[key] = value
    required = [
        "Sref",
        "Cref",
        "Bref",
        "Xref",
        "Yref",
        "Zref",
        "Alpha",
        "Beta",
        "Mach",
        "pb/2V",
        "qc/2V",
        "rb/2V",
        "p'b/2V",
        "r'b/2V",
        "CXtot",
        "CYtot",
        "CZtot",
        "CLtot",
        "CDtot",
        "Cltot",
        "Cmtot",
        "Cntot",
        "Cl'tot",
        "Cn'tot",
        "CDvis",
        "CDind",
        "CLff",
        "CDff",
        "CYff",
        "e",
    ]
    if set(required) - fields.keys() or set(("surfaces", "strips", "vortices")) - counts.keys():
        raise AVLFailure("OUTPUT_FORMAT", "Incomplete total-force output.")
    n = int(numeric(lines[ncontrol_line + 1], 1)[0])
    controls = {}
    try:
        for line in lines[ncontrol_line + 2 : ncontrol_line + 2 + n]:
            value, name = line.strip().split(maxsplit=1)
            controls[name.strip()] = numeric(value, 1)[0]
    except ValueError as exc:
        raise AVLFailure("OUTPUT_FORMAT", "Malformed control values.") from exc
    if len(controls) != n or ncontrol_line + 2 + n != end:
        raise AVLFailure("OUTPUT_FORMAT", "Inconsistent control count.")
    orientation = next((x.strip() for x in lines if "axis orientation" in x), None)
    if orientation != "Standard axis orientation,  X fwd, Z down":
        raise AVLFailure("AXIS_MISMATCH", "Expected standard NASA axis orientation.")
    return {
        "format": "AVL MRF 1.0",
        "model_title": lines[3].strip(),
        "counts": counts,
        "fields": fields,
        "controls": controls,
        "axis_orientation": orientation,
    }


def parse_derivatives(path: Path, kind: str) -> dict:
    lines = _lines(path, kind)
    total = parse_total(path, kind)
    derivatives = {}
    control = {}
    design = {}
    control_names = []
    design_names = []
    diagnostics = {}
    for i, values, label in _records(lines):
        if label == "# control vars":
            n = int(values[0])
            control_names = [x.strip() for x in lines[i + 1 : i + 1 + n]]
        elif label == "# design vars":
            n = int(values[0])
            design_names = [x.strip() for x in lines[i + 1 : i + 1 + n]]
        elif ":" in label and not label.startswith("Trefftz Plane:"):
            names = label.split(":", 1)[1].strip()
            if names.endswith("d*") or names.endswith("g*"):
                variables = control_names if names.endswith("d*") else design_names
                target = control if names.endswith("d*") else design
                if len(variables) != len(values):
                    raise AVLFailure("OUTPUT_FORMAT", "Derivative variable count mismatch.")
                for name, value in zip(variables, values):
                    target.setdefault(name, {})[names[:-2]] = value
            else:
                keys = [x.strip() for x in names.split(",")]
                if len(keys) == len(values) and all(re.fullmatch(r"[A-Za-z]+", k) for k in keys):
                    derivatives.update(zip(keys, values))
        elif label.startswith("Neutral point"):
            diagnostics["neutral_point_x"] = None if abs(values[0]) >= 1e29 else values[0]
        elif label.startswith("Clb Cnr"):
            diagnostics["spiral_parameter"] = None if abs(values[0]) >= 1e29 else values[0]
    required = {"CLa", "Cma", "CLq", "Cmq"} if kind == "DERMATS" else {"CXu", "CZu", "Cmu"}
    if required - derivatives.keys():
        raise AVLFailure("OUTPUT_FORMAT", f"Incomplete {kind} derivative table.")
    if set(control_names) != set(total["controls"]) or (control_names and not control):
        raise AVLFailure("OUTPUT_FORMAT", "Incomplete control derivatives.")
    return {
        "axes": "standard stability" if kind == "DERMATS" else "standard body",
        "derivatives": derivatives,
        "control_derivatives": control,
        "design_derivatives": design,
        "diagnostics": diagnostics,
        "units": {
            "alpha_beta": "per radian (ST)",
            "rates": "per nondimensional rate pb/(2V), qc/(2V), rb/(2V) in table axes",
            "controls": "per CONTROL variable unit; local degrees = gain * variable",
            "body_velocity": "per normalized velocity component (SB)",
        },
    }


def parse_surfaces(path: Path) -> list[dict]:
    lines = _lines(path, "SURF")
    surfaces = []
    for i, line in enumerate(lines):
        if line.strip() == "SURFACE":
            if i + 3 >= len(lines):
                raise AVLFailure("OUTPUT_FORMAT", "Truncated surface output.")
            values = numeric(lines[i + 2].split("|", 1)[0], 10)
            local = numeric(lines[i + 3].split("|", 1)[0], 6)
            surfaces.append(
                {
                    "name": lines[i + 1].strip(),
                    "global_reference": dict(
                        zip(
                            ("index", "area", "CL", "CD", "Cm", "CY", "Cn", "Cl", "CDi", "CDv"),
                            values,
                        )
                    ),
                    "local_reference": dict(
                        zip(("index", "area", "chord", "cl", "cd", "cdv"), local)
                    ),
                }
            )
    expected = next((int(v[0]) for _, v, label in _records(lines) if label == "# surfaces"), -1)
    if not surfaces or len(surfaces) != expected:
        raise AVLFailure("OUTPUT_FORMAT", "Surface count mismatch.")
    return surfaces


def parse_strips(path: Path) -> list[dict]:
    lines = _lines(path, "STRP")
    surfaces = []
    for i, line in enumerate(lines):
        if line.strip() != "SURFACE":
            continue
        if i + 7 >= len(lines):
            raise AVLFailure("OUTPUT_FORMAT", "Truncated strip surface.")
        dims = numeric(lines[i + 2].split("|", 1)[0], 4)
        count = int(dims[2])
        header = [x.strip() for x in lines[i + 7].split(",")]
        expected = [
            "j",
            "Xle",
            "Yle",
            "Zle",
            "Chord",
            "Area",
            "c_cl",
            "ai",
            "cl_perp",
            "cl",
            "cd",
            "cdv",
            "cm_c/4",
            "cm_LE",
            "C.P.x/c",
        ]
        if header != expected or i + 8 + count > len(lines):
            raise AVLFailure("OUTPUT_FORMAT", "Unexpected or truncated strip table.")
        rows = [
            dict(zip(header, numeric(row, len(header)))) for row in lines[i + 8 : i + 8 + count]
        ]
        surfaces.append({"name": lines[i + 1].strip(), "surface_index": int(dims[0]), "rows": rows})
    expected_count = next((int(v[0]) for _, v, label in _records(lines) if label == "surfaces"), -1)
    if not surfaces or len(surfaces) != expected_count:
        raise AVLFailure("OUTPUT_FORMAT", "Strip surface count mismatch.")
    return surfaces
