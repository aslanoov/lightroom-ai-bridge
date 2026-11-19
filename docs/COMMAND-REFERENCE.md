# Command reference

Every `lrc` command, every Develop parameter name, the helper scripts, and where
the bridge keeps its files. Run `./lrc <command> -h` for exact flags, or
`./lrc help` to ask the *plug-in* which commands it supports.

Global flags: `--raw` prints compact one-line JSON (good for parsing);
`--direct` is accepted for compatibility and still routes through the daemon
(the plug-in serves one client at a time, so every caller shares the daemon's
connection).

## Inspect

| Command | Description |
|---|---|
| `ping` | Handshake plus active photo. |
| `status` | Current module, catalog, selected photos. |
| `list-selected` | All currently selected photos. |
| `settings [--uuid id]` | Full Develop settings table of the active (or a specific) photo. |
| `metadata [--uuid id]` | Capture and EXIF info: camera, lens, ISO, shutter, aperture, focal length, GPS. |
| `value <Param> [n]` | Get (or set, if `n` given) one parameter; `n` may also be `+`, `-` (nudge) or `reset` (default). |
| `range <Param>` | Min and max of a parameter. |
| `capabilities` | Running Lightroom, plug-in and SDK versions, which SDK functions are available, and the features that need Lightroom's UI (see [UI-only features](#ui-only-features)). Does not change the module, selection or photo. |
| `ai-state` | Partial AI status of the active photo: Enhance, Reflection Removal and known filters. Partial SDK coverage: it is not a universal "AI-free" or "processing finished" test. |
| `review-selection` | The explicitly selected photos with ratings, flags, virtual-copy and folder-stack metadata, for consuming the results of Lightroom's Assisted Culling or Duplicates view. Native focus or eye scores are not available through the SDK and are never invented; stacks are not proof of duplicate content. |

## Tone and color (active photo)

