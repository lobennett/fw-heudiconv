# Compatibility fork correctness audit

## Scope and baseline

FW1/FW2 were audited at `e7509a4229a2c9eb7d4c95fa9db12fad7b86c719`
on `sherlock-compat`. All eight compatibility commits remain ancestors. This
fork is consumed by the Network pipeline; publication and no-mistakes must use
`--base-branch sherlock-compat`, not `master`.

Read surfaces: the eight-commit fork diff; query/SeqInfo and acquisition-label
plumbing in `backend_funcs/query.py`; heuristic loading, identity replacement
and chronological `{seqitem}` numbering in `cli/curate.py`; file selection,
BIDS updates and IntendedFor in `backend_funcs/convert.py`; gathering, filtering,
metadata, destinations and downloads in `cli/export.py`; existing tests and
packaging declarations. Changes are confined to selection/export consistency
and their tests. No scientific defaults, timing thresholds or run-order rules
were changed.

## Evidence and corrections

The recovered audit's tiny SDK-transport reproduction was replayed locally
against the baseline, with a synthetic local heuristic replacing the Network
consumer import. Real query, curation and export code ran; no client was
credentialed. The following outcomes were reproduced:

- **FW1:** curate an old echo-2 image, add a newer copy, curate again. Both
  retain the same BIDS destination. Export raises `FileExistsError` and removes
  the entire output root. A clean first curation with both copies selects only
  the new image; explicitly ignoring the superseded tag masks the failure.
- **FW2:** two DWI conversions each have three volumes. The new image contains
  value 20, with bvals `0 1000 2000`; the old image contains value 10, with bvals
  `0 500 500`. Baseline export succeeds with image 20 and the old gradients.
  One coherent source set, or retiring the old gradients, masks the failure.
  Equal counts therefore do not establish pairing.

These are conditional defects, not established participant incidence. The
committed synthetic fixtures in `testing/synthetic_flywheel.py` and the three
consistency/preflight test modules retain the triggers and controls without
private participant evidence or historical bundles.

Curation now prepares selection before metadata updates. After replacement
updates succeed, it clears only `BIDS.Path` and `BIDS.Filename` (and marks the
tag invalid) on superseded files in the same acquisition with the same rendered
destination. Compressed and uncompressed NIfTI copies share this identity.
Other destinations/templates and metadata survive. Retirement does not use
`BIDS.ignore`; QA rejection, its error text and custom QA fields are retained.
Selected rejected files stay rejected. Copies of metadata also keep dry curation
from mutating objects returned by the SDK. Non-DWI timestamp ties use filenames
as a stable tie-breaker; newest selection remains unchanged for unequal times.

DWI curation selects the newest raw image and exactly one bval/bvec sharing its
**original source stem**. Stems alone do not establish generation, so all three
must additionally carry the same conversion job ID: an absent origin, a
user/device origin and a job origin without an ID are all refused, as are a
common BIDS filename, upload ordering and volume count. Missing members,
differing stems, conflicting/partial job provenance and uncertain newest-image
selection raise errors with source names and available provenance. There is no
fallback to an older complete set and no warning-only path.

Converter-declared derivative maps (ADC/TRACEW/FA/... in the file's own DICOM
`ImageType`, marked `DERIVED` and not `ORIGINAL`) never compete to be the raw
image, so an ADC map uploaded after its parent image cannot hijack the stem.
This uses positive per-file converter metadata only: unknown roles,
contradictory `ORIGINAL`/`DERIVED` pairs and absent metadata leave a file a
candidate, filenames are never parsed, and missing gradients never imply a
derivative — a newer incomplete raw conversion still fails rather than falling
back. `{item}` denotes the same image for all three members.
`backend_funcs/dwi.py` holds these shared checks without SDK calls.

Export builds the actual file/JSON destinations after folder, attachment and
ignore filters, then rejects duplicate paths, file/directory conflicts and paths
escaping the output root **before any write or download**. Attachments are
rooted in the chosen dataset. Source metadata is copied before JSON
normalization.

An export writes one whole dataset. An output root that already exists — empty
or populated — is refused untouched, before any download: merging would leave
output the current curation no longer owns (a stale `run-2` beside the new
`run-1` collides with nothing), and clearing it would destroy prior results.
There is no overwrite flag; pick an unused `--destination`/`--directory-name`.

