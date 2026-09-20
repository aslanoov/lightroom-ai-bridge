# Operating the Lightroom bridge (for Claude)

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

If `ping` fails: tell the user to open Lightroom and ensure the "Claude Bridge"
plug-in is enabled (File > Plug-in Manager) and started (Plug-in Extras menu).
It is a UI step; do not try to fix it from the shell.

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
./lrc preset save "Soft Autumn Portrait" --uuid <id> --dry-run   # then without --dry-run;
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
- **The `applied`/`ok` echo is NOT commit confirmation** (three confirmed
  races): (1) the FIRST edit call while the photo sits in **Library** is
  silently eaten by the module switch, echoes `applied`, reads back 0, so
  re-issue it; (2) a render fired right after `set` or `preset apply` can serve
  the PREVIOUS state, so settle ~3s and confirm with `value <Param>` before
  rendering; (3) phantom mask creates (see Masking). Never conclude a control is
  dead without read-back plus settle: a race and a real no-op look identical in
  renders.
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
- **Phantom creates: `ok` from mask create is NOT proof of creation.** Subtypes
  needing a region or click in the UI silently create nothing: `rangeMask
  luminance` and `aiSelection objects` are confirmed, likely all `rangeMask`
  subtypes and `aiSelection color/depth`. **After every create, confirm with
  `mask list`.** To pull down a blown region a rangeMask cannot reach, use
  global `Whites` (near-surgical when only that region is at or above 250;
  measure per-region percentage first).
- **AI masks under-apply on creation.** The `local_*` values passed to `mask
  create aiSelection ...` often land weak, because the AI compute is not
  finished when they fire. Reliable pattern: **create -> `mask adjust
  local_*=...` to re-push -> verify by render**, and one re-push is a MINIMUM: a
  no-op render does NOT mean the mask failed, so re-push again with 3 to 5
  second pauses (budget about three adjusts). A hard test value
  (`local_Exposure=-1.5`) confirms the mask is live; then dial to taste. Give
  the create its own call; do not bundle grade, HSL or effects into the same
  fast `&&` chain right after it, or they get dropped.
- **Mask VALUE read-back lies; existence read-back works on 15.4+.** `settings`
  reports mask `local_*` values as `0`, so verify values by **rendering** only.
  `mask list` returns correct structure (ids, types, subtypes) on 15.4+ (the
  "always returns []" behavior was 15.3); use it for existence checks and to get
  ids for `mask select` and `mask delete` (which requires an id).
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
- `mask reset` clears all masks. **Do not probe masks with repeated `undo`** on
  a live edit: undo can over-rewind past the whole edit and redo may not recover
  it. Restore a pre-edit `snapshot-apply` (verified to bring back sliders AND
  the mask stack), or rebuild via `reset` plus replay (every edit here is a
  recipe).
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
