"""Malformed MRF records must never be published as successful aerodynamic results."""

import shutil
from pathlib import Path

import pytest

from avl_mcp.execution import collect_case
from avl_mcp.geometry import inspect_geometry
from avl_mcp.models import AVLFailure, FlightCondition
from avl_mcp.outputs import parse_derivatives, parse_strips, parse_surfaces, parse_total

FIXTURE = Path(__file__).parent / "fixtures"
TABLES = [("stability.mrf", "DERMATS"), ("body.mrf", "DERMATB")]


def derivative_rows():
    for filename, kind in TABLES:
        lines = (FIXTURE / filename).read_text().splitlines()
        start = lines.index("DESIGN") + 2
        for index, line in enumerate(lines[start:], start):
            if "|" in line:
                yield filename, kind, index


@pytest.mark.parametrize("filename,kind,index", list(derivative_rows()))
@pytest.mark.parametrize("damage", ["remove", "duplicate", "empty", "nonfinite"])
def test_every_derivative_record_is_required(tmp_path, filename, kind, index, damage):
    lines = (FIXTURE / filename).read_text().splitlines()
    if damage == "remove":
        del lines[index]
    elif damage == "duplicate":
        lines.insert(index, lines[index])
    elif damage == "empty":
        lines[index] = " |" + lines[index].split("|", 1)[1]
    else:
        words = lines[index].split()
        words[0] = "NaN"
        lines[index] = " ".join(words)
    path = tmp_path / filename
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(AVLFailure):
        parse_derivatives(path, kind)


@pytest.mark.parametrize("filename,kind", TABLES)
@pytest.mark.parametrize(
    "damage", ["duplicate_name", "reorder_names", "width", "unknown", "same_key"]
)
def test_derivative_variable_integrity(tmp_path, filename, kind, damage):
    lines = (FIXTURE / filename).read_text().splitlines()
    declaration = next(i for i, line in enumerate(lines) if "# control vars" in line)
    row = next(i for i, line in enumerate(lines) if "Cmd*" in line)
    if damage == "duplicate_name":
        lines[declaration + 2] = lines[declaration + 1]
    elif damage == "reorder_names":
        lines[declaration + 1], lines[declaration + 2] = (
            lines[declaration + 2],
            lines[declaration + 1],
        )
    elif damage == "width":
        lines[row] = lines[row].split(maxsplit=1)[1]
    elif damage == "unknown":
        lines[row] = lines[row].replace("Cmd*", "Unexpected*")
    else:
        row = next(i for i, line in enumerate(lines) if "Clp, Clq, Clr" in line)
        lines[row] = lines[row].replace("Clp, Clq, Clr", "Clp, Clp, Clr")
    path = tmp_path / filename
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(AVLFailure):
        parse_derivatives(path, kind)


@pytest.mark.parametrize("count", ["-1", "1.5", "NaN", "1000"])
@pytest.mark.parametrize("marker", ["CONTROL", "DESIGN"])
def test_invalid_preamble_counts(tmp_path, count, marker):
    lines = (FIXTURE / "total.mrf").read_text().splitlines()
    lines[lines.index(marker) + 1] = count
    path = tmp_path / "total.mrf"
    path.write_text("\n".join(lines))
    with pytest.raises(AVLFailure):
        parse_total(path)


def test_total_requires_complete_design_block(tmp_path):
    text = (FIXTURE / "total.mrf").read_text()
    path = tmp_path / "total.mrf"
    path.write_text(text[: text.index("DESIGN") + len("DESIGN")])
    with pytest.raises(AVLFailure):
        parse_total(path)


@pytest.mark.parametrize(
    "filename,parser", [("surface.mrf", parse_surfaces), ("strips.mrf", parse_strips)]
)
@pytest.mark.parametrize("damage", ["axis", "count", "duplicate_count", "index", "reference"])
def test_load_header_and_indices(tmp_path, filename, parser, damage):
    lines = (FIXTURE / filename).read_text().splitlines()
    start = lines.index("SURFACE")
    count = next(i for i, line in enumerate(lines[:start]) if line.strip().endswith("surfaces"))
    if damage == "axis":
        lines[2] = "Geometric axis orientation, X aft, Z up"
    elif damage == "count":
        lines[count] = "5.5 |" + lines[count].split("|", 1)[1]
    elif damage == "duplicate_count":
        lines.insert(count, lines[count])
    elif damage == "reference":
        del lines[3]
    else:
        words = lines[start + 2].split()
        words[0] = "2"
        lines[start + 2] = " ".join(words)
    path = tmp_path / filename
    path.write_text("\n".join(lines))
    with pytest.raises(AVLFailure):
        parser(path)


def test_duplicate_strip_row_is_rejected(tmp_path):
    lines = (FIXTURE / "strips.mrf").read_text().splitlines()
    first = lines.index("SURFACE") + 8
    lines[first + 1] = lines[first]
    path = tmp_path / "strips.mrf"
    path.write_text("\n".join(lines))
    with pytest.raises(AVLFailure):
        parse_strips(path)


@pytest.mark.parametrize(
    "filename,token,selected",
    [
        ("stability.mrf", "CYa, CYb", "stability"),
        ("stability.mrf", "Cmd*", "stability"),
        ("body.mrf", "CXp, CXq ,CXr", "body"),
    ],
)
def test_case_collection_rejects_missing_derivatives(tmp_path, vanilla, filename, token, selected):
    for name in ("total.mrf", filename):
        shutil.copyfile(FIXTURE / name, tmp_path / name)
    path = tmp_path / filename
    path.write_text("\n".join(row for row in path.read_text().splitlines() if token not in row))
    with pytest.raises(AVLFailure):
        collect_case(
            tmp_path,
            inspect_geometry(str(vanilla)),
            FlightCondition(alpha_deg=3, mach=0.2),
            None,
            ["total", selected],
            "unspecified",
        )
    assert not (tmp_path / "result-contract.json").exists()


@pytest.mark.parametrize(
    "filename,selected", [("surface.mrf", "surfaces"), ("strips.mrf", "strips")]
)
def test_case_rejects_loads_with_other_references(tmp_path, vanilla, filename, selected):
    shutil.copyfile(FIXTURE / "total.mrf", tmp_path / "total.mrf")
    text = (
        (FIXTURE / filename).read_text().replace("9.000000000000000E+00", "8.000000000000000E+00")
    )
    (tmp_path / filename).write_text(text)
    with pytest.raises(AVLFailure):
        collect_case(
            tmp_path,
            inspect_geometry(str(vanilla)),
            FlightCondition(alpha_deg=3, mach=0.2),
            None,
            ["total", selected],
            "unspecified",
        )


@pytest.mark.native
def test_native_design_and_zero_control_tables(runner, vanilla, tmp_path):
    # Exercise real upstream DESIGN columns and zero-control declarations, not only synthetic MRF.
    shutil.copytree(vanilla.parent, tmp_path / "model")
    path = tmp_path / "model/vanilla.avl"
    lines = path.read_text().splitlines()
    stripped = []
    skip = False
    for line in lines:
        if skip:
            if line.strip() and not line.lstrip().startswith("#"):
                skip = False
            continue
        if line.strip().startswith("CONTROL"):
            skip = True
        else:
            stripped.append(line)
    path.write_text("\n".join(stripped) + "\nDESIGN\ntwist 1.0\n")
    result = runner.run(str(path), FlightCondition(alpha_deg=3, mach=0.2))
    assert result["success"], result
    for table, count in (("stability", 8), ("body", 6)):
        assert result[table]["control_derivatives"] == {}
        assert len(result[table]["design_derivatives"]["twist"]) == count
