# Operating the Lightroom bridge (for Claude)

> This file is part of the public project. Keep it generic. Anything about one
> user (taste, destinations, paths, photo details, session findings about their
> photos) belongs in `PERSONAL.md` or the knowledge base, never here.

This project edits the user's RAW photos in **Adobe Lightroom Classic** through
a CLI. Use `./lrc <command>` (wrapper) or `python3 bridge/lrc.py <command>`, or
plain `lrc <command>` if the package is installed. Add `--raw` for compact JSON.
Full docs: `README.md`, `docs/COMMAND-REFERENCE.md`, `docs/USER-GUIDE.md`.

**Each session, start with `./lrc ping`.** It needs Lightroom Classic open, the
"Claude Bridge" plug-in enabled and running, and a photo (or photos) selected.
The bridge auto-starts a background daemon on first call, nothing to launch by
hand. Architecture, protocol and SDK gotchas are in `README.md` and
`docs/COMMAND-REFERENCE.md`. Adobe's SDK is NOT in this repo (it is not
redistributable); download the Lightroom Classic SDK from Adobe and read
`API Reference/modules/LrDevelopController.html` before claiming a parameter
does not exist, and read the PDFs before changing plug-in Lua.

**Personal overlay.** If a `PERSONAL.md` exists at the repo root, read it right
after this file. It holds your own local rules (paths, destinations, house
workflow, which cards to load) and wins on conflict. It is personal: keep it out
of any public fork.

**Versions.** Plug-in 1.5.0 with SDK baseline and minimum Lightroom Classic
15.3, tested on 15.6. SDK and application versions are independent: do not raise
`LrSdkVersion` to the application version. The wire protocol is JSON plus a
per-start token (`~/.claude-lrc-bridge/auth.token`), so the plug-in and the
daemon must come from the same release. Errors carry a `diagnosis` (port owners,
handshake state, the fix); read its `hint` before guessing. A busy or stuck
plug-in fails in about 5 to 12 s, and `./lrc daemon stop` is safe.
`./lrc capabilities` reports API availability and the documented UI-only gaps.

**Newer controls** (parameters in `docs/COMMAND-REFERENCE.md`): `point-color`
(global, or `--local --mask-id`), `mask duplicate-invert`, `mask toggle-hidden`,
`mask component`, `mask delete-empty`, `preset apply --amount 0..200
--update-ai`, `ai-state`, `update-ai`, `enhance`, `reflection`,
`review-selection`, and `geometry`. Responses from AI update, Enhance and
Reflection Removal report a request or state, never proof that processing
finished: keep the render verification step, and respect the destination's AI
policy. Feather/Edge, Assisted Culling scores, native Duplicates and Render to
DNG have no documented SDK 15.3 endpoints; use the Lightroom UI. Lightroom's own
selection (for example after Assisted Culling) can feed `python3 bridge/cull.py
triage --native-selection`; stacks are not proof of duplicates.

**Geometry (straighten, perspective, crop) is its own delivery gate.** Use
`./lrc geometry capture/plan/apply/audit` (MCP `geometry_*`) and follow
`bridge/geometry-audit.md`: explicit photo UUID, current display coordinates,
a fresh capture after each transform, named references on both axes. A
whole-frame texture average, Upright result or slider read-back does not
certify that a picture is level.

## Knowledge base: recall -> apply -> learn (kb/)

This repo carries a self-improving knowledge base of photographic judgment:
culling criteria, genre knowledge, editing recipes, the user's taste. **Using it
is not optional**; it is what keeps ratings and edits consistent across sessions
and models. Full contract: `kb/README.md`.

- **It ships EMPTY.** `recall` printing "no cards matched" is the normal state
  of a fresh install, not a failure. Work from first principles and make the
  LEARN step count: every card the user will ever have starts as a lesson
  written on a day like this one. If the KB is empty and the user wants a head
  start, offer the seeding pass described in `kb/README.md`.
- **Recall** before culling or editing: classify the photo(s) (genre, light),
  then `python3 bridge/kb.py recall --genres <g> --task cull|edit` and read the
  listed cards. Cull with `kb/core/culling-rubric.md` rather than ad-hoc
  criteria, and cite its dimensions. Edit per `kb/core/edit-workflow.md`.
  `kb/styles/user-taste.md` always wins on conflict.
- **Destination constraints**: if the user names a publishing destination, read
  its profile card if one exists (`kb/editing/channel-profiles.md` is the
  pointer card once written). Destination hard constraints override generic
  recipes. A common one worth recording: platforms that label images carrying
  Adobe's generative-AI metadata, which rules out AI Denoise, AI Remove and
  Generative Fill for photos bound there. Analytic AI masks are fine.
