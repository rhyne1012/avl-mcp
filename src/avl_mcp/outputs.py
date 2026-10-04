"""Strict readers for the upstream AVL 3.52 MRF VERSION 1.0 records.

Raw files remain the authority. Labels preserve case (CL is lift; Cl is roll).
"""

import math
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
            yield i, numeric(values), label.strip()


def _count(value, label, minimum=0):
    if value != int(value) or value < minimum:
        raise AVLFailure("OUTPUT_FORMAT", f"Invalid integer count/index: {label}")
    return int(value)


def _marker(lines, label):
    positions = [i for i, line in enumerate(lines) if line.strip() == label]
    if len(positions) != 1:
        raise AVLFailure("OUTPUT_FORMAT", f"Expected one {label} record.")
    return positions[0]


def _named_values(lines, start, label):
    if start + 1 >= len(lines):
        raise AVLFailure("OUTPUT_FORMAT", f"Missing {label} count.")
    n = _count(numeric(lines[start + 1], 1)[0], label)
    end = start + 2 + n
    if end > len(lines):
        raise AVLFailure("OUTPUT_FORMAT", f"Truncated {label} values.")
    result = {}
    for row in lines[start + 2 : end]:
        parts = row.strip().split(maxsplit=1)
        if len(parts) != 2 or parts[1] in result or "|" in parts[1]:
            raise AVLFailure("OUTPUT_FORMAT", f"Invalid/duplicate {label} variable.")
        result[parts[1]] = numeric(parts[0], 1)[0]
    return result, end


def _orientation(lines):
    orientations = [x.strip() for x in lines if "axis orientation" in x]
    expected = "Standard axis orientation,  X fwd, Z down"
    if orientations != [expected]:
        raise AVLFailure("AXIS_MISMATCH", "Expected one standard NASA axis orientation.")
    return expected


def parse_total(path: Path, kind="TOT") -> dict:
    lines = _lines(path, kind)
    total, _, end = _preamble(lines)
    if kind == "TOT" and any(line.strip() for line in lines[end:]):
        raise AVLFailure("OUTPUT_FORMAT", "Unexpected data after total-force output.")
    return total


def _preamble(lines):
    ncontrol_line = _marker(lines, "CONTROL")
    ndesign_line = _marker(lines, "DESIGN")
    controls, control_end = _named_values(lines, ncontrol_line, "CONTROL")
    designs, end = _named_values(lines, ndesign_line, "DESIGN")
    if control_end != ndesign_line:
        raise AVLFailure("OUTPUT_FORMAT", "Inconsistent control count.")
    records = list(_records(lines[:ncontrol_line]))
    fields = {}
    counts = {}
    for _, values, label in records:
        if label.startswith("# "):
            key = label[2:]
            if len(values) != 1 or key in counts:
                raise AVLFailure("OUTPUT_FORMAT", f"Invalid/duplicate count: {label}")
            counts[key] = _count(values[0], label, 1)
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
    orientation = _orientation(lines)
    return (
        {
            "format": "AVL MRF 1.0",
            "model_title": lines[3].strip(),
            "counts": counts,
            "fields": fields,
            "controls": controls,
            "axis_orientation": orientation,
        },
        designs,
        end,
    )


