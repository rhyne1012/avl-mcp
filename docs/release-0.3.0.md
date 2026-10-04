# AVL MCP 0.3.0

Incomplete derivative outputs could previously be returned as successful cases:
removing the CYb row or a control's Cm row was not detected. Version 0.3.0 rejects
these partial results and adds repeatable acceptance before merging/publishing.

## Changes

- Require every ST/SB derivative (30/36 fields), all declared CONTROL/DESIGN
  coefficient rows, consistent variable names/order and ST diagnostics.
- Reject duplicate fields/records, empty or nonfinite values, fractional/negative
  counts and truncated control/design preambles.
- Validate surface/strip axes, counts, indices, row widths and reference quantities
  against the totals before publishing a case.
- Automate Python regression/build/install and real AVL 3.52 regression plus
  installed-wheel MCP acceptance. Missing native execution or skipped tests fail
  the combined acceptance gate.
- Add a current documentation index while retaining prior validation records.

## Compatibility and scope

The ten tool signatures, normal result keys, native numbers, axis/unit definitions
and result contract 1.0.0 are unchanged. Previously accepted malformed outputs
now return structured failures with retained native files. Saved JSON queries
remain readable; this change does not retroactively certify or rewrite old data.
As in 0.2.0, implementation changes invalidate background resume fingerprints;
finish old jobs with their original runtime or submit new jobs after upgrading.

No trim, eigenmodes, geometry conversion/editing, solver modifications or private
research-aircraft data are added. Native acceptance targets macOS arm64; Python
regression on Linux does not establish Linux native AVL support. The optional
native bundle remains separate from the Python package.

See [acceptance instructions](automated-validation.md),
[validation evidence](validation.md), and the exact commit's GitHub checks.
Publication and installation are separate actions from preparing/merging 0.3.0.