- **Learn** before ending a session: user corrections, surprising outcomes, new
  working recipes go to `python3 bridge/kb.py new --title "..."`, then fill in
  the lesson in `kb/learned/`. Applied a card successfully? `kb.py bump <id>`.
  After an editing session, judge whether the edit earned a preset
  (`./lrc preset save`).
- **Ingest then archive**: `knowledge/` is an inbox of raw reference the user
  drops in. After extracting a drop's knowledge into the cards, MOVE the source
  into `knowledge/processed/` and update any card path citations in the same
  pass. Scan only the TOP LEVEL of `knowledge/` for new drops. Archive, never
  delete. Collision-safe move only: check the target first and date-prefix on a
  name clash (`mv -n`), because a bare `mv` clobbers a same-named archived file.
  Full recipe: `kb/README.md`.
- **Consolidate and revise** when `kb.py stats` says due: merge lessons by
  REWRITING the target sections, never append-only, then `kb.py absorb <id>`. On
  REWRITE DUE or STALE SEED flags, deep-revise: re-synthesize the whole card
  (body, lesson trail, fresh research, new `knowledge/` drops), restructure
  cards if needed, then `absorb <id> --reset`.
- Version split: general photography rules live outside `kb/lightroom/`;
  anything tied to a Lightroom version lives inside it (`lightroom_min`).

## Golden rule: look -> edit -> look

You can SEE the photo. Always render a preview, inspect it with the Read tool,
edit, then render again and compare. Never edit blind.

```bash
./lrc ping                                       # confirm Lightroom is connected
./lrc render --out /tmp/before.jpg --size 1500   # then Read /tmp/before.jpg
./lrc settings                                   # current Develop values (JSON)
# ...make edits...
./lrc render --out /tmp/after.jpg --size 1500    # then Read /tmp/after.jpg and compare
```

Use `render` (full export) for before/after: it is reliable and reflects every
edit, including crop and masks. `thumb` is a faster preview-cache shortcut but
Lightroom's preview pipeline often refuses it for the active photo in Develop;
the CLI auto-falls back to `render`, so `thumb` still works, just slower on a
miss. Prefer `render --out` in the loop.

If `ping` fails: relay the error's `diagnosis.hint` (it says whether Lightroom
is closed, the plug-in is stopped, too old, busy or stuck, or another program
holds the ports). Usually the fix is to open Lightroom and ensure the "Claude
Bridge" plug-in is enabled (File > Plug-in Manager) and started (Plug-in
Extras menu). It is a UI step; do not try to fix it from the shell.

## Most-used commands

```bash
./lrc set Exposure=0.35 Contrast=12 Shadows=20 Highlights=-15 Vibrance=8
./lrc wb --temp 5400 --tint 6           # or: ./lrc wb --as Daylight
./lrc auto-tone
./lrc crop --left 0.05 --top 0 --right 0.95 --bottom 1 --angle -1.2
./lrc mask create aiSelection sky local_Exposure=-0.5 local_Temperature=-6
./lrc mask create aiSelection subject local_Exposure=0.3 local_Clarity=8
./lrc reset                             # reset all   |   ./lrc reset Texture
./lrc snapshot "before big edit"        # restore point; list: ./lrc snapshots
./lrc snapshot-apply "before big edit"  # SAFE rollback (cannot over-rewind like undo)
./lrc undo  /  ./lrc redo               # responses include canUndo/canRedo
./lrc value Exposure   /   ./lrc range Exposure
./lrc rating 4 --uuid <id>              # verified writeback, immune to stale selection
./lrc preset save "My Look" --uuid <id> --dry-run   # then without --dry-run;
                                        # UI-visible preset (loads at next LrC restart)
./lrc call enhance_state                # is AI Denoise available for the ACTIVE photo?
./lrc call denoise amount=50            # non-destructive AI Denoise (RAW yes; JPEG is NOT
                                        # supported in LrC 15.4.1, the command errors with
                                        # Lightroom's own reason; classic NR is the JPEG path)
./lrc call remove_people timeout=90     # detect and remove distracting people (active photo).
                                        # First call may error 'No distracting people spots'
                                        # while detection computes, retry once. Verify by
                                        # RENDER only: RemoveAreas stays empty for this feature
./lrc call <anyPluginCmd> k=v ...       # escape hatch for anything below
```

## Editing model: read before you act

- Edits act on the **active (most-selected) photo**; the plug-in auto-switches
  to the **Develop** module. For multiple photos use `apply ... --targets
  selected`, or `bridge/batch_apply.py` when correctness matters.
- **The `applied`/`ok` echo is NOT final confirmation; the render is.** The
  plug-in waits for Develop and verifies (and retries) absolute numeric slider
  writes, which covers the old "first edit in Library is eaten" race; relative
  increments and toggles are never retried. A render fired right after `set` or
  `preset apply` can still serve the PREVIOUS state, so settle ~3s and confirm
  with `value <Param>` before rendering. Mask structure can appear before the AI
  coverage is ready (see Masking). Never conclude a control is dead without
  read-back plus settle: a race and a real no-op look identical in renders.
