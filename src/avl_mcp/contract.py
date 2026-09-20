"""Versioned semantics for AVL 3.52 MRF values; raw fields never change units/axes."""

import math

SCHEMA_VERSION = "1.0.0"


def field(unit="1", axes=None, definition=None, normalization=None):
    return {
        k: v
        for k, v in {
            "unit": unit,
            "axes": axes,
            "definition": definition,
            "normalization": normalization,
        }.items()
        if v is not None
    }


def coefficient(name, table="body"):
    axes = "standard_stability" if table == "stability" else "standard_body"
    if name in ("CL", "CD"):
        return field(
            axes="standard_stability",
            definition={"CL": "Lift, positive along -z_s", "CD": "Drag, positive along -x_s"}[name],
            normalization="Q*Sref",
        )
    if name in ("CX", "CY", "CZ"):
        return field(
            axes=axes, definition="Force along +" + name[-1].lower(), normalization="Q*Sref"
        )
    if name in ("Cl", "Cm", "Cn"):
        return field(
            axes=axes,
            definition="Right-hand moment about " + {"Cl": "x", "Cm": "y", "Cn": "z"}[name],
            normalization="Q*Sref*" + ("Cref" if name == "Cm" else "Bref"),
        )
    if name == "CDff":
        return field(
            axes="trefftz_plane", definition="Far-wake induced drag", normalization="Q*Sref"
        )
    if name == "e":
        return field(definition="AVL Trefftz span efficiency")
    return field(definition="Unrecognized native field; consult raw MRF labels")


def derivative_contract(table, parsed):
    stability = table == "stability"
    axes = "standard_stability" if stability else "standard_body"
    rates = {
        "p": "p_s*Bref/(2*V)" if stability else "p_b*Bref/(2*V)",
        "q": "q*Cref/(2*V)",
        "r": "r_s*Bref/(2*V)" if stability else "r_b*Bref/(2*V)",
    }
    variables = {k: field(axes=axes, definition=v) for k, v in rates.items()}
    if stability:
        variables.update(
            {"a": field("rad", definition="alpha"), "b": field("rad", definition="beta")}
        )
    else:
        variables.update({k: field(axes=axes, definition=f"{k}/V0") for k in "uvw"})
    return {
        "native_command": "ST" if stability else "SB",
        "axes": axes,
        "variables": variables,
        "held_fixed": (
            "Other angles, stability rates, Mach, controls, geometry and references. "
            "Alpha derivatives include rotation of the output axes and of body rates needed to "
            "hold stability rates fixed in the intended convention; see native_limitations "
            "for the 3.52 standard-axis nonzero-rate exception."
            if stability
            else "Other body velocity/rate components, Mach, controls, geometry and references. "
            "V0 and reference dynamic pressure remain fixed for u/V0,v/V0,w/V0 derivatives; "
            "these are not derivatives per m/s or Mach."
        ),
        "fields": {
            key: {
                "coefficient": key[:-1],
                "variable": key[-1],
                "unit": "rad^-1" if stability and key[-1] in "ab" else "1",
                "coefficient_definition": coefficient(key[:-1], table),
            }
            for key in parsed["derivatives"]
        },
        "control_derivatives": {
            "unit": "CONTROL_unit^-1",
            "variables": list(parsed["control_derivatives"]),
            "definition": "d(coefficient)/d(CONTROL variable); section gains and duplicate signs "
            "are already included. This is not automatically per physical radian.",
            "coefficients": {
                k: coefficient(k, table)
                for vals in parsed["control_derivatives"].values()
                for k in vals
            },
        },
        "design_derivatives": {
            "unit": "DESIGN_unit^-1",
            "variables": list(parsed["design_derivatives"]),
            "definition": "d(coefficient)/d(DESIGN variable); local twist degrees = gain*variable. "
            "Variables are retained at zero by this connector.",
            "coefficients": {
                k: coefficient(k, table)
                for vals in parsed["design_derivatives"].values()
                for k in vals
            },
        },
    }