| Command | Description |
|---|---|
| `set K=V ...` | Set Develop sliders by friendly name (process-version aware); a value may also be `+`, `-` (nudge) or `reset`. |
| `wb --temp <K> --tint <t> [--targets selected]` | White balance by value; `--targets selected` batches Kelvin WB across the selection (RAW/DNG). |
| `wb --as <preset>` | WB preset: `Auto`, `Daylight`, `Cloudy`, `Shade`, `Tungsten`, `Fluorescent`, `Flash`. |
| `auto-tone` / `auto-wb` | Auto Tone / Auto White Balance. |
| `point-color list\|add\|update\|select\|delete\|clear [K=V...] [--json '{...}'] [--index 1..8] [--local [--mask-id ID]]` | Global or per-mask Point Color, including Variance and partial swatch and range updates. See [Point Color](#point-color). |
| `enhance denoise\|rawDetails\|superRes [--amount 50] [--disable]` | AI Denoise, Raw Details or Super Resolution, availability-checked for the active photo. `--amount` (1..100) applies to Denoise. The response is the Enhance panel state, not confirmation that processing finished. |
| `call denoise amount=1..100 [enable=false]` | Older alias for `enhance denoise` (SDK 15.3+ `setEnhance`). Pre-checks availability and errors with Lightroom's own text when the format is unsupported (LrC 15.4.1: RAW yes, JPEG no); the response echoes the Enhance panel state. `enable=false` removes it. |
| `call enhance_state` | Enhance panel state for the active photo: `denoiseEnabled`, `denoiseState`, `denoiseAmount`, Raw Details, Super Resolution. Check this before assuming Denoise applies to a format. |
| `reflection state\|enable\|disable [--amount -100..100] [--quality preview\|standard\|best] [--timeout 90]` | Reflection Removal: read its availability and state, or enable/disable it. Opens the Remove tool as required and waits for Lightroom's callback up to `--timeout` seconds (1..600); `timedOut: true` means pending, not success. Render to verify. |
| `update-ai [--targets selected]` | Ask Lightroom to recompute AI settings (for example AI masks after a preset). The work may continue in the background: check Lightroom's AI Edit Status and render before relying on it. |
| `call remove_people [timeout=60]` | Detect and remove distracting people (SDK 14.5+, opens the Remove tool). The first call may error `No distracting people spots are present` while detection is still computing; retry once after a few seconds. Verify by render: `RemoveAreas` / `RetouchAreas` stay EMPTY for this feature, so `settings` cannot confirm it. |
| `reset [Param]` | Reset everything, or just one parameter. |

The AI features above (Enhance, Reflection Removal, Remove People, `update-ai`)
are off the default editing path. Adobe records AI processing in the photo's
metadata, and some publishing platforms label images that carry it: respect the
destination's policy before using them.

### Point Color

Swatch indices are 1..8. `add` requires `SrcHue` (0..6), `SrcSat` and `SrcLum`
(0..1). `HueShift`, `SatScale`, `LumScale` and `Variance` use -1..1;
`RangeAmount` uses 0..1. The nested `HueRange`, `SatRange` and `LumRange`
objects take ordered `LowerNone`, `LowerFull`, `UpperFull`, `UpperNone` limits
(0..1); pass them with `--json`. `update` merges only the fields you supply into
the selected or `--index` swatch. `--local` works on the current mask, or on
`--mask-id ID`; `clear` removes every swatch in that scope. The response is the
SDK read-back; render to see the effect.

```bash
./lrc point-color add SrcHue=2.8 SrcSat=0.4 SrcLum=0.55 LumScale=-0.2 Variance=0.2
./lrc point-color update Variance=0 --index 1
./lrc point-color update --index 1 --json '{"HueRange":{"LowerFull":0.2}}'
./lrc point-color list --local --mask-id MASK_ID
```

## Geometry

| Command | Description |
|---|---|
| `crop --left --top --right --bottom --angle [--history name]` | Crop box (0..1) and/or straighten (degrees). If any edge is given, omitted edges reset to the full frame; angle-only leaves the box. |
| `crop-reset` | Remove the crop. |
| `rotate left\|right [--uuid id]` | 90 degree rotation. |
| `tool <name>` | Select a Develop tool: `loupe`, `crop`, `dust`, `redeye`, `masking`, `upright`, `point_color`, `local_point_color`, `depth_refinement`. |
| `geometry capture\|plan\|apply\|audit\|transfer\|suggest (--json '{...}' \| --file args.json)` | The state-bound geometry workflow. Exactly one of `--json` or `--file` carries the operation's arguments. Needs the optional `geometry` extra. |

Crop edges are **fractions of the frame, 0..1**, in unrotated sensor space.

### Geometry workflow

`crop --angle` sets a number; it does not prove the picture is level. The
`geometry` operations bind every step to one photo UUID and one exact render,
so a plan cannot be applied to a different photo, a stale state or a different
JPEG:

| Operation | MCP tool | What it does |
|---|---|---|
| `capture` | `geometry_capture` | Saves the photo UUID, the full Develop state and a unique render (with its hash) into a NEW output folder, plus a reference template. |
| `plan` | `geometry_plan` | A reviewable plan in DISPLAY coordinates: either transform controls (angle, vertical, horizontal) or a trim box on the current render, never both. No Lightroom writes. |
| `apply` | `geometry_apply` | Applies the plan only if the catalog and full Develop state still match the capture; creates a rollback snapshot and a receipt first, reads back, exports the after image and checks its dimensions. Never retries a write on its own. |
| `audit` | `geometry_audit` | Fits the named horizontal and vertical reference lines you marked on that exact render and saves the measurements plus an overlay. It reports residuals and uncertainty; it never certifies "level" by itself. |
| `transfer` | `geometry_transfer` | Relocates your references onto a new render of the same photo after a geometry change, by feature matching. Output is provisional; re-mark when it is rejected. |
| `suggest` | `geometry_suggest` | From separate one-control probe audits, proposes a bounded correction (at most 1 degree or 15 perspective units per step). No writes. |

```bash
./lrc geometry capture --json '{"uuid":"PHOTO-UUID","out_dir":"/tmp/geo/before","size":2560}'
```

Install the extra first (`uv pip install -e '.[geometry]'`); the `./lrc`
wrapper prefers the checkout's `.venv` when it exists. The full procedure,
reference format and acceptance rules are in
[bridge/geometry-audit.md](../bridge/geometry-audit.md). The final image still
needs a visual check.

## Masking

| Command | Description |
|---|---|
| `mask create <type> [subtype] [K=V...]` | New mask, optionally with local adjustments. |
| `mask add\|subtract\|intersect <type> [subtype] [K=V...]` | Combine with the current mask (trailing `local_*` values apply too). |
| `mask adjust K=V ... [--id ID]` | Adjust the selected mask, or the mask with that id (`local_*` params). |
| `mask list` | Inspect all masks on the photo (ids, tools, hidden and inverted state) plus the selected mask id. |
| `mask reset` | Remove all masks. |
| `mask invert [--id]` / `mask delete <id>` / `mask select <id>` | Per-mask operations. |
| `mask duplicate-invert [--id ID]` | Duplicate a mask and invert the copy's selection. |
| `mask toggle-hidden [--id ID]` | Toggle a mask's visibility once, without selecting it first (selecting a hidden mask would unhide it). Do not retry blindly after a timeout. |
| `mask component select\|delete\|toggle-hidden\|invert TOOL_ID [--mask-id ID]` | Operate on one component (tool) of a mask; the component id comes from `mask list` and must belong to that mask. |
| `mask delete-empty [--targets selected]` | Remove empty masks from the active or selected photos through the catalog API. Take a snapshot first; it does not report a guessed count. |

`type`: `brush`, `gradient`, `radialGradient`, `rangeMask`, `aiSelection`.
`subtype` (for `rangeMask` and `aiSelection` only): `subject`, `sky`,
`background`, `objects`, `people`, `landscape`, `color`, `luminance`, `depth`.

### Masking caveats (verified)

- **Creation is verified by a new mask id.** `mask create` waits up to 10
  seconds for Lightroom to list a mask id that was not there before, and
  returns an error if none appears, instead of reporting a phantom success.
  Subtypes that need a region or click in the UI create nothing:
  `rangeMask luminance` and `aiSelection objects` are confirmed, and likely all
  `rangeMask` subtypes plus `aiSelection color/depth`. After such an error,
  check `mask list` before retrying, because the AI may still be computing.
- **AI mask strength on creation.** On Lightroom Classic 15.3 and 15.4,
  `local_*` values passed to `mask create aiSelection ...` often landed too
  weak because the AI selection was not computed yet. In a live 15.6 test
  (plug-in 1.5.0, a JPEG, subject and sky masks) the values landed at full
  strength on the first call. Large RAW files have not been re-tested, so the
  safe pattern stays: create with a hard test value (`local_Exposure=-1.5`) in
  its own call, settle about 3 seconds, **render**. If the effect is missing or
  weaker than the value implies, re-push with `mask adjust local_*=...` (a few
  seconds between tries, about three tries) before concluding the mask found
  nothing. Do not chain grade or effects immediately after a create.
- **Read mask values with `value local_*` or the create/adjust response, not
  `settings`.** `settings` reported mask values as `0` on 15.3 and stores them
  on an internal scale on 15.6 (`LocalExposure2012` -0.375 for -1.5 EV).
  Coverage is verified by **rendering**. `mask list` returns correct structure
  on 15.4+ (the "always returns []" behavior was 15.3); an empty or nil list can
  appear briefly while an AI mask computes.
- **Masks cannot be positioned.** There is no API to place a radial or gradient
  mask at chosen coordinates: `LrDevelopController.createNewMask` takes only
  type and subtype, and hand-writing `MaskGroupBasedCorrections` via
  `applyDevelopSettings` renders nothing *and* replaces the whole mask array.
  Geometric masks land at Lightroom's default spot only; for a fixed placement,
  bake it into a Develop preset in the UI and use `preset apply`.
- **Per-mask color exists; the Color Grading wheels do not apply per mask.**
  There is no `local_ColorGrade*` or `local_SplitToning*` (Lightroom design).
  Per-mask color is the Color swatch: `local_ToningHue`,
  `local_ToningSaturation`, `local_ToningLuminance`, plus per-mask RGB curves
  `local_Maincurve`, `local_Redcurve`, `local_Greencurve`, `local_Bluecurve`.
- A hand-brushed region added by the user in the UI responds about 4x weaker
  than an AI selection in the same mask. Brush flow and density are tool
  properties the bridge cannot change; say so rather than over-pushing the
  shared mask.
- **Do not probe masks with repeated `undo`** on a live edit: undo can rewind
  past the entire edit and redo may not restore it. Rebuild with `reset` plus a
  replay of the commands (every edit is a deterministic recipe), or restore a
  snapshot.

## Batch and advanced

| Command | Description |
|---|---|
| `apply K=V ... [--targets selected] [--history name]` | Apply **raw** Develop-settings keys (can target all selected photos). |
| `preset list` / `preset apply [name] [--uuid id] [--targets selected] [--amount 100] [--update-ai]` | Develop presets; the name is optional if `--uuid` gives an exact match from `preset list`. `--amount` is the preset strength, 0..200, where the preset supports it. `--update-ai` (opt-in) asks Lightroom to recompute AI settings afterwards; the response does not confirm that background AI work finished. |
| `preset save <name> [--uuid photo] [--with wb,exposure] [--without g,...] [--groups g,...] [--group Claude] [--dry-run] [--force]` | Save the photo's current edit as a **UI-visible develop preset**. Filters the settings to transferable "look" groups (default: tone, curve, presence, color, grade, effects, detail, calibration, plus `bw` auto-added for grayscale edits; never crop, masks, retouch, Point Color or profile; wb and exposure are opt-in) and writes an XMP into `~/Library/Application Support/Adobe/CameraRaw/Settings/`. It appears in the Develop Presets panel (group "Claude") after the next Lightroom restart, then works with `preset list` and `preset apply`. `--dry-run` prints the exact payload. |
| `snapshot [name] [--uuid id]` | Create a Develop snapshot. |
| `snapshots [--uuid id]` | List Develop snapshots (name plus id). |
| `snapshot-apply <name-or-id>` | Restore a snapshot. This is the safe rollback, and it beats chained undo. |
| `snapshot-delete <id>` | Delete a snapshot by id. |

`apply` takes the **raw** keys you see in `settings` output (`Exposure2012`,
`CropLeft`), not the friendly names. Note that `lrc apply` runs each value
through a scalar autotyper, so arrays (such as a tone curve) arrive as strings
and Lightroom drops them silently; use `bridge/apply_json.py` for those.

## Selection and catalog

| Command | Description |
|---|---|
| `select next\|prev\|first\|last` | Move the active photo. |
| `select --uuid <id>` / `select --path <file>` | Select a specific photo. |
| `rating 0..5` / `flag pick\|reject\|none` / `label <color>` (all accept `[--uuid id]`) | Cull metadata. With `--uuid`, routes to the plug-in's `set_metadata`: writes directly to that photo and the response echoes the read-back verification. Without it, acts on the UI selection, which in grid view means **ALL** selected photos. |
| `module <name>` | Switch module (`library`, `develop`, ...). |
| `undo` / `redo` | History. Responses include `canUndo` and `canRedo`. |
| `review-selection` | Read the current selection with ratings, flags and stack metadata (see [Inspect](#inspect)). |

### UI-only features

The published SDK (15.3) has no documented endpoints for these, so the bridge
does not pretend to drive them. Use Lightroom's UI; `./lrc capabilities` lists
them too.

- Mask **Feather/Edge** (introduced in Lightroom 15.5).
- **Assisted Culling** focus and eye scores. Run Assisted Culling in the UI,
  select its results, then use `review-selection` or
  `python3 bridge/cull.py triage --native-selection`.
- Native **Duplicates** detection and its results. Use the Duplicates view,
  select, then `review-selection`.
- **Render to DNG.** An ordinary DNG export is not the same thing.

## Previews

| Command | Description |
|---|---|
| `render [--out path] [--size N] [--quality 0..1] [--uuid id] [--timeout s]` | Full-fidelity export JPEG, recommended for before/after. `--size 0` means native resolution; `--timeout` bounds a stalled export (the transport timeout grows with it). |
| `thumb [--size N] [--out path] [--uuid id] [--tag t]` | Fast preview-cache JPEG; auto-falls back to `render` on a miss. |

Both accept `--out`; otherwise they write into `~/.claude-lrc-bridge/previews/`.

## Escape hatch and daemon

| Command | Description |
|---|---|
| `call <cmd> k=v ... [--json '{...}']` | Send any plug-in command directly. |
| `daemon status\|start\|stop` | Manage the background daemon. |
| `install-plugin [--dest <dir>]` | Copy the bundled `ClaudeBridge.lrplugin` into Lightroom's Modules folder (default `~/Library/Application Support/Adobe/Lightroom/Modules`). |

## MCP tools

The MCP server exposes the same operations as 62 tools. Names follow the CLI
closely; each tool's description carries its own caveats.

| Group | Tools |
|---|---|
| Inspect | `ping`, `status`, `capabilities`, `review_selection`, `list_selected`, `get_develop_settings`, `get_metadata`, `get_value`, `get_range`, `list_presets`, `list_masks`, `daemon_status` |
| Previews | `render_preview` (inline image), `render_to_file` |
| Develop | `set_develop`, `set_value`, `set_white_balance`, `auto_tone`, `auto_white_balance`, `reset_develop`, `select_tool`, `point_color`, `enhance`, `reflection_state`, `reflection_removal`, `ai_state`, `update_ai_settings` |
| Crop and geometry | `crop`, `crop_reset`, `rotate`, `geometry_capture`, `geometry_plan`, `geometry_apply`, `geometry_audit`, `geometry_transfer`, `geometry_suggest` |
| Masks | `mask` (create, add, subtract, intersect, adjust with optional `mask_id`, list, reset, invert, delete, select, duplicate_invert, toggle_hidden), `mask_tool` (one component of a mask), `delete_empty_masks` |
| Presets and snapshots | `apply_preset` (with `amount` and `update_ai`), `save_preset`, `apply_raw_settings`, `create_snapshot`, `list_snapshots`, `apply_snapshot`, `delete_snapshot` |
| Catalog | `select_photo`, `set_rating`, `set_flag`, `set_label`, `set_metadata`, `switch_module`, `undo`, `redo` |
| Knowledge base | `kb_recall`, `kb_read_card`, `kb_learn`, `kb_bump`, `kb_absorb`, `kb_validate`, `kb_stats` |
| Escape hatch | `call_plugin` |

The `geometry_*` tools need the optional `geometry` extra in the server's
environment. After an update, reconnect the client so it sees new tools.

---

## Develop parameter names (for `set`, `value`, `mask adjust`)

These friendly names map to the correct process-version key automatically. The
full list is Adobe's `LrDevelopController` reference in the Lightroom Classic
SDK. Do not know a range? `./lrc range Exposure` returns `{"min": -5, "max": 5}`.

- **Basic:** `Temperature` `Tint` `Exposure` `Contrast` `Highlights` `Shadows`
  `Whites` `Blacks` `Texture` `Clarity` `Dehaze` `Vibrance` `Saturation`
- **Tone curve (parametric):** `ParametricHighlights` `ParametricLights`
  `ParametricDarks` `ParametricShadows`
- **HSL** (suffix a color `Red/Orange/Yellow/Green/Aqua/Blue/Purple/Magenta`):
  `HueAdjustment<Color>` `SaturationAdjustment<Color>` `LuminanceAdjustment<Color>`
- **Color grading:** `ColorGradeShadowLum` `ColorGradeMidtoneHue`
  `ColorGradeGlobalSat` `SplitToningBalance` and the rest of the wheel keys
- **Detail:** `Sharpness` `SharpenRadius` `SharpenDetail` `LuminanceSmoothing`
  `ColorNoiseReduction`
- **Effects:** `PostCropVignetteAmount` `GrainAmount` `GrainSize` `GrainFrequency`
- **Local (inside a mask), prefix `local_`:** `local_Exposure` `local_Contrast`
  `local_Highlights` `local_Shadows` `local_Whites` `local_Blacks`
  `local_Temperature` `local_Tint` `local_Clarity` `local_Texture`
  `local_Dehaze` `local_Saturation` `local_Sharpness`

## Masking examples

```bash
# Darken and cool the sky
./lrc mask create aiSelection sky local_Exposure=-0.6 local_Temperature=-8 local_Clarity=12

# Brighten the subject a touch
./lrc mask create aiSelection subject local_Exposure=0.3 local_Shadows=15

# A graduated filter (lands at Lightroom's default orientation, cannot be aimed)
./lrc mask create gradient local_Exposure=-0.5

# A radial (lands centered; geometric masks cannot be positioned)
./lrc mask create radialGradient local_Exposure=0.4 local_Clarity=8

# Range masks need input in Lightroom's UI; creation can return "No new mask observed".
./lrc mask create rangeMask color
# After completing the selection in Lightroom:
./lrc mask adjust local_Saturation=20

# Target a specific mask, or a second copy of it inverted
./lrc mask list
./lrc mask adjust local_Exposure=-0.5 --id MASK_ID
./lrc mask duplicate-invert --id MASK_ID
```

---

## Helper scripts (`bridge/`)

Durable tools. They live in the repo so sessions stop re-deriving them in
`/tmp`; their *outputs* are what goes to a `/tmp` workdir. Several need Pillow
(`pip3 install --user Pillow`).

| Script | What it does |
|---|---|
| `lrc.py` | Compatibility shim so `./lrc ...` works from a source checkout without installing anything. |
| `kb.py` | CLI for the knowledge base: `recall`, `read`, `index`, `new`, `bump`, `absorb`, `validate`, `stats`. Thin shim over `lightroom_bridge.kb`. |
| `review_folder.py` | Render the currently targeted photos and build labeled contact sheets, then `--flag 12,22,67` to flag picks by sheet index. The quick "which of these is best" workflow. |
| `cull.py` | The two-stage rate-at-scale funnel: `triage` (render all plus sheets plus manifest) -> `hires --from-csv ratings.csv --min 4` -> `writeback ratings.csv` (verified, `--dry-run` to preview). `triage --native-selection` starts from the photos you selected after Lightroom's Assisted Culling or Duplicates view and keeps their ratings, flags and stack metadata in the manifest. |
| `writeback_fast.py` | Bulk rating/flag/label/title writeback in ONE bridge call per photo using the plug-in's `set_metadata`, with its read-back as verification. Much faster and safer than select-then-write bursts on large culls. |
| `batch_apply.py` | Apply a develop recipe to an explicit list of photos, verified per photo: applies to each as the active photo, reads back, and re-issues only the keys that did not land. Use it instead of `apply --targets selected` when correctness matters. |
| `apply_json.py` | `apply_settings` with FULL JSON values (arrays, tables) that `lrc apply` cannot send, such as tone curves. |
| `measure_tilt.py` | Measure a rendered photo against named reference lines (`--references refs.json`), draw a guide overlay (`--grid out.jpg`) and save the evidence (`--json-out`). Its whole-frame texture average is a diagnostic only and never certifies level. A compatibility entry point for the package's line fitter; needs the `geometry` extra. Positive angles are clockwise. |
| `find_plumb.py` | Find candidate tracks along isolated vertical structures (a mast, a tower, a doorframe) when heavy foliage or water lines dominate the whole-frame average. Positive angle is clockwise (the top tips right), matching `geometry audit`. The tracks are unnamed candidates, not a verdict: identify the real references and measure them with the geometry audit. |
| `geometry-audit.md` | Not a script: the step-by-step geometry delivery workflow behind `lrc geometry` ([bridge/geometry-audit.md](../bridge/geometry-audit.md)). |
| `attention_check.py` | Publish-readiness gate: per-region scan confirming the subject wins on brightness and holds saturation and contrast against every other large region. |

---

## Files and data locations

Everything the bridge writes lives under `~/.claude-lrc-bridge/`:

| File | Purpose |
|---|---|
| `bridge.json` | Handshake written by the plug-in (ports, protocol, Lightroom version, status). |
| `auth.token` | Per-start secret the plug-in writes; every command must carry it. The folder is kept owner-only (0700). |
| `previews/` | JPEGs from `thumb` and `render`, named per photo UUID, plus `geometry-*` capture folders. |
| `cli.sock` | Unix socket between the `lrc` CLI and its daemon. |
| `instance.gen` | Plug-in instance-handoff token: a reloaded plug-in stamps it and the old serving loop stops. |
| `daemon.lock` | Daemon spawn lock, so two concurrent clients cannot both launch a daemon. |
| `bridge.log` | Plug-in-side log (inside Lightroom); rotates at 5 MB to `bridge.log.1`. |
| `daemon.log` | Daemon-side log (the Python bridge); rotates at 5 MB to `daemon.log.1`. |

The daemon prunes `previews/` when it starts and at most hourly while in use:
renders older than 24 hours go, then the oldest until the folder is under 1 GB.
Nothing younger than 10 minutes and nothing but top-level `.jpg` files and
`geometry-*` folders is touched.

Ports: **49463** (commands) and **49464** (responses), localhost only. Wire
protocol `json-1`: one JSON object per line each way; every command carries the
`auth.token` secret.

## Lightroom Lua environment gotchas (for plug-in maintainers)

Documented in Adobe's *Programmers Guide* and confirmed live:

- `setfenv()` is **not** available in the plug-in sandbox (Programmers Guide
  p.18), so `loadstring` cannot be sandboxed: never use it on input. Commands
  are decoded by a strict JSON parser instead.
- A `send`-mode `LrSocket` does not accept a new client after `reconnect()`,
  and its `onClosed` does not fire when a client leaves (only when Lightroom
  quits). The plug-in therefore rebinds the response port (close, then bind
  again) when the command client disconnects.
- You **cannot yield across a plain `pcall`**. All SDK calls that yield (module
  switch, `sleep`, `withWriteAccessDo`, export) go through `LrTasks.pcall`.
- `LrSocket` delivers newline-delimited messages with the newline already consumed.
- Applying changed plug-in code: **Plug-in Author Tools > Reload Plug-in** works
  cleanly as of plug-in 1.1.0. A reload makes a *fresh* Lua environment, so the
  old instance's globals are unreachable, which is why the handoff goes through
  a generation token on disk (`~/.claude-lrc-bridge/instance.gen`): the new
  instance stamps it, the old serving loop polls it, stops, and frees the ports.
  One-time caveat: upgrading *from* a pre-1.1.0 plug-in (which does not poll the
  token) still needs one full Lightroom restart.