- **The user browsing moves the active photo between commands.** Verify
  `active.uuid` before every mutating call in a long session.
- `set`, `value` and `mask adjust` take **friendly names** (`Exposure`,
  `local_Exposure`, HSL like `SaturationAdjustmentBlue`). Process-version
  mapping is automatic.
- `crop` edges are **fractions 0..1** in unrotated sensor space; `--angle` is
  degrees. `apply` takes **raw** keys from `settings` output (`Exposure2012`,
  `CropLeft`). `lrc apply` cannot send arrays (a tone curve arrives as a string
  and Lightroom drops it silently); use `bridge/apply_json.py` for those.
- Everything is non-destructive and shows in Lightroom's History as
  "Claude: ...". **Before a multi-step edit, `snapshot "name"` a restore
  point**; if it goes wrong, `snapshot-apply "name"` is the safe rollback. A
  single `undo` is fine for one bad step; chained undo can over-rewind.
- `render` = full-fidelity export (reliable, use for before/after; `--size 0` is
  native resolution, `--timeout <s>` bounds stalled exports). `thumb` =
  preview-cache shortcut (intermittent in Develop, auto-falls back to render).

## Masking: verified working (with caveats)

- Workflow: `mask create <type> [subtype] local_*=v ...`, then `mask adjust
  local_*=v ...` (acts on the currently selected mask, last-created by default).
  **Masks are fully addressable on LrC 15.4+:** `mask list` -> `mask select <id>`
  -> `mask adjust` targets ANY mask precisely. Types: `brush gradient
  radialGradient rangeMask aiSelection`; subtypes (rangeMask and aiSelection
  only): `subject sky background objects people landscape color luminance depth`.
- Do NOT pass a subtype for `brush`, `gradient` or `radialGradient` (Lightroom
  rejects it).
- **Creation is verified by a new mask id.** Subtypes needing a region or
  click in the UI silently create nothing: `rangeMask luminance` and
  `aiSelection objects` are confirmed, likely all `rangeMask` subtypes and
  `aiSelection color/depth`. When no new id appears within 10 seconds the
  create returns an error; run `mask list` before retrying, because the AI may
  still be computing. If a range needs UI input, define it in the UI and verify
  the coverage. To pull down a blown region a rangeMask cannot reach, global
  `Whites` works when its side effects elsewhere are acceptable (measure the
  per-region percentage first).
- **AI mask strength on creation.** On Lightroom Classic 15.6 with plug-in 1.5,
  `mask create aiSelection ... local_*=v` reached full strength on the first
  call in testing, and the response's read-back matched the render. Older
  Lightroom versions often under-applied because the AI compute was not done.
  So: give the create its own call with a hard test value
  (`local_Exposure=-1.5`), do not chain grade, HSL or effects right after it,
  settle ~3s and render. If the effect is missing or weaker than the value
  implies, re-push with `mask adjust local_*=...` (3 to 5 s pauses, about three
  tries) before concluding the mask found nothing. Large RAW files are less
  tested: compare a render after one re-push before trusting the first.
- **Read mask values with `value local_*` or the create/adjust response, not
  `settings`.** On 15.6 `settings` stores local exposure on a /4 scale
  (`LocalExposure2012` -0.375 means -1.5 EV; 15.3 read 0). Coverage is verified
  by **rendering**. `mask list` returns correct structure (ids, types,
  subtypes) on 15.4+ (the "always returns []" behavior was 15.3); use it for
  existence checks and to get ids for `mask select` and `mask delete` (which
  requires an id).
- **Per-mask color exists; the Color Grading wheels do not apply per mask.** No
  `local_ColorGrade*` or `local_SplitToning*` (Lightroom design). Per-mask color
  is the Color swatch: `local_ToningHue`, `local_ToningSaturation`,
  `local_ToningLuminance`, plus per-mask RGB curves `local_Maincurve`,
  `local_Redcurve`, `local_Greencurve`, `local_Bluecurve`.
- A hand-brushed region (user-added in the UI) responds about 4x weaker than an
  AI selection in the same mask. Brush flow and density are tool properties the
  bridge cannot change; tell the user rather than over-pushing the shared mask.
- **You CANNOT position a mask** at chosen coordinates (for example a radial
  spotlight on a subject). `createNewMask` takes no geometry, and mask
  serialization is write-broken (`applyDevelopSettings` with a hand-built mask
  renders nothing *and* replaces the mask array). Confirmed dead end; do not
  build a "place" command. Geometric masks land at Lightroom's default spot only.
  For a shaped local burn or dodge, place or refine the brush, gradient or
  radial in Lightroom's UI when needed, then verify the coverage by render.