def make_contract(result):
    """Read actual validated solver references, never assume SI or a mass-derived CG."""
    unit = result["geometry_length_unit"]
    length = "Lunit" if unit == "unspecified" else unit
    total = result["total"]["fields"]
    refs = {k: total[k] for k in ("Sref", "Cref", "Bref", "Xref", "Yref", "Zref")}
    totals = {}
    for key in total:
        if key == "Sref":
            definition = field(length + "^2", definition="Coefficient reference area")
        elif key in ("Cref", "Bref", "Xref", "Yref", "Zref"):
            definition = field(
                length, "geometry" if key.endswith("ref") and key[0] in "XYZ" else None
            )
        elif key in ("Alpha", "Beta"):
            definition = field("deg", definition=key.lower())
        elif key in ("pb/2V", "qc/2V", "rb/2V", "p'b/2V", "r'b/2V"):
            definition = field(
                axes="standard_stability" if "'" in key else "standard_body",
                definition="Nondimensional angular rate " + key,
            )
        elif key in ("CDvis", "CDind"):
            definition = field(
                axes="standard_stability",
                normalization="Q*Sref",
                definition={
                    "CDvis": "Supplied profile-drag contribution, not a viscous flow solution",
                    "CDind": "Near-field induced contribution, CDtot-CDvis; distinct from CDff",
                }[key],
            )
        elif key in ("CLff", "CYff"):
            definition = field(
                axes="trefftz_plane",
                normalization="Q*Sref",
                definition="Far-wake " + ("lift" if key == "CLff" else "side force"),
            )
        elif key == "Mach":
            definition = field(definition="Freestream Mach number")
        else:
            name = key.removesuffix("tot").replace("'", "")
            definition = coefficient(name, "stability" if "'" in key else "body")
        totals[key] = definition
    contract = {
        "schema_version": SCHEMA_VERSION,
        "native_format": "AVL 3.52 MRF VERSION 1.0",
        "definition_sources": {
            "manual": "https://web.mit.edu/drela/Public/web/avl/avl_doc.txt",
            "source_archive": "https://web.mit.edu/drela/Public/web/avl/avl3.52.tgz",
            "archive_sha256": "0b588ecea9222f5b625d0af0c87ae31daf3cdba1532cf0bbb36f93d6e854849b",
            "routines": [
                "aoutmrf.f:MRFTOT/MRFSURF/MRFSTRP",
                "aoutput.f:DERMATS/DERMATB",
                "aero.f:AERO",
                "atpforc.f:TPFORC",
            ],
        },
        "value_origin": {
            "total/stability/body/surfaces/strips": "parsed_native",
            "raw_transformations": [],
            "derived": "connector_calculated; separate from native coefficients",
            "undefined_diagnostics": "Native neutral-point/spiral sentinel >=1e29 becomes null",
        },
        "units": {
            "length": length,
            "area": length + "^2",
            "length_unit_declared": unit,
            "unit_source": "caller_label_only",
            "geometry_converted": False,
            "dynamic_pressure": "Q=0.5*rho*V^2; no dimensional force is inferred",
        },
        "axes": {
            "geometry": {"x": "aft", "y": "right", "z": "up"},
            "standard_body": {"x": "forward", "y": "right", "z": "down"},
            "standard_stability": {
                "definition": "Body vector components rotated through alpha only; y_s=y_b",
                "body_to_stability": [
                    [
                        math.cos(math.radians(total["Alpha"])),
                        0,
                        math.sin(math.radians(total["Alpha"])),
                    ],
                    [0, 1, 0],
                    [
                        -math.sin(math.radians(total["Alpha"])),
                        0,
                        math.cos(math.radians(total["Alpha"])),
                    ],
                ],
                "full_wind_axes": False,
            },
            "trefftz_plane": {
                "definition": "Native quantities from the geometry Y-Z wake plane; "
                "CDff is the induced-drag energy integral"
            },
            "moments_and_rates": "Right-hand rule; input rates use body axes",
        },
        "references": refs,
        "moment_reference": {
            "axes": "geometry",
            "coordinates": [refs[k] for k in ("Xref", "Yref", "Zref")],
            "unit": length,
            "is_mass_derived_cg": False,
        },
        "total_fields": totals,
        "controls": {
            "unit": "CONTROL_unit",
            "definition": "local degrees = section gain * "
            "variable; YDUPLICATE also applies duplicate_sign",
        },
        "derivative_tables": {
            k: derivative_contract(k, result[k]) for k in ("stability", "body") if k in result
        },
        "derived_fields": {
            "wind_forces": {
                "unit": "1",
                "source": "total.fields",
                "angle_unit": "deg",
                "formula": {
                    "CD": "CDtot*cos(beta)-CYtot*sin(beta)",
                    "CY": "CYtot*cos(beta)+CDtot*sin(beta)",
                    "CL": "CLtot",
                },
                "axes": "wind",
                "normalization": "Q*Sref",
                "note": "Rotation of native CDtot/CYtot/CLtot only; see native_limitations. "
                "No moment/derivative rotation, "
                "unit conversion or reference-point translation is performed.",
            },
        },
    }
    if "stability" in result:
        contract["derivative_tables"]["stability"]["diagnostics"] = {
            "neutral_point_x": field(length, "geometry", "AVL neutral point Xnp"),
            "spiral_parameter": field(definition="Clb*Cnr/(Clr*Cnb); not an eigenmode calculation"),
        }
    if "surfaces" in result:
        contract["surface_fields"] = {
            "global_reference": {
                "index": field(definition="1-based surface index"),
                "area": field(length + "^2"),
                **{k: coefficient(k) for k in ("CL", "CD", "CY", "Cl", "Cm", "Cn")},
                "CDi": totals["CDind"],
                "CDv": totals["CDvis"],
            },
            "local_reference": {
                "index": field(definition="1-based surface index"),
                "area": field(length + "^2"),
                "chord": field(length, definition="Surface average chord"),
                "cl": field(
                    axes="local_surface",
                    normalization="Q*surface_area",
                    definition="Lift in local surface plane",
                ),
                "cd": field(
                    axes="local_surface",
                    normalization="Q*surface_area",
                    definition="Drag in local surface plane",
                ),
                "cdv": field(normalization="Q*surface_area", definition="Profile drag"),
            },
        }
    if "strips" in result["outputs"]:
        contract["strip_fields"] = {
            "j": field(definition="1-based strip index"),
            **{k: field(length, "geometry") for k in ("Xle", "Yle", "Zle")},
            "Chord": field(length),
            "Area": field(length + "^2"),
            "c_cl": field(length, "local_strip", "AVL CNC: chord-scaled normal loading"),
            "ai": field(
                "1",
                "local_strip",
                "AVL DWWAKE: far-wake downwash/ V; small-angle proxy, not degrees",
            ),
            "cl_perp": field(
                axes="local_strip",
                definition="Local cl normalized by squared "
                "effective velocity perpendicular to strip span, relative to V^2",
            ),
            "cl": field(axes="local_strip", normalization="Q*strip_area", definition="Local lift"),
            "cd": field(axes="local_strip", normalization="Q*strip_area", definition="Local drag"),
            "cdv": field(normalization="Q*strip_area", definition="Local profile drag"),
            "cm_c/4": field(
                axes="local_strip",
                normalization="Q*strip_area*chord",
                definition="Moment at quarter chord in strip dihedral plane",
            ),
            "cm_LE": field(
                axes="local_strip",
                normalization="Q*strip_area*chord",
                definition="Moment about strip leading-edge midpoint along LE segment",
            ),
            "C.P.x/c": field(definition="0.25-cm_c/4/cl; native 999 sentinel retained when cl=0"),
        }
    return contract