Export rechecks DWI source pairing, including acquisition identity, even for
legacy curated tags. Downloads and generated metadata are staged in a temporary
directory under the output parent. Each downloaded payload is bound to whichever
SDK `version`/`hash` identity the selected file exposed at gather time, so a
file replaced in place during the export window is refused; this detects a
replacement, it cannot reconstruct provenance the server never recorded. Before
publishing any files, NIfTI dimensions must describe a nonempty 3D/4D image,
bvals must be `1 × N`, bvecs `3 × N`, and gradients must be finite, where N is
the image's volume count (one for 3D). Invalid gradients, a replaced file or a
failed download leave no output root behind and prior datasets intact.
Publication creates the root exclusively, so a racing exporter fails rather than
interleaving two curations, and never deletes an output tree.

## Validation and replay

Local validation: Darwin/ARM64, Python 3.12.13, Flywheel SDK 21.5.0, pandas 3.0.5,
NumPy 2.5.3, nibabel 5.4.2 and pytest 9.1.1. Installed the local package editable
using its `setup.py` dependencies in an isolated worktree environment. This is
not validation of the Network consumer's frozen Linux/Sherlock lock.

```sh
mkdir -p .audit/tmp .audit/uv-cache
UV_CACHE_DIR="$PWD/.audit/uv-cache" TMPDIR="$PWD/.audit/tmp" uv venv .venv --python 3.12
UV_CACHE_DIR="$PWD/.audit/uv-cache" TMPDIR="$PWD/.audit/tmp" uv pip install --python .venv/bin/python -e .
.venv/bin/python -m pytest testing -k 'not test_client' --basetemp=.audit/pytest -q
```

Baseline package tests: **11 passed, 1 failed, 1 deselected**. The failing test
expected an anatomical bval despite the intentional DWI-only sidecar change;
its assertion was corrected without restoring invalid sidecars. The first
regression run failed at 36 expected assertions before the implementation.
Final package and regression suite: **73 passed, 1 deselected**. Each review
correction above was re-run against the pre-correction source first and failed
there: derived-map selection, replaced-file detection, absent/non-conversion
provenance and existing-output-root refusal.

`test_client` is deliberately deselected because it constructs a credentialed
live Flywheel client. The legacy CircleCI job also logs in and mutates its gear
testing project; it was not run. Synthetic checks cover consecutive and
idempotent curation, reversed enumeration, three true echoes and derivative
exclusion, fieldmap/magnitude separation, anatomy, QA/ignore preservation,
matching and ambiguous DWI sources, converter-declared derivative maps and
unknown/contradictory roles, absent and non-conversion provenance, same-length
different gradients, gradient validation, dry runs, existing-output-root
refusal, prior-dataset preservation, failed transports, in-place replacement
races, actual SDK file models, and a full query → heuristic → curation →
export path.

## Limits

- No live SDK writes, participant reruns, remote compute or production deployment
  were performed. Server concurrency/version behavior and participant incidence
  were not tested.
- Automatic retirement is limited to matching rendered destinations in the same
  acquisition. It does not infer ownership of tags after subject/session labels
  or templates change, or repair unrelated historical tags.
- DWI pairing now requires a recorded conversion job on every member, so sets
  whose gradients were uploaded or re-attributed outside the converting gear are
  refused and must be reconverted or re-uploaded by that gear. Version/hash
  binding detects a file replaced in place during an export, but cannot
  reconstruct provenance the server never recorded. Nonmatching naming schemes
  require explicit source review; counts alone never authorize a pairing.
- Derivative exclusion reads only the converter's own `ImageType`. Acquisitions
  whose converter recorded no `ImageType`, an unrecognized role, or a
  contradictory `ORIGINAL`/`DERIVED` pair are still resolved by creation time
  and may need explicit source review.
- A partially QA-ignored DWI set is refused as incomplete; an entirely ignored
  set is omitted. QA flags are never cleared to make a set exportable.
- Dry export keeps the existing placeholder-tree behavior and performs metadata
  and path checks, but cannot validate gradient contents without downloading.
- Staging requires additional local disk space. Publication is not a multi-file
  transaction: an I/O failure or concurrent writer during publication may leave
  some new files. Exclusive creation protects prior files, and retries report
  those conflicts. No broad transaction/state framework was introduced.