- `mask reset` clears all masks. **Do not probe masks with repeated `undo`** on
  a live edit: undo can over-rewind past the whole edit and redo may not recover
  it. Restore a pre-edit `snapshot-apply` (verified to bring back sliders AND
  the mask stack). Do not globally `reset` a live edit just to recover from a
  probe: delete only the disposable test mask when that is enough.
- `mask add/subtract/intersect` apply trailing `local_*` values too (plug-in
  1.1.0+), and `mask invert` works without an id on the currently selected mask.

## Reviewing and culling a folder ("find the best shots")

The bridge only "sees" *targeted* photos. To review a folder, have the user
press **Cmd+A (Select All)** in the grid first, then use the helper:

```bash
python3 bridge/review_folder.py            # renders all selected + builds contact sheets
# -> Read the printed sheets/sheet*.jpg, pick the best by their #NN index
python3 bridge/review_folder.py --flag 12,22,37,53,67   # flag those picks as Picks
```

How it works (do this manually if the helper is unavailable):

- `./lrc list-selected` gives all photos with `uuid` and `filename`.
- Render each **by uuid** (read-only, does not touch the catalog):
  `./lrc render --uuid <uuid> --out /tmp/lrreview/NN.jpg --size 800`.
- Tile them into labeled contact sheets with Pillow (`pip3 install --user
  Pillow`) and `Read` the sheets, far cheaper than reading 60+ images
  individually.
- Flag a specific photo: `./lrc flag pick --uuid <uuid>` (or `rating 5 --uuid
  ...`, `label green --uuid ...`). This writes directly to that photo via
  `set_metadata` and the response echoes the read-back (uuid, rating,
  pickStatus, colorLabel), so it IS the verification. No select-then-flag dance.
- Judge on light, composition, focus and moment, not current brightness (RAWs
  look flat and dark before editing). Call out near-duplicate clusters so the
  user can cull.

### Star-rating at scale: the two-stage funnel

Resolution matters more than the model. Do not rate the keeper tier from
thumbnails. This funnel is a **persistent tool: `bridge/cull.py`**; do not
re-create it in `/tmp`.

```bash
python3 bridge/cull.py triage                  # Stage 1: render all targeted + labeled sheets + manifest
#   -> Read sheets/*.jpg; rate each cell by its KEY (sheet+cell, e.g. A3) into a CSV
python3 bridge/cull.py hires --from-csv ratings.csv --min 4   # Stage 2: full-res keepers, one file each
python3 bridge/cull.py writeback ratings.csv   # verified catalog writeback (--dry-run to preview)
```

Outputs land in a `/tmp` workdir (disposable); the script is the durable part.

1. **Triage (all photos):** ~800px contact sheets, cheap subagents reject,
   dedupe and rough-star. Label cells with short `(sheetID, cell#)` keys mapped
   to the photo on your side; never have agents transcribe long indices, they
   drift and collide.
2. **Top tier (the keepers at 4 stars or more):** render **one image per file**
   (`render --uuid`, ~2560px) and re-rate with the strongest model using SMALL
   comparative sets (pairs or triads, shuffled order) anchored to the rubric,
   not raw star numbers, judging *edited potential* (do not penalize flat or
   dark RAWs). Only this reliably yields 4 and 5 stars and recovers the night
   and blue-hour heroes thumbnails bury.

Model ranking for aesthetic calls: **Opus > Sonnet > Haiku**. **Write ratings
with `./lrc rating <n> --uuid <id>`** (plug-in `set_metadata`): it writes
directly to that photo, is immune to the stale-selection bug where a selection
goes stale mid-burst and the rating hits the wrong photo, and its response
echoes the read-back for verification. Avoid selection-based `rating` (no
`--uuid`) in bursts; for large culls, `bridge/writeback_fast.py` does one call
per photo. AI ratings are advisory; suggest a final 1:1 sharpness pass by eye
before publishing. For the purely technical cull (focus, blinks, duplicates),
Lightroom's native Assisted Culling (15.4+) beats the LLM funnel.

## Long jobs

Renders, culls and exports can run for a long time. If the environment offers a
keep-awake tool, start it at the beginning of a long job and stop it the moment
the job is done, never leaving it running. If there is no such tool, proceed.

## Tidiness

- Clear a color label with `./lrc label none` (valid: none, red, yellow, green,
  blue, purple).
- After exploratory edits on a user's photo, restore a clean state (`reset` then
  re-apply the intended edits, or `snapshot-apply` the pre-edit snapshot) and
  restore neutral metadata if you changed it.

## When unsure of a parameter or value

`./lrc settings` shows every current key; `./lrc range <Param>` shows min and
max; `./lrc help` lists every command the plug-in supports.
