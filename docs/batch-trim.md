# Synchronous and background longitudinal trim batches

The 0.5 development implementation extends `avl.sweep` and `avl.submit` with
`trim_requests`. Existing `conditions` calls remain supported. Supply exactly
one of the two lists, containing 1–10000 entries. An empty list, both lists or
neither list is rejected before a job starts. The two analysis types cannot be
mixed in one job.

Every entry is a complete [TrimRequest](trim.md), with its own CL/Cm targets,
Mach, named CONTROL, fixed controls, acceptance bounds and residual tolerances.
Reference dimensions/point, units and output selection apply to the whole batch.
No target or control value is inherited from the preceding point.

## Example

Call `avl.sweep` with:

```json
{
  "model_path": "/absolute/path/to/vanilla.avl",
  "trim_requests": [
    {
      "control": "elevator",
      "target_cl": 0.4,
      "target_cm": 0.0,
      "mach": 0.2,
      "alpha_bounds_deg": [-10, 15],
      "control_bounds": [-30, 30]
    },
    {
      "control": "elevator",
      "target_cl": 0.6,
      "target_cm": 0.0,
      "mach": 0.2,
      "alpha_bounds_deg": [-10, 15],
      "control_bounds": [-30, 30]
    }
  ],
  "case_name": "trim-polar",
  "timeout_seconds": 300,
  "outputs": ["total"],
  "stop_on_error": false
}
```

The same arguments work with `avl.submit`. It returns immediately with a
`job_id`; use `avl.status`, `avl.cancel`, `avl.resume` and `avl.results` as for
prescribed-condition jobs. At most two background jobs execute per work root,
sharing the existing queue with prescribed jobs. Synchronous calls use no queue.

## Execution and failures

The input geometry/dependencies are snapshotted once. Trim points run in their
original order, each in a new native process followed by a separate prescribed
replay. They do not use prescribed sweeps' Mach grouping or shared factorization.
All candidate solves and replays share one total attempt budget, from 0.1 to
86400 seconds. Queue time is excluded. Resume grants the same budget again.
`avl.trim` remains a separate synchronous single-point operation with a 600-second
maximum.

A point succeeds only after both its candidate and independent replay satisfy
the existing trim contract. Bounds are post-solve acceptance tests, not bounded
optimization. CONTROL gains/signs, reference-point semantics and result contract
1.0.0 are unchanged. No mass, thrust balance, control mixing or eigenmodes are added.

By default, the first unsuccessful point stops the batch. `stop_on_error=false`
continues after a rejected solution or solver failure, retaining that point's
diagnostics. Timeout, cancellation or changed source data always stops the
attempt. A partly successful synchronous batch returns `isError=true` and retains
its successful points. No unattempted point is reported as a zero coefficient.

Cancellation is checked during both native candidate and verification processes.
A cancelled point is not accepted even if its candidate had already converged.
Wait for cancellation acknowledgement before resuming. Resume checks the input,
snapshot, solver and implementation identities plus saved artifact hashes. Only
successful points with `trim.accepted=true` and `trim.verification.status="passed"`
are reused; failed, missing or damaged checkpoints are recomputed in new folders.
Earlier attempts and completed checkpoints remain available.

Jobs must use the same host, work root and implementation for resume. Finish old
jobs with their original runtime; upgrading the implementation requires a new
job. Hard-killed/OOM workers are not a process-supervisor guarantee: the existing
worker-lock mechanism can detect interruption but does not establish cleanup of
an orphaned native process.

## Results

The aggregate uses `analysis_type="longitudinal_trim_batch"`; each attempted
point keeps its original zero-based `index`, `analysis_type="longitudinal_trim"`
and complete `trim` metadata (request, solved condition, residuals, bounds and
verification). MCP sweep responses retain at most 25 point summaries. Use
`avl.results` for additional pages or selected fields, including:

```json
{
  "job_id": "<returned job_id>",
  "indices": [0, 1],
  "fields": [
    "trim.solved_condition.alpha_deg",
    "trim.solved_condition.controls.elevator",
    "trim.residuals.CL",
    "trim.residuals.Cm",
    "trim.verification.status"
  ]
}
```

Failed rows retain their error and `trim` metadata; requested coefficient fields
are populated only for successful rows. Query success describes the query, not
the solver outcome.

Executed attempts save `summary.csv`, also linked by `summary_csv`. It contains
index, success, target CL/Cm, Mach, solved alpha/CONTROL value, candidate residuals,
verification status and residuals, error code and the point directory. A failed
point can contain candidate values; always check `success` and verification
before treating them as an accepted solution. CSV is an aggregate written when
the attempt ends; use status and saved-result queries for live progress.

Every candidate and replay lives below its batch directory. Original snapshots,
manifests, command streams, native output and per-case checkpoints are retained.
Synchronous batches live under `<work-root>/runs/`; durable jobs live under
`<work-root>/jobs/`. No solver run is required to read saved results.