def attach_contract(result, folder, geometry):
    from .runner import dump

    contract = make_contract(result)
    contract["controls"]["section_definitions"] = [
        {
            "surface": surf["name"],
            "surface_index": si,
            "section_index": j,
            "surface_duplicated": surf["duplicated"],
            **control,
        }
        for si, surf in enumerate(geometry.surfaces, 1)
        for j, section in enumerate(surf["sections"], 1)
        for control in section["controls"]
    ]
    f = result["total"]["fields"]
    beta = math.radians(f["Beta"])
    result["derived"] = {
        "wind_forces": {
            "CD": f["CDtot"] * math.cos(beta) - f["CYtot"] * math.sin(beta),
            "CY": f["CYtot"] * math.cos(beta) + f["CDtot"] * math.sin(beta),
            "CL": f["CLtot"],
        }
    }
    alpha = math.radians(f["Alpha"])
    projected_drag = -(math.cos(alpha) * f["CXtot"] + math.sin(alpha) * f["CZtot"])
    residual = f["CDtot"] - projected_drag
    limitations = []
    if abs(residual) > 1e-10 * max(1, abs(f["CDtot"])):
        limitations.append(
            {
                "code": "NATIVE_FORCE_PROJECTION_DIFFERENCE",
                "message": "Native CDtot differs from a rotation of CXtot/CZtot. AVL 3.52 adds "
                "constant CDp directly to CDtot but adds its body force along the "
                "freestream, producing a CDp*(1-cos(beta)) difference. The derived wind "
                "fields rotate native CDtot/CYtot/CLtot; no native value is corrected.",
                "CDtot_minus_body_projection": residual,
            }
        )
    if "stability" in result and (abs(f["pb/2V"]) > 1e-12 or abs(f["rb/2V"]) > 1e-12):
        limitations.append(
            {
                "code": "NATIVE_ST_ALPHA_NONZERO_RATES",
                "message": "In upstream 3.52 DERMATS, WROT_A lacks the DIR factor used for the "
                "standard-axis rate conversion. At nonzero p/r, native ST alpha "
                "derivatives need not equal finite differences holding stability rates "
                "fixed. Use explicit perturbed runs for that derivative; raw ST is retained.",
            }
        )
    contract["native_limitations"] = limitations
    for item in limitations:
        result["warnings"].append(item["code"] + ": " + item["message"])
    result["result_contract"] = contract
    path = folder / "result-contract.json"
    dump(path, contract)
    result["artifacts"]["result-contract.json"] = str(path)
    result["result_context"] = {
        k: contract[k] for k in ("schema_version", "references", "moment_reference", "units")
    } | {
        "contract_file": str(path),
        "native_values_transformed": False,
        "native_limitation_codes": [item["code"] for item in limitations],
        "derivative_axes": {k: v["axes"] for k, v in contract["derivative_tables"].items()},
    }
