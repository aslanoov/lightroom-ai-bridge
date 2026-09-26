# Lightroom AI Bridge

Let Claude edit your photos in a **real, running Adobe Lightroom Classic**.

Not an AI image generator, not a filter app. This drives Lightroom's actual
Develop module: exposure, white balance, tone, color grading, HSL, crop and
straighten, AI masks (select sky, select subject), presets, ratings and flags,
snapshots, undo. Every change is a normal, non-destructive Develop step that
shows up in your History panel as "Claude: ...", on your own RAW files, in your
own catalog. Your originals are never touched.

The trick that makes it work: Claude can **see** the photo. It renders a JPEG
through Lightroom's pipeline, looks at it, edits, renders again, and compares.
Look, edit, look.

```
   you / Claude                 this repo                         Lightroom Classic
 +--------------+   commands  +--------------+   TCP sockets    +----------------------+
 |  ./lrc set   | ----------> |  lrc CLI     |  49463 commands  | ClaudeBridge         |
 |  ./lrc crop  |             |  + daemon    | ---------------> |  .lrplugin           |
 |  ./lrc render| <---------- |              |  49464 responses |  (LrDevelopController|
 +--------------+   JSON      +--------------+ <--------------- |   LrSelection, ...)  |
        ^                            |  writes JPEG previews    +----------------------+
        |   reads preview .jpg       v        to ~/.claude-lrc-bridge/previews/
        +-------------------  ~/.claude-lrc-bridge/previews/*.jpg
```

It is built entirely on Adobe's public **Lightroom Classic SDK** using the same
socket-based "external control" mechanism Adobe documents in the *Controller SDK
Guide* (the approach hardware vendors use to map a physical knob to a Develop
slider). No private APIs, no screen-scraping, localhost only.

**New here? Read [docs/USER-GUIDE.md](docs/USER-GUIDE.md).** It walks from "I
have Lightroom and nothing else" to a finished edit, in order, with what to type.

> **One name to know:** inside Lightroom the plug-in appears as **Claude Bridge**
> (in the Plug-in Manager and the Plug-in Extras menu). That is this project.

---

## What you get

| Piece | What it is |
|---|---|
| `ClaudeBridge.lrplugin/` | The Lightroom plug-in. Install it once into Lightroom. |
| `lrc` CLI | 40+ commands: tone, WB, crop, masks, presets, ratings, snapshots, render. Pure Python 3 stdlib. |
| MCP server | The same operations as 46 native tools for any MCP client (Claude Desktop, Claude Code, others). |
| `skills/` | Two Claude Code skills: `/lightroom-bridge` (everyday editing and culling) and `/lightroom-bridge-pro` (a full professional edit of the selected shot, zero questions). |
| `kb/` | A self-improving knowledge base of photographic judgment. **Ships empty on purpose.** It fills with *your* taste as you work. |
| `bridge/` | Durable helper scripts: contact-sheet review, the two-stage culling funnel, verified batch apply, tilt measurement, attention-hierarchy check. |

**Requirements:** macOS and Adobe Lightroom Classic **15.3 or newer**. The `lrc`
CLI needs only **Python 3** (already on macOS, no third-party packages). The MCP
server additionally needs `fastmcp` and Python **3.10+**, both provisioned
automatically by `uvx`. Pillow (`pip3 install --user Pillow`) is needed only for
the contact-sheet helpers. macOS only: the bridge uses a Unix domain socket and
`~/Library` paths; Windows and Linux are untested.

---

## Install in three steps

### 1. The Lightroom plug-in (once)

1. Open Lightroom Classic.
2. **File > Plug-in Manager...**
3. Click **Add**, choose this repo's `ClaudeBridge.lrplugin` folder, click **Add Plug-in**.
4. It should read **Status: This plug-in is enabled and running.**

The plug-in starts with Lightroom from then on, and shows a brief "Claude Bridge
running" badge in the top-left when it does.

### 2. Check the connection

Select a photo in Lightroom, then:

```bash
./lrc ping
```

```json
{ "ok": true, "result": {
    "plugin": "Claude Bridge",
    "lightroom": "Adobe Lightroom Classic ...",
    "module": "develop",
    "active": { "filename": "DSC_0001.ARW", "uuid": "...", "path": "..." } } }
```

The first call quietly launches a background daemon that keeps the Lightroom
connection open; later calls are instant.

### 3. Pick how you want to drive it

**A. Claude Code with skills (recommended).**

```bash
./skills/install.sh
```

