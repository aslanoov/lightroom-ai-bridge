# The Knowledge Base (`kb/`) - how photo judgment gets into the agent

The bridge gives an AI agent *hands* (select, rate, crop, develop, mask). This
knowledge base gives it *taste and memory*: explicit criteria for how to pick
photos, how to edit them, and, critically, a loop that makes every session
leave the next session smarter.

**This repo ships the knowledge base EMPTY on purpose.** Photographic taste is
personal. A KB full of someone else's preferences would fight yours on every
edit. What ships here is the *contract*: the folder layout, the card format,
the recall/apply/learn loop, and the tooling. Your cards get written by the
agent as you work, from your corrections and your approved edits. See
[Seeding an empty KB](#seeding-an-empty-kb) below for the fast start.

It is **plain Markdown + YAML frontmatter**, so any agent (Claude, Codex, any
model) can use it with nothing but file reads. A small stdlib-only helper,
[`bridge/kb.py`](../bridge/kb.py), adds deterministic recall and indexing on top.

```
kb/
├── README.md          <- you are here (the contract every agent follows)
├── INDEX.md           <- auto-generated card index (rebuild: python3 bridge/kb.py index)
├── core/              <- genre-independent craft: culling rubric, composition,
│                        selection mastery, color & tone theory, edit workflow
├── genres/            <- per-genre cards: portrait, wildlife, landscape,
│                        street-city, nature-macro, travel-documentary
├── editing/           <- concrete technique cards (color grading, skin tones,
│                        crop/recompose, batch sync, export)
├── styles/            <- named looks and your evolving taste profile
├── lightroom/         <- ONLY version-specific Lightroom/bridge knowledge
└── learned/           <- the self-improvement layer: lessons written after
                         sessions, later consolidated into the cards above
```

## Design principles

1. **General over tool-specific.** Photography truths (light, composition,
   moment, color harmony) never expire; Lightroom sliders and SDK quirks do.
   Cards default to `scope: general`. Anything tied to a Lightroom version
   lives in `kb/lightroom/` with a `lightroom_min` version, so when Lightroom
   16/17 arrives you revise one folder, not the whole KB.
2. **Every claim must be actionable.** A card line should be checkable against
   a rendered JPEG ("eyes sharpest point? edge intrusions?") or executable as
   an edit ("shadows toward teal hue around 200, sat 8-15, then balance
   highlights with the opposite color"). No vibes-only advice.
3. **Knowledge has a confidence lifecycle.** `seed` (from research/reading) ->
   `tested` (worked in at least one real session) -> `proven` (validated
   repeatedly or user-confirmed). Contradicted knowledge gets corrected or
   deleted, not hoarded.
4. **The render is the ground truth.** The KB never overrides the golden rule:
   look -> edit -> look. Cards give starting points and judgment criteria; the
   before/after render decides.

## Card format

Every card is one Markdown file with this frontmatter:

```yaml
---
id: genre-portrait              # unique, kebab-case, stable
title: "Portrait photography: selection and editing knowledge"
type: technique                 # rubric | technique | recipe | style | lesson | reference
scope: general                  # general | lightroom
genres: [portrait]              # or [all]; lowercase list
tags: [skin, eyes, light]       # 4-8 lowercase kebab tags
source: research-2026-07        # research-YYYY-MM | session | user | <your source>
confidence: seed                # seed | tested | proven
evidence: 0                     # count of sessions where this was applied successfully
lightroom_min: n/a              # e.g. "15" - only meaningful when scope: lightroom
created: 2026-07-11
updated: 2026-07-11
---
```

Types: **rubric** = scoring criteria; **technique** = how/why knowledge;
**recipe** = a concrete slider/mask sequence; **style** = a named look;
**lesson** = a session learning (lives in `learned/`); **reference** = pointers.

## The loop every agent must run: RECALL -> APPLY -> LEARN

### 1. RECALL (before touching a photo)

- Read `kb/INDEX.md` (one line per card, cheap).
- **Classify the photo(s) first**: genre (portrait / wildlife / landscape /
  street / nature / travel-documentary), light situation (golden hour, midday,
  blue hour, night, interior), and intent if the user stated one ("moody",
  "clean", "print"). Classify from a *render*, not the filename.
- Load the matching cards: always `core/culling-rubric.md` (for culling) or
  `core/edit-workflow.md` (for editing), plus the genre card, plus any
  editing/style cards the task touches. Helper:
  `python3 bridge/kb.py recall --genres travel,street --task edit` prints the
  matching card paths (add `--full` to print contents).
- `styles/user-taste.md` is ALWAYS loaded for edits once it exists: it is the
  user's accumulated preferences and overrides genre defaults on conflict.
- **If a card does not exist yet, say so and work from first principles.** An
  empty KB is a valid state, not an error. Whatever you work out in that
  session is exactly what step 3 captures.

### 2. APPLY (while working)

- Culling: score with the rubric in `core/culling-rubric.md` rather than
  ad-hoc criteria, and cite rubric line items when reporting picks ("A3: 5
  stars, peak gesture, layered composition, keeper light").
- Editing: start from the genre card's starting points, adjust by render.
  Follow the crop A/B protocol in `editing/crop-recompose.md` (propose crop
  with a stated reason -> render -> compare -> keep or revert).
- Precedence on conflict: `learned/` (fresh, user-specific) >
  `styles/user-taste.md` > genre card > core theory. Newer and more specific wins.

### 3. LEARN (before ending the session)

Write a lesson card into `kb/learned/` when, and only when, one of these
happened:

- **User correction**: they overrode your call ("too warm", "crop was better
  before", "I actually like crushed blacks here"). Capture what, why, and the
  numbers involved.
- **Surprising outcome**: a starting point from a card was clearly wrong for a
  legible reason (e.g. "Sony A7 files at ISO 6400 need Luminance NR around 25,
  the card's 10 left visible chroma noise").
- **New recipe that worked**: a slider/mask sequence the user approved that no
  card covers.
- **Taste signal**: a consistent preference observed across 2 or more photos
  this session (goes toward `styles/user-taste.md` at consolidation).

Do NOT save: one-off trivia, anything the render already proves each time,
restatements of existing cards (bump their `evidence` instead), or session
mechanics (that belongs in CLAUDE.md).

Scaffold: `python3 bridge/kb.py new --title "..." --genres travel --tags wb,night`
creates `learned/YYYY-MM-DD-<slug>.md` from the template. Fill in the
**Situation / What happened / Rule to carry forward** sections.

Also: when you successfully apply an existing card's guidance and the render
plus the user confirm it, bump it: `python3 bridge/kb.py bump <card-id>`
(increments `evidence`; at 3+ promote `confidence` seed->tested, at ~10
tested->proven).

### 4. CONSOLIDATE (light pass - when `kb.py stats` says due)

Trigger: 10 or more unmerged lessons, or monthly, or when the user asks.

1. Read every `learned/*.md` with `merged: false`.
2. Merge each durable lesson into its target card by **rewriting the affected
   section so it reads as if written with that knowledge from the start**.
   Never append an "update:" note. Cards are living documents, not changelogs.
3. Taste signals accumulate into `styles/user-taste.md`.
4. Contradictions: the render and user feedback win; fix or delete the losing
   claim (an inline correction note is allowed when instructive).
5. For each card that received knowledge: `python3 bridge/kb.py absorb <id>`
   (counts how much merged-in knowledge a card carries since its last full
   rewrite) and bump `updated`. Mark the lesson `merged: true` (keep the
   file, it is the audit trail). Rebuild: `python3 bridge/kb.py index`.

### 5. REVISE (deep pass - knowledge improves, it does not just accumulate)

Consolidation keeps cards *current*; revision keeps them *good*. `kb.py stats`
flags when a card has earned one:

- **REWRITE DUE**: the card absorbed 4 or more lessons since its last full rewrite.
- **STALE SEED**: seed-confidence content untouched for 6+ months with zero
  evidence. Re-verify it against practice or refresh the research behind it.

Also revise on structural smells regardless of flags: two cards overlap, one
card serves two different jobs, a genre has outgrown a single file.

A revision **re-synthesizes the entire card from scratch**: current body, plus
the audit trail of its lessons in `learned/` (merged ones included), plus fresh
research if warranted (fire research subagents exactly like the original
seeding), plus anything new in `knowledge/`. Prune claims that evidence
contradicted, promote what proved out, restructure the sections, keep it
dense. Then reset the counter (`kb.py absorb <id> --reset`) and bump
`updated`.

**Restructuring the KB itself is allowed and expected**: split oversized
cards, merge overlapping ones, retire cards whose content proved wrong (fold
any surviving fragments into the winner first). Keep `id`s stable when content
moves; the INDEX regenerates.

**New seed knowledge follows the same rule as lessons**: a new article in
`knowledge/`, a new research wave, a new preset. Weave it into the existing
cards by rewriting them; create a new card only when no natural home exists.
The KB is a book kept in print through new editions, not a first edition with
a pile of errata.

**Ingesting `knowledge/` drops: inbox, then archive.** `knowledge/` is an
**inbox** where you drop raw reference material (articles, presets,
before/after sets, XMPs, PDFs). After an agent has extracted a drop's knowledge
into the cards, it **moves the source file(s) into `knowledge/processed/`**, the
archive, so the top level of `knowledge/` always shows only what still needs
ingesting. Three invariants when moving a file:

- **Never overwrite on move (name-collision safety).** Drops reuse generic
  names (`before-after`, `preset.xmp`, `1.jpg`), so a bare `mv X processed/`
  can silently clobber a same-named archived file (data loss) or nest a
  directory confusingly. Check first, and on a collision disambiguate with the
  ingest date:
  ```bash
  for name in *; do                                   # run inside knowledge/
    [ "$name" = processed ] && continue
    dest="processed/$name"
    [ -e "$dest" ] && dest="processed/$(date +%F)-$name"   # collide -> date-prefix
    mv -n -- "$name" "$dest"                                # -n = never clobber
  done
  ```
  If the date-prefixed name still exists (two same-named drops in one day), add
  a numeric suffix. For a large multi-file batch, archiving the whole batch
  under a dated subfolder `knowledge/processed/<YYYY-MM-DD>/` is collision-proof
  by construction. Cite whatever final path the file landed at.
- **Keep citations resolvable.** If any card cites a moved file by path,
  update the reference to its final `knowledge/processed/...` path in the same
  pass (grep `knowledge/` across `kb/` first).
- **Archive, never delete.** The originals are provenance and may be re-mined
  on a deep revision (section 5 re-synthesis reads `knowledge/`). Only move to
  `processed/`; if a drop is only *partly* extracted, leave it in the inbox
  until it is fully mined.

`knowledge/processed/` is excluded from the "what is new to ingest?" scan. When
looking for un-processed drops, read the top level of `knowledge/` only.

## Seeding an empty KB

You have three ways to get from zero cards to a working KB. They combine well.

1. **Just work (recommended).** Edit and cull with the skills. Every time you
   correct the agent ("too warm", "I hate vignettes", "keep the symmetry"), it
   writes a lesson into `learned/`. After a handful of sessions, ask it to
   consolidate, and your first real cards appear. This KB is *yours* from the
   first line, which is the whole point.
2. **Ask for a research seeding pass.** One prompt is enough:
   > Seed my photography knowledge base. Research the craft (culling criteria,
   > composition, color and tone, the standard edit order, and the genres I
   > shoot: travel, street, landscape), then write the `core/`, `genres/` and
   > `editing/` cards per `kb/README.md`, all at `confidence: seed`.

   Seed cards are hypotheses, not law. Practice promotes or kills them.
3. **Feed it your references.** Drop newsletters, presets, XMPs, award-winner
   collections or before/after pairs into `knowledge/` and ask the agent to
   ingest them into cards. Your own exported presets are especially good: they
   are your taste in numeric form.

Whichever route you take, `styles/user-taste.md` is the card that matters most,
and it can only be written by observing you. Do not import someone else's.

## Lightroom versions

- `kb/lightroom/` cards carry `scope: lightroom` and `lightroom_min`. Check
  the running version if in doubt (`./lrc ping` reports it) and prefer the
  newest card whose `lightroom_min` is satisfied.
- When a new Lightroom Classic version changes behavior (new sliders, changed
  ranges, fixed or introduced SDK quirks): add or update a card in
  `kb/lightroom/`, never bake version quirks into `core/` or `genres/`.
- Slider *semantics* (what Clarity does, what the grade wheels mean) are
  general and belong in `core/` or `editing/`; slider *names, ranges and SDK
  behavior* are version-scoped and belong in `kb/lightroom/`.

## For agents that are not Claude Code

Everything above works with plain file reads and writes. If you cannot run
Python, skip `kb.py` and: read `INDEX.md`, open the cards whose `genres` or
`tags` match your task, and append lessons to `learned/` following
`learned/TEMPLATE.md`. The format is the contract; the tooling is convenience.

## Provenance and honesty rules

- Every card states its `source`. Research-seeded cards cite URLs in a
  `## Sources` section.
- Never present `seed` knowledge as settled fact to the user; it is a starting
  hypothesis until renders and feedback move it to `tested` or `proven`.
- Cards must not contain private data (no client names, no image contents
  beyond what the lesson needs). Your `kb/` is yours; if you fork this repo
  publicly, keep your cards out of the public remote.
