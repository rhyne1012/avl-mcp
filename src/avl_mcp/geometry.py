"""Read AVL input grammar without modifying the original geometry or dependencies.

This validates structural invariants, not surface intersections or aerodynamic suitability.
Keyword abbreviations follow AVL's first-four-character convention.
"""

import hashlib
import math
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from .models import AVLFailure

MAX_INPUT_BYTES = 8_000_000
KEYWORDS = {
    "SURF",
    "BODY",
    "SECT",
    "YDUP",
    "SCAL",
    "TRAN",
    "ANGL",
    "COMP",
    "INDE",
    "NOWA",
    "NOAL",
    "NOLO",
    "CDCL",
    "NACA",
    "AIRF",
    "AFIL",
    "BFIL",
    "CONT",
    "DESI",
    "CLAF",
    "CORE",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def clean(line: str) -> str:
    # Comments only outside quoted filenames.
    quote = None
    for i, char in enumerate(line):
        if char in "\"'":
            quote = None if quote == char else (char if quote is None else quote)
        if char in "#!" and quote is None:
            return line[:i].strip()
    return line.strip()


def numbers(line: str, n: int, where: str) -> list[float]:
    try:
        out = [float(x.replace("D", "E").replace("d", "e")) for x in line.split()[:n]]
        if len(out) != n or not all(math.isfinite(x) for x in out):
            raise ValueError
        return out
    except ValueError as exc:
        raise AVLFailure("INVALID_GEOMETRY", f"Expected {n} finite numbers: {where}") from exc


def _integer(value, where, minimum=0):
    if int(value) != value or value < minimum:
        raise AVLFailure("INVALID_GEOMETRY", f"Expected integer >= {minimum}: {where}")
    return int(value)


@dataclass
class Geometry:
    path: Path
    raw_lines: list[str]
    header_lines: list[int]
    title: str
    mach: float
    symmetry: list[float]
    references: dict
    cdp: float
    source_bytes: bytes = b""
    source_hash: str = ""
    surfaces: list[dict] = field(default_factory=list)
    bodies: list[dict] = field(default_factory=list)
    controls: list[str] = field(default_factory=list)
    dependencies: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    panel_estimate: int = 0

    def report(self) -> dict:
        return {
            "source": str(self.path),
            "source_sha256": self.source_hash,
            "title": self.title,
            "mach": self.mach,
            "symmetry": self.symmetry,
            "references": self.references,
            "profile_drag_constant": self.cdp,
            "surfaces": self.surfaces,
            "bodies": self.bodies,
            "controls": self.controls,
            "dependencies": self.dependencies,
            "estimated_vortices": self.panel_estimate,
            "warnings": self.warnings,
            "validation_scope": "Text grammar, dependencies and basic geometry invariants; "
            "not a surface intersection or aerodynamic validity proof.",
        }


def inspect_geometry(model_path: str, allowed_root: Path | None = None) -> Geometry:
    path = Path(model_path).expanduser().resolve()
    if allowed_root and not path.is_relative_to(allowed_root.resolve()):
        raise AVLFailure("PATH_OUTSIDE_ROOT", "Model is outside the configured input root.")
    if not path.is_file():
        raise AVLFailure("MODEL_NOT_FOUND", f"Geometry file not found: {path}")
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise AVLFailure("INPUT_TOO_LARGE", "Geometry exceeds the 8 MB input limit.")
    try:
        source_bytes = path.read_bytes()
        if len(source_bytes) > MAX_INPUT_BYTES:
            raise AVLFailure("INPUT_TOO_LARGE", "Geometry exceeds the 8 MB input limit.")
        raw = source_bytes.decode("utf-8-sig").splitlines()
    except UnicodeError as exc:
        raise AVLFailure("INVALID_GEOMETRY", "Geometry must be a UTF-8 text file.") from exc
    lines = [(i, clean(line)) for i, line in enumerate(raw) if clean(line)]
    if len(lines) < 5:
        raise AVLFailure("INVALID_GEOMETRY", "Missing the five-line AVL header.")
    mach = numbers(lines[1][1], 1, "Mach")[0]
    sym = numbers(lines[2][1], 3, "symmetry")
    refs = numbers(lines[3][1], 3, "reference dimensions")
    xyz = numbers(lines[4][1], 3, "moment reference")
    if not 0 <= mach < 1 or any(v <= 0 for v in refs):
        raise AVLFailure("INVALID_GEOMETRY", "Require subsonic Mach and positive Sref/Cref/Bref.")
    if sym[0] not in (-1, 0, 1) or sym[1] not in (-1, 0, 1):
        raise AVLFailure("INVALID_GEOMETRY", "Symmetry flags must be -1, 0 or 1.")
    obj = Geometry(
        path,
        raw,
        [x[0] for x in lines[:5]],
        lines[0][1],
        mach,
        sym,
        dict(zip(("sref", "cref", "bref", "xref", "yref", "zref"), refs + xyz)),
        0,
    )
    obj.source_bytes = source_bytes
    obj.source_hash = hashlib.sha256(source_bytes).hexdigest()
    pos = 5
    if pos < len(lines) and re.match(r"^[+\-.\d]", lines[pos][1]):
        obj.cdp = numbers(lines[pos][1], 1, "CDp")[0]
        if obj.cdp < 0:
            raise AVLFailure("INVALID_GEOMETRY", "CDp cannot be negative.")
        pos += 1
    current = None
    section = None

    def take():
        nonlocal pos
        if pos >= len(lines):
            raise AVLFailure("INVALID_GEOMETRY", "Unexpected end of geometry file.")
        row = lines[pos]
        pos += 1
        return row

    while pos < len(lines):
        idx, text = take()
        key = text.split()[0][:4].upper()
        if key not in KEYWORDS:
            raise AVLFailure(
                "UNSUPPORTED_KEYWORD", f"Unsupported keyword at line {idx + 1}: {text}"
            )
        if key in ("SURF", "BODY"):
            _, name = take()
            _, dims = take()
            vals = numbers(dims, 2, key)
            current = {
                "name": name,
                "type": "surface" if key == "SURF" else "body",
                "sections": [],
                "n_chord" if key == "SURF" else "n_body": _integer(vals[0], key, 1),
                "spacing": vals[1],
                "duplicated": False,
            }
            if key == "SURF":
                tokens = dims.split()
                if len(tokens) >= 4:
                    try:
                        extra = numbers(dims, 4, "SURFACE discretization")
                    except AVLFailure:
                        extra = None
                    if extra:
                        current["n_span"] = _integer(extra[2], "surface span", 1)
                        current["span_spacing"] = extra[3]
                obj.surfaces.append(current)
            else:
                obj.bodies.append(current)
            section = None
            continue
        if current is None:
            raise AVLFailure("INVALID_GEOMETRY", f"{key} must follow SURFACE or BODY.")
        if key == "SECT":
            if current["type"] != "surface":
                raise AVLFailure("INVALID_GEOMETRY", "SECTION must belong to a surface.")
            _, text = take()
            v = numbers(text, 5, "SECTION")
            if v[3] <= 0:
                raise AVLFailure("INVALID_GEOMETRY", "Section chord must be positive.")
            section = dict(zip(("xle", "yle", "zle", "chord", "incidence_deg"), v))
            if len(text.split()) >= 7:
                try:
                    extra = numbers(text, 7, "SECTION spacing")
                    section["n_span"] = _integer(extra[5], "section span")
                    section["span_spacing"] = extra[6]
                except AVLFailure:
                    # Trailing descriptive text is permitted by AVL's list-directed reads.
                    if re.match(r"^[+\-.\d]", text.split()[5]):
                        raise
            section["controls"] = []
            current["sections"].append(section)
        elif key in ("AFIL", "BFIL"):
            if key == "AFIL" and section is None:
                raise AVLFailure("INVALID_GEOMETRY", "AFILE requires a SECTION.")
            if key == "BFIL" and current["type"] != "body":
                raise AVLFailure("INVALID_GEOMETRY", "BFILE requires a BODY.")
            line_number, fname = take()
            try:
                tokens = shlex.split(fname)
            except ValueError as exc:
                raise AVLFailure("INVALID_GEOMETRY", "Invalid quoted dependency filename.") from exc
            if len(tokens) != 1:
                raise AVLFailure(
                    "INVALID_GEOMETRY", "Quote dependency filenames containing spaces."
                )
            relative = Path(tokens[0])
            # A geometry can use a local assets subdirectory; never read absolute/parent paths.
            dep = (path.parent / relative).resolve()
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or not dep.is_relative_to(path.parent)
            ):
                raise AVLFailure(
                    "UNSAFE_DEPENDENCY", "Dependencies must stay within model directory."
                )
            if not dep.is_file():
                raise AVLFailure("MISSING_DEPENDENCY", f"Missing {key} dependency: {relative}")
            if dep.stat().st_size > MAX_INPUT_BYTES:
                raise AVLFailure("INPUT_TOO_LARGE", "Dependency exceeds the 8 MB input limit.")
            obj.dependencies.append(
                {"kind": key, "path": str(dep), "line": line_number, "sha256": sha256(dep)}
            )
            if key == "AFIL":
                section["airfoil_file"] = str(relative)
            else:
                current["body_file"] = str(relative)
        elif key in ("NACA", "CLAF", "CONT", "DESI", "AIRF"):
            if section is None:
                raise AVLFailure("INVALID_GEOMETRY", f"{key} requires a SECTION.")
            if key == "AIRF":
                coords = []
                while pos < len(lines) and lines[pos][1].split()[0][:4].upper() not in KEYWORDS:
                    _, row = take()
                    coords.append(numbers(row, 2, "AIRFOIL coordinates"))
                if len(coords) < 3:
                    raise AVLFailure("INVALID_GEOMETRY", "AIRFOIL needs at least 3 coordinates.")
                section["inline_airfoil_points"] = len(coords)
            else:
                _, row = take()
                if key == "CONT":
                    tokens = row.split()
                    if len(tokens) < 7:
                        raise AVLFailure("INVALID_GEOMETRY", "CONTROL requires name and 6 numbers.")
                    vals = numbers(" ".join(tokens[1:]), 6, "CONTROL")
                    name = tokens[0]
                    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,31}", name):
                        raise AVLFailure("INVALID_GEOMETRY", "Unsupported control variable name.")
                    if not -1 <= vals[1] <= 1:
                        raise AVLFailure("INVALID_GEOMETRY", "CONTROL hinge must lie in [-1, 1].")
                    section["controls"].append(
                        {
                            "name": name,
                            "gain": vals[0],
                            "hinge_xc": vals[1],
                            "hinge_vector": vals[2:5],
                            "duplicate_sign": vals[5],
                        }
                    )
                    if name not in obj.controls:
                        obj.controls.append(name)
                elif key == "NACA":
                    naca = _integer(numbers(row, 1, "NACA")[0], "NACA")
                    if naca > 9999:
                        raise AVLFailure(
                            "INVALID_GEOMETRY", "Only four-digit NACA sections supported."
                        )
                    section["naca"] = f"{naca:04d}"
                elif key == "CLAF":
                    section["claf"] = numbers(row, 1, "CLAF")[0]
                else:
                    numbers(" ".join(row.split()[1:]), 1, "DESIGN gain")
                    obj.warnings.append(
                        "DESIGN variables retained at zero; changes are not exposed."
                    )
        elif key in ("NOWA", "NOAL", "NOLO"):
            current[key.lower()] = True
        else:
            _, row = take()
            n = {
                "YDUP": 1,
                "SCAL": 3,
                "TRAN": 3,
                "ANGL": 1,
                "COMP": 1,
                "INDE": 1,
                "CDCL": 6,
                "CORE": 3,
            }[key]
            v = numbers(row, n, key)
            if key == "YDUP":
                current["duplicated"] = True
            if key == "SCAL" and any(x <= 0 for x in v):
                raise AVLFailure("UNSUPPORTED_GEOMETRY", "Phase 1 supports positive SCALE factors.")
            target = section if key == "CDCL" and section is not None else current
            target[key.lower()] = v
    if not obj.surfaces:
        raise AVLFailure("INVALID_GEOMETRY", "At least one lifting surface is required.")
    for surf in obj.surfaces:
        sections = surf["sections"]
        if len(sections) < 2:
            raise AVLFailure(
                "INVALID_GEOMETRY", f"Surface {surf['name']} has fewer than 2 sections."
            )
        for left, right in zip(sections, sections[1:]):
            if math.hypot(right["yle"] - left["yle"], right["zle"] - left["zle"]) <= 1e-12:
                raise AVLFailure("INVALID_GEOMETRY", "Adjacent sections have zero span separation.")
        span = surf.get("n_span") or sum(x.get("n_span", 0) for x in sections[:-1])
        if not surf.get("n_span") and any(x.get("n_span", 0) <= 0 for x in sections[:-1]):
            raise AVLFailure("INVALID_GEOMETRY", "Missing spanwise discretization.")
        obj.panel_estimate += surf["n_chord"] * span * (2 if surf["duplicated"] else 1)
    for body in obj.bodies:
        if not body.get("body_file"):
            raise AVLFailure("INVALID_GEOMETRY", "BODY is missing BFILE.")
    if obj.bodies:
        obj.warnings.append("BODY uses slender-body theory, not a thick-surface panel mesh.")
    if sym[0] or sym[1]:
        obj.warnings.append(
            "Image symmetry enabled; reference quantities refer to the full geometry."
        )
    obj.warnings.append("Adjacent .run and .mass files are not loaded by phase-1 prescribed cases.")
    return obj
