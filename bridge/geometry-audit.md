# Crop and geometry: executable workflow

Needs plug-in 1.4 or later (package 2.1). The command is `./lrc geometry`.
MCP exposes the same six operations as `geometry_capture`, `geometry_plan`,
`geometry_apply`, `geometry_audit`, `geometry_transfer`, `geometry_suggest`.
Install `.[geometry]` into the bridge environment. The checkout wrapper prefers
`.venv/bin/python`. Old clients need an MCP server reconnect to discover new tools;
the CLI works immediately. No SDK upgrade beyond the published 15.3 is needed.

## What each operation guarantees

| Operation | Result | Still requires judgment |
|---|---|---|
| capture | Explicit photo UUID, catalog, full settings, unique JPEG, hash, dimensions | See the image and choose credible references |
| plan | Display-space coordinates, crop preview, native size prediction, protected points | Decide why this composition is better |
| apply | Stale-state guard inside catalog write, unique snapshot, readback, after export, size and nongeometry checks | Inspect actual distortion, edge gaps and reference alignment |
| audit | Named line fits in physical pixels, both axes, uncertainty, guide overlay | Confirm the lines follow real features with the intended meaning |
| transfer | Conservative feature matching of the same photo after geometry | Inspect relocated lines; re-mark when support is weak |
| suggest | Measured local slider response, bounded least-squares proposal | Re-render, re-measure, and reject a visually worse result |

No operation automatically certifies that the picture is straight. An accepted
review is a recorded visual judgment. Lightroom returning `ok` is only one check.

## 1. Capture, look and annotate

From the project root:

```sh
./lrc geometry capture --json '{"uuid":"PHOTO-UUID","out_dir":"/tmp/geo/before","size":2560}'
```

Use a NEW directory each time. The plug-in checks Develop settings before and
after exporting into its own unique directory, so another render cannot overwrite
this evidence. `capture.json` includes the full tone/mask/lens state. Its image
hash prevents reusing coordinates on a different JPEG. Capture may reject an
in-flight edit; let Lightroom settle and make a fresh capture.

Read `render.jpg`. Copy `references-template.json` to a new file and populate it:

```json
{
  "image_size": [2000, 2500],
  "image_sha256": "ACTUAL-HASH-FROM-CAPTURE",
  "references": [
    {"label":"V1 building corner", "axis":"vertical",
     "reason":"Plumb outside corner of a facade, traced on the corner line itself",
     "points":[[0.2,0.2],[0.21,0.5],[0.22,0.8]]},
    {"label":"H1 far lake shoreline", "axis":"horizontal",
     "reason":"Water meets the far shore at eye level; no reeds or waves",
     "points":[[0.1,0.4],[0.5,0.41],[0.9,0.42]]}
  ],
  "unavailable": {}
}
```

Coordinates are fractions of the EXACT rendered width/height, not RAW sensor
coordinates. The example coordinates are illustrative, never reusable evidence.
Use vertical references on opposite sides where possible. A real horizon is
stronger evidence of gravity than a receding roof or pavement joint. For tapered
poles use centerlines, not one edge. Architectural diagonals and sloping ground
are not defects. If one axis genuinely lacks a credible reference, record e.g.
`"unavailable":{"horizontal":"No horizon or front-facing level structure is visible"}`.
An empty axis without an explanation remains unresolved.

```sh
./lrc geometry audit --json '{"capture_path":"/tmp/geo/before/capture.json","references":"/tmp/geo/refs.json","out":"/tmp/geo/before-audit.json"}'
```

Read the `.guides.jpg` overlay and the clean render. Angles are clockwise in
physical pixel coordinates for BOTH axes. Two endpoints cannot establish fitting
precision. The tool includes a placement-error floor (default 2 px per endpoint),
fit RMS, span and drift in pixels. Long clean references beat short precise-looking
numbers. A curved or damaged reference must be rejected, not fitted harder.

## 2. Correct geometry before trimming

```sh
./lrc geometry plan --json '{"capture_path":"/tmp/geo/before/capture.json","out":"/tmp/geo/angle-plan.json","controls":{"angle":-1.0},"reason":"Correct measured clockwise sea-horizon tilt"}'
./lrc geometry apply --json '{"plan_path":"/tmp/geo/angle-plan.json","out_dir":"/tmp/geo/angle-after"}'
```

`controls` are ABSOLUTE display values: `angle` (clockwise degrees), `vertical`
and `horizontal` (Lightroom Transform sliders, -100..100). Read current values
from capture; do not confuse an absolute target with an increment. Current
manual perspective and crop are preserved when changing only angle.

The plan converts the display axes through `orientation` and calculates crop
side lengths from the rotated diagonal. It checks that the model reproduces the
current Lightroom native cropped dimensions. Unsupported or ambiguous states
stop with an explanation, rather than guessing. Supported rotations: AB, BC,
CD, DA. Mirrored orientations, active automatic/Guided Upright, and nondefault
Perspective Rotate/Aspect/Scale/X/Y are intentionally outside the current model;
use Lightroom UI and the same capture/audit protocol for those cases.

