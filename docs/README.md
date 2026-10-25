# Documentation index

## Current 0.5.0

- [Installation and tool examples](../README.md) and [繁體中文說明](../README.zh-TW.md).
- [0.5.0 release notes](release-0.5.0.md): package contents, acceptance and upgrade scope.
- [Batch trim](batch-trim.md): synchronous and durable trim targets with per-point replay.
- [Mesh sensitivity](mesh-sensitivity.md): general surface refinement with coefficient differences and reports.
- [Dimensional trim](physical-trim.md): explicit mass/weight, speed and CG conversion, with verified execution.
- [Local mesh/dimensional-trim evidence](mesh-physical-validation.json): 487 tests and fourteen installed tools.
- [Ubuntu native installation](native-installation-ubuntu.md): verified Linux source build.
- [Previous Linux/batch-trim evidence](ubuntu-batch-trim-validation.json): 392 tests,
  eleven installed tools, 256 prescribed conditions and 24 resumable trim points.
- [Trim inputs and results](trim.md): control/reference semantics, acceptance and independent verification.
- [Automated acceptance](automated-validation.md): Python tests, real AVL build/tests,
  installed-wheel stdio, and release gate failure rules.
- [Validation status and history](validation.md); current acceptance is recorded by the exact commit's checks.
- [Result contract 1.0.0](result-contract.md): unchanged units, axes and upstream caveats.
- [Connection diagnostics](diagnostics.md): native startup, numerical and client checks.
- [Native installation](native-installation.md) and [native sources](native-sources.md).

## Historical evidence

- [0.4.0 notes](release-0.4.0.md) and [local evidence](version-0.4.0-validation.json):
  365 tests, eleven installed tools, verified single-point trim and 256 background cases.
- [0.3.0 notes](release-0.3.0.md) and [local evidence](version-0.3.0-validation.json).
- [0.2.0 notes](release-0.2.0.md) and [release validation](release-0.2.0-validation.json).
- [Version promotion](version-0.2.0-validation.json),
  [contract/diagnostic validation](contract-diagnostics-validation.json), and
  [batch/job validation](batch-jobs-validation.json).
- [Official example results](official-validation-summary.json) and
  [input hashes](official-inputs.sha256.json).

Historical test counts and desktop observations apply to their recorded versions.
The Python MCP release and the optional historical native runtime are separate
artifacts. Private research-aircraft comparisons are not repository acceptance
fixtures and are not published here.
