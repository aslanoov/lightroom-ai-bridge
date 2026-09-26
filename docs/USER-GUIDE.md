# Lightroom AI Bridge user guide

From "I have Lightroom and nothing else" to a finished edit, in order.

If you already installed the plug-in and just want the commands, go to
[COMMAND-REFERENCE.md](COMMAND-REFERENCE.md).

**Contents**

1. [What this is, and what it is not](#1-what-this-is-and-what-it-is-not)
2. [Before you start](#2-before-you-start)
3. [Install the Lightroom plug-in](#3-install-the-lightroom-plug-in)
4. [Install the CLI](#4-install-the-cli)
5. [Connect Claude](#5-connect-claude)
6. [Your first edit](#6-your-first-edit)
7. [How to talk to it](#7-how-to-talk-to-it)
8. [Everyday workflows](#8-everyday-workflows)
9. [Culling a shoot](#culling-a-shoot)
10. [The safety net](#9-the-safety-net)
11. [The knowledge base: how it gets smarter](#10-the-knowledge-base-how-it-gets-smarter)
12. [Troubleshooting](#troubleshooting)
13. [FAQ](#11-faq)
14. [Uninstall](#12-uninstall)
15. [Get help](#13-get-help)

---

## 1. What this is, and what it is not

**It is** a remote control for Lightroom Classic's Develop module that an AI can
operate while looking at the photo. Claude renders your photo through
Lightroom's own pipeline, inspects the JPEG, moves real sliders, renders again,
and compares. Everything it does is a normal non-destructive Develop step,
recorded in your History panel as "Claude: ...", on your real catalog.

**It is not** an image generator, an upscaler, or a filter pack. It never
rewrites your RAW files. It cannot invent content unless you explicitly ask it
to use Lightroom's own generative tools, and by default it avoids them.

The honest summary of what it is good at:

- One photo taken from flat RAW to finished, with reasoning you can read.
- Culling and rating a large shoot down to the keepers.
- Applying a consistent look across a set, verified per photo.
- Measuring things your eye argues about: tilt, clipping, which region wins
  attention.

And what it is not good at: judging tack-sharp focus from a thumbnail (use
Lightroom's Assisted Culling for the technical cull), and knowing your taste on
day one. The second one fixes itself, see section 10.

---

## 2. Before you start

| You need | Notes |
|---|---|
| macOS | The bridge uses a Unix socket and `~/Library` paths. Windows and Linux are untested. |
| Adobe Lightroom Classic 15.3+ | Lightroom CC (the cloud one) does not have the plug-in SDK. Classic only. |
| Python 3 | Already on macOS. `python3 --version` to confirm. |
| Claude Code, or any MCP client | Optional but the whole point. Plain shell use works too. |
| Pillow (optional) | `pip3 install --user Pillow`, only for the contact-sheet culling helpers. |

Get the repo:

```bash
git clone https://github.com/aslanoov/lightroom-ai-bridge.git ~/lightroom-ai-bridge
cd ~/lightroom-ai-bridge
```

No git? Download the source zip of the latest release from the
[Releases page](https://github.com/aslanoov/lightroom-ai-bridge/releases/latest),
unzip it, and rename the folder to `~/lightroom-ai-bridge`.

`~/lightroom-ai-bridge` is the default path the skills look for. Anywhere else
works, you just tell them where (section 5).

> Inside Lightroom, the plug-in appears as **Claude Bridge**. That is this
> project's plug-in; the menus below use that name.

---

## 3. Install the Lightroom plug-in

You do this once.

**Option A: Plug-in Manager (recommended, keeps the plug-in in the repo so
`git pull` updates it).**

1. Open Lightroom Classic.
2. **File > Plug-in Manager...**
3. Click **Add**, select the `ClaudeBridge.lrplugin` folder inside the repo,
   click **Add Plug-in**.
4. The panel should read **Status: This plug-in is enabled and running.**
5. Click **Done**.

**Option B: the auto-load folder.**

```bash
./lrc install-plugin
```

That copies the plug-in into
`~/Library/Application Support/Adobe/Lightroom/Modules/`. Restart Lightroom
afterwards. This is the right option if you installed the package from PyPI and
have no checkout.

### Confirm it is alive

In Lightroom: **File > Plug-in Extras > Claude Bridge: Show Status**. You want
"Run loop active: yes". The same menu has **Claude Bridge: Start / Restart** if
it is not.

From now on the plug-in starts automatically with Lightroom, and you will see a
brief "Claude Bridge running" badge in the top-left corner of the window at
launch.

> The plug-in listens on `127.0.0.1` only. Nothing is exposed to your network.

---

## 4. Install the CLI

**From the checkout, nothing to install:**

```bash
cd ~/lightroom-ai-bridge
./lrc ping
```

**Or put `lrc` on your PATH:**

```bash
pip install -e ~/lightroom-ai-bridge      # editable: git pull keeps it current
lrc ping
```

Either way, select a photo in Lightroom first, then run `ping`. A healthy
response looks like this:

```json
{ "ok": true, "result": {
    "plugin": "Claude Bridge",
    "lightroom": "Adobe Lightroom Classic 15.4",
    "module": "develop",
    "active": { "filename": "DSC_0001.ARW", "uuid": "...", "path": "..." } } }
```

If you get `Lightroom not reachable`, jump to [Troubleshooting](#troubleshooting).

The first call starts a small background daemon that holds the Lightroom
connection open, so later calls are instant. It stops when you log out, or with
`./lrc daemon stop`.

---

## 5. Connect Claude

### Claude Code with the skills (recommended)

```bash
cd ~/lightroom-ai-bridge
./skills/install.sh
```

This symlinks `lightroom-bridge` and `lightroom-bridge-pro` into
`~/.claude/skills/`, so `git pull` keeps them current. Use `--copy` if you would
rather have independent copies, or `--project /path/to/project` to install them
into one project only.

If your checkout is **not** at `~/lightroom-ai-bridge` and you did not `pip
install`, tell the skills where it is:

```bash
echo 'export LIGHTROOM_BRIDGE_HOME="$HOME/code/lightroom-bridge"' >> ~/.zshrc
```

Then start Claude Code (from anywhere) and type `/lightroom-bridge`. You can
also just describe what you want: the skill triggers on intent, so "darken the
sky on this shot" is enough.

### Any MCP client

The package ships an MCP server that exposes the same operations as 46 native
tools, with before/after previews returned as inline images. Claude Desktop:
*Settings > Developer > Edit Config*:

```json
{
  "mcpServers": {
    "lightroom": {
      "command": "uv",
      "args": ["run", "--directory", "/Users/you/lightroom-ai-bridge", "lightroom-bridge"]
    }
  }
}
```

Claude Code:

```bash
claude mcp add lightroom -- uv run --directory ~/lightroom-ai-bridge lightroom-bridge
```

Then ask the client to "ping Lightroom". Once the package is on PyPI, the
`--directory` form can be replaced with plain `uvx lightroom-bridge`.

The MCP server also exposes seven `kb_*` tools so an MCP client gets the same
knowledge-base loop the skills use. It finds `kb/` next to the package in a
source checkout; from a PyPI install, point it at one:

```bash
export LIGHTROOM_BRIDGE_KB="$HOME/lightroom-ai-bridge/kb"
```

> The MCP server and the CLI share one daemon, so you can use both without them
> fighting over the plug-in (which accepts a single client).

### Plain shell

Nothing to connect. `./lrc <command>`, see
[COMMAND-REFERENCE.md](COMMAND-REFERENCE.md).

---

## 6. Your first edit

1. In Lightroom, open a photo in **Develop** and leave it selected. One photo.
2. In Claude Code, say:

   > Edit this shot. Open up the shadows, keep the sky from blowing out, and
   > show me before and after.

What you will see Claude do, in order:

1. `ping`, to confirm Lightroom is there and see which photo is active.
2. `render` a before JPEG and actually look at it.
3. `settings`, to read where the sliders currently sit.
4. A few `set` / `wb` / `mask` calls.
5. `render` again, look, compare, adjust.
6. Report what it changed and why.

In Lightroom, watch the **History** panel on the left. Every step appears as
"Claude: ...". Click any earlier step to jump back. Nothing here is destructive
and nothing is written to your RAW file.

Then try the full treatment on a single frame:

```
/lightroom-bridge-pro
```

That skill runs the whole professional sequence without asking you anything: a
written brief on what the photo needs, geometry, global tone, color, cleanup,
local light-shaping with masks, the grade, detail, and a cold QC pass at the
end. It takes a few minutes and it explains itself as it goes. Add a sentence if
you want to steer it: `/lightroom-bridge-pro keep it moody, do not lift the
shadows`.

---

## 7. How to talk to it

**Say what the photo needs, not which slider to move.** "The eye goes to the
bright building on the left instead of the subject" gets you a better edit than
"set Highlights to -40", because the first tells it the problem and it can
verify the fix in the render.

**Steer with intent words.** Moody, clean, editorial, warm, filmic, punchy,
natural. It maps those onto real decisions and tells you which it made.

**Correct it plainly.** "Too warm", "I liked the crop before", "I want crushed
blacks here". Corrections are the single most valuable thing you can give it:
they are what the knowledge base learns from, so the next session starts closer
to you. Say what is wrong, you do not need to be polite or precise about it.

**Tell it the destination if you have one.** Print, Instagram, a client
gallery. Some destinations have hard constraints (a platform that flags Adobe's
generative-AI metadata, an export ceiling, a fixed ratio) and it will respect
them if it knows.

**A few things worth asking for by name:**

| Ask | What happens |
|---|---|
| "before and after" | Two renders you can compare side by side. |
| "snapshot first" | A restore point in Lightroom you can return to at any time. |
| "measure the tilt" | A numeric roll and keystone reading instead of an opinion. |
| "does the subject win?" | The attention-hierarchy check, per region, with numbers. |
| "save this as a preset" | The look becomes a real Develop preset in Lightroom. |
| "sync this to the rest" | The recipe is applied across the set, verified per photo. |

---

## 8. Everyday workflows

### Edit one photo

Select it, say what you want. Use `/lightroom-bridge-pro` when you want the
full treatment with QC; plain conversation when you want one specific thing
changed.

### Batch edit a set

Select the set in Lightroom, then: "edit the first one properly, then apply the
same recipe to the rest".

The reliable path it should take is: finish one photo, extract the recipe, then
apply it photo by photo with a read-back per photo (`bridge/batch_apply.py`),
because a selected-batch apply can silently drop keys on some frames. If the
light changes across the set, ask it to group by light situation first and give
each group its own delta. Thirty frames from one golden-hour walk is one recipe
plus small per-frame nudges, not thirty edits.

### Presets

"Save this look as a preset" writes a real XMP into Lightroom's preset folder,
in a group called "Claude". It appears in the Develop Presets panel after the
next Lightroom restart, and from then on both you and Claude can apply it by
name. Crop, masks and retouching are deliberately excluded; a preset is a look,
not a composition.

### Culling a shoot

Two different jobs, and mixing them up is the classic mistake.

**The technical cull** (out of focus, blinks, duplicate frames from a burst) is
best done by Lightroom's own **Assisted Culling** (15.4+, free, works at full
resolution). Blur discrimination is the weakest thing vision models do, at any
resolution. Do this first.

**The hero cull** (which of these good frames is the one worth publishing) is
where Claude is strong, because ranking is a comparison, and comparison is what
models do well.

For a small folder:

1. In Lightroom, open the folder and press **Cmd+A** to select all. The bridge
   only sees photos you have targeted.
2. Ask: "review this folder and flag the best shots".
3. It renders everything by UUID (read-only, your selection is not disturbed),
   builds labeled contact sheets, reads them, and flags its picks. You get the
   reasoning and the near-duplicate clusters called out.

For hundreds or thousands of frames, the durable funnel is `bridge/cull.py`:

```bash
python3 bridge/cull.py triage                   # render all + contact sheets + manifest
python3 bridge/cull.py hires --from-csv ratings.csv --min 4   # full-res pass on keepers
python3 bridge/cull.py writeback ratings.csv    # verified writeback (--dry-run first)
```

The two stages exist for a reason worth knowing: at roughly 300px per tile you
cannot confirm sharpness, and dark or blue-hour frames read as "flat" and get
unfairly down-rated. So stage one is a cheap triage that throws out the
obvious rejects and collapses bursts, and stage two re-judges only the survivors
at ~2560px, in small shuffled comparative sets. That second pass is the one that
recovers your night heroes.

Ratings are written with a verified per-photo call that echoes the read-back, so
a rating never lands on the wrong frame. Treat AI ratings as advisory anyway and
do a final 1:1 sharpness pass by eye on the shortlist before you publish.

### Measuring instead of arguing

```bash
python3 bridge/measure_tilt.py /tmp/render.jpg     # roll and keystone, whole frame
python3 bridge/find_plumb.py  /tmp/render.jpg      # roll from one vertical structure
python3 bridge/attention_check.py /tmp/render.jpg  # does the subject win its frame?
```

`find_plumb.py` exists because whole-frame tilt estimation averages every edge
in the picture, so heavy foliage or two parallel water lines can report the
frame as level while the one tower in it visibly leans. When your eye and the
number disagree, measure the thing your eye is looking at.

---

## 9. The safety net

- **Everything is non-destructive.** Develop settings are catalog metadata. Your
  RAW file is never modified.
- **History.** Every bridge action appears in Lightroom's History panel labeled
  "Claude: ...". Click any step to return to it.
- **Snapshots are the real rollback.** Before a multi-step edit, ask for a
  snapshot (the pro skill takes one automatically). `snapshot-apply` restores
  sliders and the mask stack exactly. Chained `undo` can over-rewind past the
  whole edit, and redo does not always bring it back, so prefer snapshots.
- **Nothing is exported unless you ask.** The agent recommends export settings;
  it does not export.
- **Ratings, flags and labels are only touched when the job is about them.**
- **Nothing leaves your machine.** The bridge is localhost only. What your AI
  client sends to its own servers is between you and that client, and that
  includes the preview JPEGs it looks at.

---

## 10. The knowledge base: how it gets smarter

`kb/` is a folder of Markdown cards holding photographic judgment: a culling
rubric, genre knowledge, editing recipes, named looks, and a taste profile. The
agent reads the matching cards before it works (RECALL), applies them (APPLY),
and writes down what it learned afterwards (LEARN). Lessons pile up, get
consolidated into the cards they belong to, and cards get promoted from `seed`
to `tested` to `proven` as evidence accumulates.

**This repo ships it empty on purpose.** Taste is personal, and an inherited KB
would argue with you on every photo. Yours starts on your first correction.

Three ways to fill it, and they combine:

1. **Just work.** Correct the agent when it is wrong. Each correction becomes a
   lesson card. After a handful of sessions, say "consolidate the knowledge
   base" and your first real cards get written from your own history.
2. **Ask for a seeding pass.** One prompt:

   > Seed my photography knowledge base. Research the craft (culling criteria,
   > composition, color and tone, the standard edit order, and the genres I
   > shoot: travel, street, landscape), then write the `core/`, `genres/` and
   > `editing/` cards per `kb/README.md`, all at `confidence: seed`.

   Those cards are hypotheses. Practice promotes them or kills them.
3. **Feed it your references.** Drop newsletters, articles, award-winner
   collections, before/after pairs, and especially **your own exported presets**
   into `knowledge/`, then ask it to ingest them. A preset you made is your
   taste in numeric form, which is the most direct input the KB can get.

Useful commands:

```bash
python3 bridge/kb.py stats      # how many cards, what is due for consolidation
python3 bridge/kb.py index      # rebuild kb/INDEX.md
python3 bridge/kb.py validate   # lint every card's frontmatter
```

The full contract, including the card format and the consolidation and revision
rules, is [`kb/README.md`](../kb/README.md). It is worth reading once: it is the
part of this repo that decides whether session 50 is better than session 1.

> Your `kb/` is tracked by git so your taste is versioned along with the tool.
> If you fork this repo to a **public** remote, uncomment the three lines at the
> bottom of `.gitignore` first so your cards stay yours.

---

## Troubleshooting

**`Lightroom not reachable...`**

1. Is Lightroom Classic actually open? (Classic. Not Lightroom CC.)
2. **File > Plug-in Extras > Claude Bridge: Show Status**. If "Run loop active"
   is *no*, pick **Claude Bridge: Start / Restart** in the same menu.
3. Confirm it is enabled in **File > Plug-in Manager**.
4. Then `./lrc daemon stop && ./lrc ping`.

This is a UI step. A shell command cannot start the plug-in for you.

**`No active photo...`** Select a photo in Lightroom. Most edits act on the
active (most-selected) photo.

**The first edit did nothing.** Known and expected: the very first edit call
issued while the photo is sitting in the **Library** module gets eaten by the
switch to Develop. It reports `applied` and reads back unchanged. Re-issue it.
More generally, `ok` from the bridge is not proof of a commit, which is why the
agent reads values back and verifies by render.

**A mask does not seem to do anything.** AI masks under-apply on creation,
because the selection is still computing when the values arrive. The fix is to
re-push with `mask adjust` and check the render, possibly more than once. Also:
`settings` always reports mask values as 0, so a read-back cannot confirm a
mask. Only the render can.

**A preview looks stale.** Use `render`, not `thumb`. `render` is a full export
through the pipeline and always reflects the current state, including crop and
masks. `thumb` reads the preview cache, which Lightroom often refuses for the
active photo in Develop; the CLI falls back to `render` automatically.

**The bridge wedges after Lightroom restarts or the plug-in reloads.** The
plug-in serves one client at a time. Restart it from **Plug-in Extras > Claude
Bridge: Start / Restart**, then `./lrc daemon stop && ./lrc ping`.

**Contact sheets fail.** `pip3 install --user Pillow`.

**Where the logs are.** `~/.claude-lrc-bridge/daemon.log` for the Python side,
and the in-Lightroom **Claude Bridge: Show Status** dialog for the plug-in side.

---

## 11. FAQ

**Does this modify my RAW files?** No. Develop settings live in the catalog.
Your originals are untouched, which is also why every edit is reversible.

**Does it upload my photos anywhere?** The bridge does not. It is localhost
only. Your AI client does send the preview renders it looks at to its own
service, exactly like pasting an image into a chat.

**Can it work on several photos at once?** Yes for batch apply, ratings and
culling. Single-photo `set` acts on the active photo only, by design: edits that
matter get verified per photo.

**Can it place a radial mask on my subject's face?** No. Lightroom's SDK gives
no way to position a geometric mask, so radial and gradient masks land at the
default spot. AI masks (`subject`, `sky`, `background`) do not have this problem
because Lightroom finds the region itself. For a fixed placement, build it once
in the UI, save it as a preset, and have Claude apply the preset.

**Does it use generative AI on my photos?** Not unless you ask. Lightroom's
Generative Remove, AI Denoise and Generative Fill are available through the
bridge but are off the default path, partly because Adobe writes AI metadata
into the result and some platforms label images that carry it. Select-sky and
select-subject masks are *analytic*, not generative, and carry no such metadata.

**Which Claude model should I use?** For aesthetic judgment, the strongest model
you have access to. The gap between models is large for culling and grading and
small for mechanical work, so a cheap model for triage plus a strong one for the
frames you will publish is the efficient split.

**Can I use it with something other than Claude?** Yes. The CLI is a plain
JSON-over-stdout tool, the MCP server works with any MCP client, and
`AGENTS.md` is the same operating cheat-sheet written for non-Claude agents.

**Windows?** Untested. The transport uses a Unix domain socket and macOS
`~/Library` paths. The plug-in's Lua side is portable; the Python side would
need work.

---

## 12. Uninstall

1. Lightroom: **File > Plug-in Manager**, select **Claude Bridge**, click
   **Remove**. (Or delete it from
   `~/Library/Application Support/Adobe/Lightroom/Modules/` if you used
   `install-plugin`.)
2. `./lrc daemon stop`
3. `rm -rf ~/.claude-lrc-bridge`
4. `rm ~/.claude/skills/lightroom-bridge ~/.claude/skills/lightroom-bridge-pro`
5. `pip uninstall lightroom-bridge` if you installed it.

Your photos and catalog are unaffected. Edits Claude made stay in your History
and can be reverted in Lightroom at any time. Keep the `kb/` folder even if you
remove everything else: it is the only part that cannot be downloaded again.

---

## 13. Get help

- Bugs and feature requests: open an
  [issue on GitHub](https://github.com/aslanoov/lightroom-ai-bridge/issues).
  Include the output of `./lrc ping --raw`, your Lightroom Classic version, and
  the last lines of `~/.claude-lrc-bridge/daemon.log`.
- Anything else: support@speranda.com
