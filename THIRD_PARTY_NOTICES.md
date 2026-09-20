# Third-party notices

## AVL example inputs

Files in `examples/official/vanilla/` and `examples/official/bd/` are unmodified
example inputs from the official AVL 3.52 distribution:

- Source: https://web.mit.edu/drela/Public/web/avl/avl3.52.tgz
- SHA-256: `0b588ecea9222f5b625d0af0c87ae31daf3cdba1532cf0bbb36f93d6e854849b`
- Upstream: Mark Drela, Harold Youngren; retain individual file comments/credits.
- AVL release conditions: GPL-2.0-or-later, as stated in the upstream source headers.

The adjacent `.run`/`.mass` examples are preserved for context and provenance.
They are not implicitly loaded by phase-1 prescribed-condition tools.

`tests/fixtures/*.mrf` are numerical outputs generated locally from Plane Vanilla
with unmodified AVL 3.52 numerical source, double precision, Mach 0.2, alpha 3 deg,
beta zero, zero controls/rates, and geometry-header reference quantities.

## Native dependencies

The Python wheel does not contain AVL, X11 or Fortran runtime binaries. Local build
helpers operate on separately obtained inputs. The tested compiler package lock
and build details are recorded under `docs/`. Native binaries require their own
applicable notices and corresponding-source compliance when redistributed.