Then in Claude Code: `/lightroom-bridge` or `/lightroom-bridge-pro`, or just say
"open up the shadows on this shot". Details: [`skills/README.md`](skills/README.md).

**B. Any MCP client.** Claude Desktop: *Settings > Developer > Edit Config*:

```json
{ "mcpServers": { "lightroom": {
    "command": "uv",
    "args": ["run", "--directory", "/absolute/path/to/this/repo", "lightroom-bridge"]
} } }
```

In Claude Code that is one line:

```bash
claude mcp add lightroom -- uv run --directory /absolute/path/to/this/repo lightroom-bridge
```

Once the package is published to PyPI, `uvx lightroom-bridge` replaces the
`--directory` form. The MCP server exposes the same operations as 46 tools, with
before/after previews returned as inline images, plus seven `kb_*` tools for the
knowledge base. It shares one daemon with the CLI, so the two cooperate rather
than fight over the single-client plug-in.

**C. Plain shell.** Use `./lrc ...` directly, or `pip install -e .` to get `lrc`
on your `PATH`.

---

## Two minutes of it

```bash
./lrc render --out /tmp/before.jpg --size 1500   # look first
./lrc settings                                   # read the numbers

./lrc set Exposure=0.35 Contrast=12 Shadows=25 Highlights=-20 Vibrance=10
./lrc wb --temp 5400 --tint 8
./lrc crop --left 0.05 --top 0 --right 0.95 --bottom 1 --angle -1.5
./lrc mask create aiSelection sky local_Exposure=-0.5 local_Temperature=-6
./lrc mask adjust local_Clarity=12               # AI masks need the re-push

./lrc render --out /tmp/after.jpg --size 1500    # look again
./lrc undo                                       # or snapshot-apply to roll back
```

Full command reference: [docs/COMMAND-REFERENCE.md](docs/COMMAND-REFERENCE.md).
Run `./lrc <command> -h` for any command's flags, or `./lrc help` to ask the
plug-in what it supports.

---

## The knowledge base: why this gets better over time

A capable model with no memory re-derives your taste from scratch every session
and gets it slightly wrong every time. `kb/` fixes that. It is plain Markdown
cards (culling rubric, genre knowledge, editing recipes, named looks, and your
taste profile) plus one rule the agent follows every session:

**RECALL** the cards that match this photo, **APPLY** them, **LEARN** from what
happened. Your correction becomes a lesson card; ten lessons get consolidated
into the cards they belong to; cards that earn their keep get promoted from
`seed` to `tested` to `proven`. It is a loop, not a pile.

**This repo ships the KB empty, and that is deliberate.** Taste is personal;
someone else's cards would argue with you on every edit. Three ways to fill it,
all described in [`kb/README.md`](kb/README.md):

- just work, and let your corrections accumulate (the honest way),
- ask Claude for a research seeding pass to write `seed` cards for the genres
  you shoot,
- drop your own references (presets, XMPs, newsletters, before/after pairs) into
  `knowledge/` and have them ingested.

`bridge/kb.py` gives the loop deterministic tooling: `recall`, `read`, `index`,
`new`, `bump`, `absorb`, `validate`, `stats`. Every one of them degrades
gracefully on an empty KB.

---

## Culling at scale

For rating hundreds or thousands of frames there is a two-stage funnel in
`bridge/cull.py`, and the reasoning behind it is the part worth stealing:

1. **Triage on contact sheets** (~800px tiles, cheap model, all photos): reject
   throwaways, collapse near-duplicate bursts, assign rough stars.
2. **Judge the top tier on hi-res** (one ~2560px image per file, strongest
   model, comparative pairs or triads): only this reliably separates 4 stars
   from 5, and it recovers the night and blue-hour frames that thumbnails bury.

