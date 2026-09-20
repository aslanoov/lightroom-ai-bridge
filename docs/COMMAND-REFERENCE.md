# Command reference

Every `lrc` command, every Develop parameter name, the helper scripts, and where
the bridge keeps its files. Run `./lrc <command> -h` for exact flags, or
`./lrc help` to ask the *plug-in* which commands it supports.

Global flags: `--raw` prints compact one-line JSON (good for parsing);
`--direct` is accepted for compatibility and still routes through the daemon (a
true one-shot bypass would wedge the single-client plug-in).

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

## Tone and color (active photo)

| Command | Description |
|---|---|
| `set K=V ...` | Set Develop sliders by friendly name (process-version aware); a value may also be `+`, `-` (nudge) or `reset`. |
| `wb --temp <K> --tint <t> [--targets selected]` | White balance by value; `--targets selected` batches Kelvin WB across the selection (RAW/DNG). |
| `wb --as <preset>` | WB preset: `Auto`, `Daylight`, `Cloudy`, `Shade`, `Tungsten`, `Fluorescent`, `Flash`. |
| `auto-tone` / `auto-wb` | Auto Tone / Auto White Balance. |
| `call denoise amount=1..100 [enable=false]` | Non-destructive AI Denoise on the active photo (SDK 15.3+ `setEnhance`). Pre-checks availability and errors with Lightroom's own text when the format is unsupported (LrC 15.4.1: RAW yes, JPEG no); the response echoes the Enhance panel state as verification. `enable=false` removes it. |
| `call enhance_state` | Enhance panel state for the active photo: `denoiseEnabled`, `denoiseState`, `denoiseAmount`, Raw Details, Super Resolution. Check this before assuming Denoise applies to a format. |
| `call remove_people [timeout=60]` | Detect and remove distracting people (SDK 14.5+, opens the Remove tool). The first call may error `No distracting people spots are present` while detection is still computing; retry once after a few seconds. Verify by render: `RemoveAreas` / `RetouchAreas` stay EMPTY for this feature, so `settings` cannot confirm it. |
| `reset [Param]` | Reset everything, or just one parameter. |

## Geometry

| Command | Description |
|---|---|
| `crop --left --top --right --bottom --angle [--history name]` | Crop box (0..1) and/or straighten (degrees). If any edge is given, omitted edges reset to the full frame; angle-only leaves the box. |
| `crop-reset` | Remove the crop. |
| `rotate left\|right [--uuid id]` | 90 degree rotation. |
| `tool <name>` | Select a Develop tool: `loupe`, `crop`, `dust`, `redeye`, `masking`, `upright`. |

Crop edges are **fractions of the frame, 0..1**, in unrotated sensor space.

## Masking

| Command | Description |
|---|---|
| `mask create <type> [subtype] [K=V...]` | New mask, optionally with local adjustments. |
| `mask add\|subtract\|intersect <type> [subtype] [K=V...]` | Combine with the current mask (trailing `local_*` values apply too). |
| `mask adjust K=V ...` | Adjust the selected mask (`local_*` params). |
| `mask list` | Inspect all masks on the photo. |
| `mask reset` | Remove all masks. |
| `mask invert [--id]` / `mask delete <id>` / `mask select <id>` | Per-mask operations. |

`type`: `brush`, `gradient`, `radialGradient`, `rangeMask`, `aiSelection`.
`subtype` (for `rangeMask` and `aiSelection` only): `subject`, `sky`,
`background`, `objects`, `people`, `landscape`, `color`, `luminance`, `depth`.

### Masking caveats (verified)

- **AI masks under-apply on creation.** `local_*` values passed to
  `mask create aiSelection ...` often land too weak, because the AI selection is
  not computed yet when they fire. Reliable pattern: `mask create` ->
  `mask adjust local_*=...` to re-push -> confirm by **render**. One re-push is a
  minimum; a no-op render does not prove the mask failed, so re-push again with
  a few seconds between tries. A hard test value (`local_Exposure=-1.5`)
  confirms the mask is live, then dial to taste. Keep the create as its own
  call; do not chain grade or effects immediately after it.
- **Phantom creates: `ok` is not proof of creation.** Subtypes that need a
  region or click in the UI silently create nothing: `rangeMask luminance` and
  `aiSelection objects` are confirmed, and likely all `rangeMask` subtypes plus
  `aiSelection color/depth`. Confirm with `mask list` after every create.
- **Mask VALUE read-back lies; existence read-back works on LrC 15.4+.**
  `settings` reports mask `local_*` as `0`, so verify values by rendering only.
  `mask list` returns correct structure (ids, types, subtypes) on 15.4+ (the
  "always returns []" behavior was 15.3), so use it for existence checks and to
  get ids for `mask select` and `mask delete`.
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
| `preset list` / `preset apply [name] [--uuid id] [--targets selected]` | Develop presets; the name is optional if `--uuid` gives an exact match from `preset list`. |
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

# Range mask by color
./lrc mask create rangeMask color
./lrc mask adjust local_Saturation=20
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
| `cull.py` | The two-stage rate-at-scale funnel: `triage` (render all plus sheets plus manifest) -> `hires --from-csv ratings.csv --min 4` -> `writeback ratings.csv` (verified, `--dry-run` to preview). |
| `writeback_fast.py` | Bulk rating/flag/label/title writeback in ONE bridge call per photo using the plug-in's `set_metadata`, with its read-back as verification. Much faster and safer than select-then-write bursts on large culls. |
| `batch_apply.py` | Apply a develop recipe to an explicit list of photos, verified per photo: applies to each as the active photo, reads back, and re-issues only the keys that did not land. Use it instead of `apply --targets selected` when correctness matters. |
| `apply_json.py` | `apply_settings` with FULL JSON values (arrays, tables) that `lrc apply` cannot send, such as tone curves. |
| `measure_tilt.py` | Measure roll and keystone on a rendered photo (magnitude-weighted structure tensor, with sign calibration on every run). |
| `find_plumb.py` | Measure roll from ONE isolated vertical structure (a mast, a tower, a doorframe) instead of the whole frame's average edge direction. Use it when heavy foliage or water lines fool `measure_tilt.py`. |
| `attention_check.py` | Publish-readiness gate: per-region scan confirming the subject wins on brightness and holds saturation and contrast against every other large region. |

---

## Files and data locations

Everything the bridge writes lives under `~/.claude-lrc-bridge/`:

| File | Purpose |
|---|---|
| `bridge.json` | Handshake written by the plug-in (ports, Lightroom version, status). |
| `previews/` | JPEGs from `thumb` and `render`, named per photo UUID. |
| `cli.sock` | Unix socket between the `lrc` CLI and its daemon. |
| `instance.gen` | Plug-in instance-handoff token: a reloaded plug-in stamps it and the old serving loop stops. |
| `daemon.lock` | Daemon spawn lock, so two concurrent clients cannot both launch a daemon. |
| `bridge.log` | Plug-in-side log (inside Lightroom). |
| `daemon.log` | Daemon-side log (the Python bridge). |

Ports: **49463** (commands) and **49464** (responses), localhost only.

## Lightroom Lua environment gotchas (for plug-in maintainers)

Documented in Adobe's *Programmers Guide* and confirmed live:

- `setfenv()` is **not** available in the plug-in sandbox (Programmers Guide
  p.18); the command parser guards for it.
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