Each apply writes only allowed geometry keys directly to the captured photo,
checks the FULL Develop state in the catalog write gate, creates a unique
snapshot, and saves a recovery receipt before mutation. It never replays a write
after a timeout. If the tool reports failure or unknown outcome, inspect its
receipt, current settings and snapshot before proceeding. No automatic rollback
can safely overwrite edits the user made meanwhile. Restore the named snapshot
when appropriate, then verify crop/settings and render again.

For a difficult coupled correction, use small one-control calibration probes
from the SAME baseline, each with its own after capture and named-line audit.
Restore the baseline between probes. A sensible starting probe is 0.5-1 degree
or 5-10 perspective units; do not make huge speculative warps.

```sh
./lrc geometry suggest --json '{"base_audit":"/tmp/geo/base-audit.json","probe_audits":["/tmp/geo/angle-audit.json","/tmp/geo/vertical-audit.json"],"out":"/tmp/geo/proposal.json"}'
```

The solver rejects rank deficiency and poor conditioning. It limits each proposal
to 1 degree and 15 perspective units, scaling a coupled step together. This is a
local proposal, not an exact global solution. Restore the baseline, capture it,
plan the suggested controls, apply, and audit the REAL result. Stop chasing
fractions of a degree below placement uncertainty; if the photo still feels
wrong, reassess the reference, perspective and visual balance.

## 3. Relocate references, never reuse old fractions blindly

```sh
./lrc geometry transfer --json '{"source_capture":"/tmp/geo/before/capture.json","target_capture":"/tmp/geo/angle-after/capture.json","references":"/tmp/geo/refs.json","out":"/tmp/geo/after-refs.json"}'
```

Transfer requires the same UUID/catalog and unchanged nongeometry settings. It
checks match count, inlier fraction, spatial coverage, residuals and local
support around every reference. References outside the new crop are rejected.
The result is provisional even with many good matches. Run audit on it and READ
the overlay to confirm the line is still on the actual feature. If rejected,
mark visible features manually. Lens/AI/mask/tone changes require new annotations.

## 4. Crop in CURRENT DISPLAY coordinates

After transforms are visually acceptable, use their fresh capture:

```sh
./lrc geometry plan --json '{"capture_path":"/tmp/geo/angle-after/capture.json","out":"/tmp/geo/crop-plan.json","box":[0.04,0.02,0.94,0.98],"ratio":0.8,"anchor":[0.5,0.5],"protected":[{"label":"subject head","point":[0.5,0.3],"margin":0.05}],"reason":"Tighten to 4:5 and drop a bright distraction at the right edge, keeping headroom"}'
```

`box` is `[left,top,right,bottom]` in the current rendered picture. `ratio` is
width/height; 0.8 means 4:5, 1.25 means 5:4. It fits the largest such rectangle
INSIDE the box. `anchor` chooses where surplus space is removed (0=left/top,
1=right/bottom). Protected points are checked with a margin measured within the
new crop. Mark both hands, feet, headroom, or crucial context when they matter.
Points do not model an entire silhouette: still inspect every edge.

Read the plan's `.preview.jpg`, then apply to a NEW output directory. The tool
verifies native dimensions within pixel rounding and requires visual review.
Do not combine a new transform with a current-render crop: coordinates would
refer to geometry that has not yet been rendered. To expand beyond the current
crop, use Lightroom UI and recapture; this tool's trim is deliberately inscribed.

## 5. Delivery gate

Audit the final render. Inspect the clean image at ordinary size and the guide
overlay. Check reference placement, both axes, people's proportions, all four
borders, intended ratio and composition. Receding facade edges need not all be
horizontal; intentional convergence may be preserved with a stated reason.

To record that review, pass an object to `audit`:

```json
{"reference_placement_checked":true,"proportions_checked":true,
 "borders_checked":true,"composition_checked":true,
 "decision":"accept","reason":"The named corner and the shoreline align; no limbs cut, no empty corners or stretched faces."}
```

Allowed decisions: accept, revise, unverifiable. An unresolved axis cannot be
accepted. This records the agent's inspection; it is NOT user approval. Default
to level unless tilt is deliberately part of the style.

## Validation and sources

- Automated suite: `./.venv/bin/python -m unittest discover -s tests -v`.
- Opt-in integration runner: `tests/live_geometry_validation.py`. Requires an
  explicit UUID verified as a virtual copy, checks SDK display values and compares
  actual cropped pixels against the planned preview; restores the copy afterward.
- Closed-loop integration runner: `tests/live_geometry_correction.py` injects a
  known tilt on a virtual copy, calibrates a small probe, proposes and verifies a
  correction, then restores the copy.
- SDK 15.3 local API: `LrDevelopController.getValue/setValue`, `LrPhoto.getDevelopSettings`,
  `applyDevelopSettings`, `createDevelopSnapshot`, `applyDevelopSnapshot`.
- [Adobe Guided Upright](https://helpx.adobe.com/lightroom-classic/desktop/process-and-develop-photos/guided-upright-perspective-correction.html)
  and [crop/straighten](https://helpx.adobe.com/lightroom-classic/desktop/help/crop-rotate-straighten.html).

Legacy `measure_tilt.py` is a compatibility wrapper around the package's line
fitter. Texture scores and `find_plumb.py` tracks are candidate-finding diagnostics
only. Neither can certify roll, keystone, or a straight final image.
