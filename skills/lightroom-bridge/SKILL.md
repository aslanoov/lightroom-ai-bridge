---
name: lightroom-bridge
description: >-
  Use this whenever the user wants to edit, develop, rate, or cull their real photos in Adobe
  Lightroom Classic on this Mac, via the local "Claude Bridge" plug-in and its `lrc` CLI. This is
  a photographer's Develop-module and library-triage workflow, not generic image editing:
  exposure, shadows/highlights, contrast, whites/blacks, white balance and temperature, vibrance,
  clarity, dehaze, color grading, HSL; crop, straighten, rotate; masking the sky, subject, or
  background (AI, brush, gradient, radial); auto-tone; presets; before/after comparison; and
  star-rating, flagging, or culling keepers vs. blurry or rejected frames, acting on the currently
  selected shots or local RAW files (.ARW/.CR2/.NEF/.DNG). Trigger on this intent even when
  "Lightroom" or the plug-in isn't named: any request to adjust, mask, tone, rate, or cull selected
  photos or raws fits (e.g. "open up the shadows on this shot", "darken the sky", "which of these
  is best", "cull the weekend trip", "rate my selects"). Do NOT use for AI image generation,
  background removal, applying a filter to a single profile or product pic, plain
  exporting/resizing, keyword or caption tagging, video color grading, or general photography
  advice.
---

# Lightroom Classic editing via Lightroom AI Bridge

This skill lets you edit and review the user's photos in a **real, running Adobe
Lightroom Classic** on this Mac. A plug-in ("Claude Bridge") exposes the Develop
module over a local socket; you drive it with a CLI. Edits are real,
non-destructive Develop steps (logged in Lightroom's History as "Claude: ...")
and fully undoable.

## Locate the CLI (once per session)

```bash
command -v lrc            # installed via pip/uvx -> just use:  lrc <command>
```

If that prints nothing, the user has a source checkout. Find it and use its
wrapper:

```bash
LRB="${LIGHTROOM_BRIDGE_HOME:-$HOME/lightroom-ai-bridge}"   # ask the user if this misses
"$LRB/lrc" <command>
```

Below, `lrc` means "whichever of the two forms works here". Pick one at the
start of the session and stay consistent. Add `--raw` for compact one-line JSON
(good for parsing). Full reference: the repo's `README.md` and
`docs/COMMAND-REFERENCE.md`; `CLAUDE.md` in the repo is the same operating
cheat-sheet in shorter form.

## First, every time: connect

```bash
lrc ping
```

`ping` returns the active photo and module when all is well. It needs:

- Lightroom Classic **open**, with the **"Claude Bridge" plug-in enabled**
  (File > Plug-in Manager) and running, and
- a photo (or photos) **selected** in Lightroom.

The first call auto-starts a small background daemon, nothing to launch by hand.
If `ping` says *"Lightroom not reachable"*, tell the user to open Lightroom and
make sure the plug-in is enabled and started (File > Plug-in Extras > *Claude
Bridge: Show Status* / *Start*). Do not try to "fix" it from the shell, it is a
UI step.

## Golden rule: look -> edit -> look

You can SEE the photo, so never edit blind. Render a preview, inspect it with
the Read tool, edit, render again, compare.

```bash
lrc render --out /tmp/before.jpg --size 1500   # then Read /tmp/before.jpg
lrc settings                                   # current Develop values
# ...make edits...
lrc render --out /tmp/after.jpg --size 1500    # then Read /tmp/after.jpg
```

Use **`render`** for before/after: it is a full export and always reflects the
current edit (crop, masks, everything). `thumb` is a faster preview-cache
shortcut but Lightroom often refuses it for the active photo mid-edit; the CLI
auto-falls back to `render`, so it still works, just slower on a miss.

## Use the knowledge base

The repo carries a self-improving knowledge base at `kb/` (contract:
`kb/README.md`). **Recall before you cull or edit**, and **write a lesson before
you finish**:

```bash
python3 "$LRB/bridge/kb.py" recall --genres travel,street --task edit
python3 "$LRB/bridge/kb.py" new --title "..." --genres travel --tags wb,night
python3 "$LRB/bridge/kb.py" bump <card-id>     # a card's guidance worked
```

**A fresh install ships the KB empty.** `recall` printing "no cards matched" is
a normal state, not a failure: work from first principles, then capture what you
learned. Write a lesson card when the user corrects you, when a card's starting
point was clearly wrong, when a new recipe worked, or when you notice a
consistent taste preference across 2 or more photos. That loop is what makes the
next session better than this one.

## Command cheat-sheet

Edits act on the **active (most-selected) photo**; the plug-in auto-switches to
the Develop module. Sliders take **friendly names** (process-version mapping is
automatic).

```bash
lrc set Exposure=0.35 Contrast=12 Shadows=25 Highlights=-20 Vibrance=10 Blacks=8
lrc wb --temp 5400 --tint 6              # or:  wb --as Daylight
lrc auto-tone                            # or:  auto-wb
lrc crop --left 0.05 --top 0 --right 0.95 --bottom 1 --angle -1.2   # edges are 0..1
lrc crop-reset
lrc reset                                # reset all   |   reset Texture (one param)
lrc value Exposure                       # get a value |   value Exposure 0.5 (set)
lrc range Exposure                       # min/max for a param
lrc rotate left|right
lrc apply Exposure2012=0.5 CropLeft=0.1 --targets selected   # RAW keys, batch
lrc preset list   |   preset apply "Adaptive: Landscape"
lrc preset save "Name" --uuid <id> [--dry-run]   # save current edit as a UI-visible
                                         # preset (loads at next LrC restart)
lrc snapshot "name"                      # restore point before multi-step edits
lrc snapshots                            # list  |  snapshot-apply "name"  (SAFE
                                         # rollback, cannot over-rewind like undo)
lrc undo   |   redo                      # responses include canUndo/canRedo
lrc rating 5   |   flag pick|reject|none   |   label red|none
lrc rating 4 --uuid <id>                 # verified writeback: writes DIRECTLY to
                                         # that photo, response echoes read-back
lrc call <anyPluginCmd> k=v ...          # escape hatch; `help` lists all commands
```

`render` extras: `--size 0` = native resolution, `--timeout <s>` bounds a
stalled export (AI-mask compute). `preset apply --uuid <id>` for exact matches;
`settings/rotate/snapshot --uuid` work without changing the selection. Common
slider names: `Exposure Contrast Highlights Shadows Whites Blacks Texture
Clarity Dehaze Vibrance Saturation Temperature Tint`, HSL like
`SaturationAdjustmentBlue`, and `local_*` inside masks. `settings` dumps every
current key; `range <Param>` gives limits; `help` lists every plug-in command.

## Masking (verified working)

```bash
lrc mask create aiSelection sky local_Exposure=-0.5 local_Temperature=-6
lrc mask create aiSelection subject local_Exposure=0.3 local_Clarity=8
lrc mask create radialGradient local_Exposure=0.4      # NO subtype for geometric masks
lrc mask adjust local_Clarity=15                       # tweak the current mask
lrc mask reset
```

- Types: `brush gradient radialGradient rangeMask aiSelection`. Subtypes (only
  for `rangeMask`/`aiSelection`): `subject sky background objects people
  landscape color luminance depth`. Passing a subtype to
  brush/gradient/radialGradient is rejected by Lightroom.
- **AI masks under-apply on creation.** Reliable pattern: `mask create` ->
  `mask adjust local_*=...` to re-push the values -> confirm by **render**. One
  re-push is a minimum; budget up to three with a few seconds between them.
- **Phantom creates: `ok` is not proof.** Subtypes that need a click in the UI
  (`rangeMask luminance`, `aiSelection objects`) can create nothing. Confirm
  existence with `mask list` on LrC 15.4+.
- **Mask value read-back lies.** `settings` reports mask `local_*` as `0`.
  Verify mask VALUES by rendering, never by reading them back.
- **Masks cannot be positioned** at chosen coordinates. Geometric masks land at
  Lightroom's default spot; for a fixed placement, bake it into a Develop preset
  in the UI and use `preset apply`.

## Reviewing / culling a whole folder ("find the best shots")

The bridge only sees *targeted* photos, so have the user press **Cmd+A (Select
All)** in the folder grid first. Then use the helper, which renders every photo
(by UUID, read-only) and builds labeled contact sheets you can scan cheaply:

```bash
python3 "$LRB/bridge/review_folder.py"                 # -> sheets at /tmp/lrreview/sheets/*.jpg
# Read those sheets, choose the best by their #NN index, then:
python3 "$LRB/bridge/review_folder.py" --flag 12,22,67 # flag those as Picks
```

Requires Pillow (`pip3 install --user Pillow`). Manual fallback: `lrc
list-selected` -> render each `--uuid` -> flag via `lrc flag pick --uuid <id>`
(writes directly to that photo; the response echoes the read-back, so it IS the
verification). Judge on light, composition, focus, and moment, **not** current
brightness (RAWs look flat and dark before editing). Call out near-duplicate
clusters so the user can cull, and recommend one keeper per cluster.

## Rating / quality-scoring at scale ("analyze and rate these images")

For star-rating many photos (hundreds to thousands), use a **two-stage funnel**.
**Input resolution matters more than the model.** The durable implementation is
`bridge/cull.py` (`triage` -> `hires` -> `writeback`); do not re-create it in
`/tmp`.

1. **Triage on thumbnails (all photos), with cheaper subagents.** Render small
   (~800px) per-folder contact sheets and have parallel subagents reject
   throwaways, collapse near-duplicate bursts (keep 1 or 2 best), and assign
   rough stars. Thumbnails are fine HERE but **useless for the top tier**: at
   ~300px per tile you cannot confirm tack-sharp focus, so a strict 5-star bar
   collapses to nearly zero and dark or blue-hour frames look "flat" and get
   wrongly down-rated.
2. **Top tier on hi-res, with the strongest model.** Take the keepers (union of
   any pass at 4 stars or more), render each **one image per file**
   (`render --uuid`, ~2560px), and re-rate using SMALL comparative sets (pairs
   or triads, shuffled order, rubric-anchored: absolute star numbers correlate
   poorly with human judgment, comparisons are where models are strongest),
   judging **edited potential**. Do NOT penalize flat or dark RAWs. This is the
   only reliable way to assign 4 and 5 stars, and it recovers night and
   blue-hour heroes that the thumbnail pass buries.

For the TECHNICAL cull (focus, blinks, duplicates), prefer Lightroom's native
Assisted Culling (LrC 15.4+, free, full-res) before the LLM funnel: blur
discrimination is the weakest LLM vision dimension at any resolution.

Hard-won rules:

- **Model ranking for aesthetic judgment: Opus > Sonnet > Haiku.** Use the cheap
  tier for triage, the strongest for the tier that gets published.
- **Label sheet cells with short, self-validating `(sheetID, cell#)` keys**
  mapped deterministically to the photo on your side. Never have agents
  transcribe long global indices; they misread and drift on long sequences and
  silently collide.
- **Validate full coverage** against the manifest before any writeback.
- **Verify each rating by read-back and retry when writing to the catalog.**
  Selection can go stale mid-burst, and a selection-based `rating` then hits the
  WRONG photo while still returning ok. Prefer `rating <n> --uuid <id>`, which
  writes directly and echoes the read-back.
- **AI ratings are advisory.** Recommend a final 1:1 sharpness pass by eye
  before publishing.

## Editing model and gotchas

- Non-destructive: every change is a History step ("Claude: ..."). **Before a
  multi-step edit, create a named snapshot; restore it with `snapshot-apply` if
  the edit goes wrong.** Chained `undo` can over-rewind past the whole edit.
  After exploratory edits on a real photo, leave it in a clean state (restore
  the pre-edit snapshot, or `reset` and re-apply the intended look) and restore
  any metadata you changed (`rating 0`, `flag none`, `label none`).
- **The `applied`/`ok` echo is NOT commit confirmation.** Three confirmed races:
  the FIRST edit call while the photo sits in Library is eaten by the module
  switch (re-issue it); a render fired right after `set`/`preset apply` can
  serve the PREVIOUS state (settle ~3s, confirm with `value <Param>`); and
  phantom mask creates. Verify by read-back, then by render.
- Batch across selected photos with `apply ... --targets selected` (RAW keys
  from `settings`); `wb --targets selected` batches Kelvin WB on RAW/DNG too.
  Single-photo friendly `set` acts on the active photo only. For a verified
  per-photo batch over an explicit list, use `bridge/batch_apply.py`, which
  reads back and re-issues the keys that did not land.
- Render-by-UUID (`render --uuid <id>`) and the `--uuid` flags on
  `rating/flag/label/settings/metadata/snapshot/rotate` let you work on a
  specific photo without disturbing the user's selection, ideal for culling.
- The user browsing in Lightroom moves the active photo between commands.
  Verify `active.uuid` before every mutating call in a long session.
- If you change the plug-in's Lua, apply it via Plug-in Manager > Plug-in Author
  Tools > **Reload Plug-in**. As of plug-in 1.1.0 this hands off cleanly (the
  instances coordinate via `~/.claude-lrc-bridge/instance.gen`), no Lightroom
  restart needed. The Lua sandbox has no `setfenv`, and you cannot yield across
  a plain `pcall`; the repo README has the full list.
