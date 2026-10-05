# AVL MCP 0.4.0 implementation

Prescribed-condition analysis requires the caller to know alpha and every control
value in advance. Version 0.4.0 adds `avl.trim`: provide target CL and Cm at a fixed
Mach, and solve alpha and one explicitly named CONTROL variable. The other ten
tools retain their existing interfaces.

## Changes

- Add a validated trim request with explicit alpha/control acceptance bounds,
  fixed controls and absolute CL/Cm residual tolerances.
- Use native AVL longitudinal constraints at zero beta and angular rates, while
  retaining CONTROL gains/signs and explicit moment-reference semantics.
- Reject unacceptable solutions and independently rerun accepted candidate
  conditions in a fresh fixed-condition native process before reporting success.
- Preserve requested targets, solved conditions, residuals, bounds and verification
  in trim metadata, raw artifacts and saved-result queries.
- Extend regression and installed-wheel acceptance to the eleventh MCP tool,
  including successful trim, saved queries and a rejected out-of-bounds solution.
  Keep the 256-case background disconnect/cancel/resume regression.
- Document [trim semantics and limitations](trim.md) and refresh the current index.

## Compatibility and scope

Existing raw values, coordinate/unit conventions, prescribed-condition behavior
and physical result contract 1.0.0 remain unchanged. The new trim metadata has its
own schema version 1.0.0. CONTROL units are not automatically physical degrees,
and the moment reference is not automatically the CG. Bounds are post-solve
acceptance checks; they neither constrain native iterations nor clamp solutions.

No batch/background trim, custom left/right mixing, automatic mass-file loading,
weight/thrust balance, eigenmodes, geometry editing or numerical-solver changes
are included. No private research models or CFD data are added.

Implementation changes invalidate old background resume fingerprints. Finish
existing jobs using their original runtime or submit new jobs after upgrading.
Native acceptance targets macOS arm64; Python-only Linux regression is not native
Linux AVL verification.

## Acceptance and publication

See [automated acceptance](automated-validation.md) for reproduction commands and
the required zero-skip native gate. Current evidence is the exact commit's test
reports and GitHub checks; previous version counts remain historical evidence.
Do not infer acceptance from this version number or from a standalone solver exit.

Preparing and merging this implementation does not publish a GitHub release,
replace an installed runtime or refresh a desktop client's MCP registry. Those
are separate deployment actions.
