"""Validated public inputs. Angles are degrees; angular rates are nondimensional."""

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AVLFailure(Exception):
    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code = code
        self.details = details

    def result(self) -> dict:
        return {
            "success": False,
            "error": {"code": self.code, "message": str(self), **self.details},
        }


class FlightCondition(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    alpha_deg: float = Field(default=0.0, ge=-30, le=30)
    beta_deg: float = Field(default=0.0, ge=-20, le=20)
    mach: float | None = Field(
        default=None,
        ge=0,
        lt=0.7,
        description="None uses the geometry header; the connector requires M<0.7.",
    )
    pb_2v: float = Field(default=0.0, ge=-0.1, le=0.1)
    qc_2v: float = Field(default=0.0, ge=-0.03, le=0.03)
    rb_2v: float = Field(default=0.0, ge=-0.25, le=0.25)
    controls: dict[str, float] = Field(
        default_factory=dict,
        description="CONTROL variable values. Local deflection degrees = value * section gain.",
    )

    @field_validator("controls")
    @classmethod
    def finite_controls(cls, value):
        if any(not math.isfinite(v) or abs(v) > 180 for v in value.values()):
            raise ValueError("Control values must be finite and within [-180, 180].")
        return value


class References(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    sref: float = Field(gt=0)
    cref: float = Field(gt=0)
    bref: float = Field(gt=0)
    xref: float
    yref: float
    zref: float


class TrimRequest(BaseModel):
    """Two native unknowns: alpha and one CONTROL, at zero beta/body rates.

    Bounds accept or reject a native solution; they do not constrain AVL's iterations.
    CONTROL bounds use the geometry's variable units, not inferred physical angles.
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    control: str = Field(min_length=1, max_length=80)
    target_cl: float
    mach: float = Field(ge=0, lt=0.7)
    alpha_bounds_deg: tuple[float, float]
    control_bounds: tuple[float, float]
    target_cm: float = 0.0
    fixed_controls: dict[str, float] = Field(default_factory=dict)
    cl_tolerance: float = Field(default=1e-6, gt=0)
    cm_tolerance: float = Field(default=1e-6, gt=0)

    @field_validator("alpha_bounds_deg", "control_bounds")
    @classmethod
    def ordered_bounds(cls, value, info):
        lower, upper = value
        maximum = 30 if info.field_name == "alpha_bounds_deg" else 180
        if not -maximum <= lower < upper <= maximum:
            raise ValueError(f"Require -{maximum} <= lower < upper <= {maximum}.")
        return value

    @field_validator("fixed_controls")
    @classmethod
    def finite_fixed_controls(cls, value):
        return FlightCondition(controls=value).controls


LengthUnit = Literal["m", "ft", "in", "unspecified"]
OutputKind = Literal["total", "stability", "body", "surfaces", "strips"]
ALL_OUTPUTS = ("total", "stability", "body", "surfaces", "strips")


def output_selection(outputs=None):
    """Totals are always retained so every solved condition can be verified."""
    chosen = set(ALL_OUTPUTS if outputs is None else outputs) | {"total"}
    if chosen - set(ALL_OUTPUTS):
        raise AVLFailure("INVALID_OUTPUT_SELECTION", "Unknown output table.")
    return [name for name in ALL_OUTPUTS if name in chosen]