Input resolution matters more than the model, and comparisons beat absolute star
numbers. For the purely technical cull (focus, blinks, duplicates), Lightroom's
own Assisted Culling is better than any LLM at any resolution. Details in
[docs/USER-GUIDE.md](docs/USER-GUIDE.md#culling-a-shoot).

---

## Repository layout

```
ClaudeBridge.lrplugin/   the Lightroom plug-in (Lua, public SDK only)
src/lightroom_bridge/    the Python package: transport core, lrc CLI, MCP server, KB engine
bridge/                  durable helper scripts (see docs/COMMAND-REFERENCE.md)
skills/                  Claude Code skills + installer
kb/                      knowledge base scaffold (empty; fills with your taste)
knowledge/               inbox for raw reference material you want ingested
docs/                    user guide, command reference, release runbook
scripts/                 release_check.py, the pre-release gate
CLAUDE.md / AGENTS.md    the operating cheat-sheet an agent reads (Claude / other agents)
```

**Not included:** Adobe's Lightroom Classic SDK and its PDF guides. They are
Adobe's material with redistribution restrictions, and they are not required at
runtime. Download them from Adobe only if you want to modify the plug-in's Lua.

---

## Troubleshooting

**`Lightroom not reachable...`** Is Lightroom Classic open? In Lightroom:
**File > Plug-in Extras > Claude Bridge: Show Status**. If "Run loop active" is
*no*, choose **Claude Bridge: Start / Restart**. Confirm it is enabled in
Plug-in Manager.

**`No active photo...`** Select a photo in Lightroom first. Most edits act on the
active photo.

**Edits do not seem to apply.** The plug-in switches to the Develop module for
slider and mask edits. Watch the History panel: each edit is logged as
"Claude: ...". Note that the very first edit call issued while the photo sits in
Library is eaten by that module switch; re-issue it.

**A preview looks stale, or `thumb` errors.** Use `render`, which is a
guaranteed fresh export that reflects every edit. `thumb` relies on Lightroom's
preview cache and is intermittent for the active photo in Develop; the CLI
auto-falls back to `render`.

**Reset the daemon:** `./lrc daemon stop && ./lrc ping`.

Logs: `~/.claude-lrc-bridge/daemon.log` (bridge side) and the in-Lightroom
**Claude Bridge: Show Status** dialog. More in
[docs/USER-GUIDE.md](docs/USER-GUIDE.md#troubleshooting).

---

## How it works (protocol)

- The plug-in opens two `LrSocket` listeners on localhost (one receive, one
  send) and runs a command loop inside a single async task and function context,
  so every Develop, catalog and export call happens in a valid context.
- **Inbound** (bridge to Lightroom): one **Lua table literal** per line, e.g.
  `{id=7,cmd="set",params={Exposure=0.5}}`. The plug-in parses it with
  `loadstring` inside an **empty sandbox environment**, so a command line can
  only build a data table; it can never call code.
- **Outbound** (Lightroom to bridge): one **JSON** object per line, e.g.
  `{"id":7,"ok":true,"result":{...}}`.
- The Python **daemon** holds both sockets open for the whole session (the
  stable, Adobe-documented model), exposes a Unix socket for the CLI, correlates
  each reply by `id`, and transparently reconnects if Lightroom restarts.

## Security

- Localhost only (127.0.0.1). Nothing is exposed to your network.
- Inbound commands are executed as **data**, not code (sandboxed `loadstring`).
- The bridge can do anything you can do in Develop: treat it like handing an
  assistant your mouse. Everything is visible in History and reversible.

## Status

Tested end-to-end against Adobe Lightroom Classic **15.3 and 15.4** on macOS
with Sony `.ARW` RAW files. Confirmed working: `ping`, `status`, `settings`,
`list-selected`, `help`, `set`, `value`, `range`, `apply`, `wb`, `auto-tone`,
`auto-wb`, `crop`, `crop-reset`, masking (create plus local adjustments,
visibly applied), `reset`, `render` before/after, `preset list` / `apply` /
`save`, `snapshot` and `snapshot-apply`, module switch, `undo` / `redo`, and
`rating` / `flag` / `label` including verified per-UUID writeback.

Two behaviors are handled automatically and worth knowing: `thumb` is
intermittent for the active photo in Develop and silently falls back to
`render`; and mask *values* cannot be read back (`settings` reports them as 0),
so masks are verified by rendering. The full list of verified quirks lives in
[CLAUDE.md](CLAUDE.md), which is also what an agent reads before it starts.

## Get help

Open an [issue on GitHub](https://github.com/aslanoov/lightroom-ai-bridge/issues),
or email support@speranda.com. Release history: [CHANGELOG.md](CHANGELOG.md).

## License

[MIT](LICENSE) (c) 2026 Mustafa Aslanov.

Adobe, Lightroom and Lightroom Classic are trademarks of Adobe Inc. Claude is a
trademark of Anthropic. Lightroom AI Bridge is an independent project and is not
affiliated with, sponsored by, or endorsed by Adobe or Anthropic.

This project bundles no Adobe code. Adobe's Lightroom Classic SDK and its
documentation are Adobe's property and are **not** included in this repository.
Download them from Adobe under Adobe's own license terms.