def parse_derivatives(path: Path, kind: str) -> dict:
    if kind not in ("DERMATS", "DERMATB"):
        raise AVLFailure("OUTPUT_FORMAT", "Expected DERMATS or DERMATB.")
    lines = _lines(path, kind)
    total, design_values, end = _preamble(lines)
    coefficients = (
        {"CL", "CY", "CD", "Cl", "Cm", "Cn"}
        if kind == "DERMATS"
        else {"CX", "CY", "CZ", "Cl", "Cm", "Cn"}
    )
    variables = "abpqr" if kind == "DERMATS" else "uvwpqr"
    required = {c + v for c in coefficients for v in variables}
    variable_coefficients = coefficients | ({"CDff", "e"} if kind == "DERMATS" else set())
    derivatives = {}
    control = {}
    design = {}
    declared = {}
    group_fields = {"d": set(), "g": set()}
    diagnostics = {}
    tail = lines[end:]
    for i, values, label in _records(tail):
        if label in ("# control vars", "# design vars"):
            group = "d" if label == "# control vars" else "g"
            if group in declared or len(values) != 1:
                raise AVLFailure("OUTPUT_FORMAT", f"Invalid/duplicate {label}.")
            n = _count(values[0], label)
            names = [x.strip() for x in tail[i + 1 : i + 1 + n]]
            expected = list(total["controls"] if group == "d" else design_values)
            if len(names) != n or names != expected:
                raise AVLFailure("OUTPUT_FORMAT", f"Inconsistent {label} names/count/order.")
            declared[group] = names
        elif ":" in label:
            names = label.split(":", 1)[1].strip()
            if names.endswith("d*") or names.endswith("g*"):
                group, coefficient = names[-2], names[:-2]
                variable_names = declared.get(group, [])
                target = control if group == "d" else design
                if (
                    not variable_names
                    or len(variable_names) != len(values)
                    or coefficient not in variable_coefficients
                    or coefficient in group_fields[group]
                ):
                    raise AVLFailure("OUTPUT_FORMAT", "Derivative variable count mismatch.")
                group_fields[group].add(coefficient)
                for name, value in zip(variable_names, values):
                    target.setdefault(name, {})[coefficient] = value
            else:
                keys = [x.strip() for x in names.split(",")]
                if (
                    len(keys) != len(values)
                    or len(set(keys)) != len(keys)
                    or set(keys) - required
                    or set(keys) & derivatives.keys()
                ):
                    raise AVLFailure("OUTPUT_FORMAT", f"Invalid/duplicate derivative row: {names}")
                derivatives.update(zip(keys, values))
        elif label.startswith("Neutral point"):
            if len(values) != 1 or "neutral_point_x" in diagnostics:
                raise AVLFailure("OUTPUT_FORMAT", "Invalid/duplicate neutral point.")
            diagnostics["neutral_point_x"] = None if abs(values[0]) >= 1e29 else values[0]
        elif label.startswith("Clb Cnr"):
            if len(values) != 1 or "spiral_parameter" in diagnostics:
                raise AVLFailure("OUTPUT_FORMAT", "Invalid/duplicate spiral parameter.")
            diagnostics["spiral_parameter"] = None if abs(values[0]) >= 1e29 else values[0]
        else:
            raise AVLFailure("OUTPUT_FORMAT", f"Unexpected derivative record: {label}")
    if required != derivatives.keys():
        raise AVLFailure("OUTPUT_FORMAT", f"Incomplete {kind} derivative table.")
    for group in ("d", "g"):
        if group not in declared or (
            declared[group] and group_fields[group] != variable_coefficients
        ):
            raise AVLFailure("OUTPUT_FORMAT", f"Incomplete {group} variable derivatives.")
    expected_diagnostics = {"neutral_point_x", "spiral_parameter"} if kind == "DERMATS" else set()
    if diagnostics.keys() != expected_diagnostics:
        raise AVLFailure("OUTPUT_FORMAT", "Incomplete/unexpected derivative diagnostics.")
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
    expected = _load_header(lines, "# surfaces")[0]
    surfaces = []
    for i, line in enumerate(lines):
        if line.strip() == "SURFACE":
            if i + 3 >= len(lines):
                raise AVLFailure("OUTPUT_FORMAT", "Truncated surface output.")
            values = numeric(lines[i + 2].split("|", 1)[0], 10)
            local = numeric(lines[i + 3].split("|", 1)[0], 6)
            index = _count(values[0], "surface index", 1)
            if index != len(surfaces) + 1 or local[0] != index or not lines[i + 1].strip():
                raise AVLFailure("OUTPUT_FORMAT", "Inconsistent surface indices/name.")
            if i + 4 < len(lines) and lines[i + 4].strip() not in ("", "SURFACE"):
                raise AVLFailure("OUTPUT_FORMAT", "Unexpected surface data.")
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
    if not surfaces or len(surfaces) != expected:
        raise AVLFailure("OUTPUT_FORMAT", "Surface count mismatch.")
    return surfaces


def parse_strips(path: Path) -> list[dict]:
    lines = _lines(path, "STRP")
    expected_count = _load_header(lines, "surfaces")[0]
    surfaces = []
    next_strip = 1
    for i, line in enumerate(lines):
        if line.strip() != "SURFACE":
            continue
        if i + 7 >= len(lines):
            raise AVLFailure("OUTPUT_FORMAT", "Truncated strip surface.")
        dims = numeric(lines[i + 2].split("|", 1)[0], 4)
        surface_index, _, count, first = [_count(v, "strip dimensions", 1) for v in dims]
        if surface_index != len(surfaces) + 1 or first != next_strip or not lines[i + 1].strip():
            raise AVLFailure("OUTPUT_FORMAT", "Inconsistent strip surface/first index.")
        numeric(lines[i + 3].split("|", 1)[0], 2)
        numeric(lines[i + 4].split("|", 1)[0], 8)
        numeric(lines[i + 5].split("|", 1)[0], 2)
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
        if [r["j"] for r in rows] != list(range(first, first + count)):
            raise AVLFailure("OUTPUT_FORMAT", "Inconsistent strip row indices.")
        after = i + 8 + count
        if after < len(lines) and lines[after].strip() not in ("", "SURFACE"):
            raise AVLFailure("OUTPUT_FORMAT", "Unexpected strip data.")
        next_strip += count
        surfaces.append(
            {"name": lines[i + 1].strip(), "surface_index": surface_index, "rows": rows}
        )
    if not surfaces or len(surfaces) != expected_count:
        raise AVLFailure("OUTPUT_FORMAT", "Strip surface count mismatch.")
    return surfaces


def _load_header(lines, count_label):
    _orientation(lines)
    start = next((i for i, line in enumerate(lines) if line.strip() == "SURFACE"), len(lines))
    records = {}
    for _, values, label in _records(lines[:start]):
        if label in records:
            raise AVLFailure("OUTPUT_FORMAT", f"Duplicate load header: {label}")
        records[label] = values
    dimensions, moment = "Sref, Cref, Bref", "Xref, Yref, Zref"
    widths = {dimensions: 3, moment: 3, count_label: 1}
    if records.keys() != widths.keys() or any(len(records[k]) != n for k, n in widths.items()):
        raise AVLFailure("OUTPUT_FORMAT", "Incomplete load reference/count header.")
    return _count(records[count_label][0], count_label, 1), dict(
        zip(("Sref", "Cref", "Bref", "Xref", "Yref", "Zref"), records[dimensions] + records[moment])
    )


def check_load_header(path: Path, kind: str, total: dict):
    """Reject loads from a different reference configuration or surface count."""
    count, refs = _load_header(_lines(path, kind), "# surfaces" if kind == "SURF" else "surfaces")
    if count != total["counts"]["surfaces"] or any(
        not math.isclose(value, total["fields"][name], rel_tol=1e-9, abs_tol=1e-10)
        for name, value in refs.items()
    ):
        raise AVLFailure("OUTPUT_FORMAT", "Load references/count do not match totals.")
