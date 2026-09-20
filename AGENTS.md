# Operating the Lightroom bridge (for any agent)

**The full operating cheat-sheet is [`CLAUDE.md`](CLAUDE.md).** It is written for
Claude Code, but everything in it (the commands, the races, the mask caveats,
the culling funnel, the knowledge-base loop) applies to any agent driving this
bridge. Read it. This file only covers what differs when you are not Claude Code.

## The short version

```bash
./lrc ping                                       # Lightroom open + plug-in running + photo selected
./lrc render --out /tmp/before.jpg --size 1500   # LOOK at this image before editing
./lrc settings                                   # current Develop values
./lrc set Exposure=0.35 Shadows=20 Highlights=-15
./lrc render --out /tmp/after.jpg --size 1500    # LOOK again, compare
```

Add `--raw` for compact one-line JSON, which is what you want when parsing.
Every command: `docs/COMMAND-REFERENCE.md`. Run `./lrc help` to ask the plug-in
itself what it supports.

## The rules that matter most

1. **Never edit blind.** The whole design assumes you render a JPEG, look at it,
   edit, render again, and compare. If your harness cannot show you an image,
   say so plainly rather than editing on faith, and lean on the numeric helpers
   (`bridge/measure_tilt.py`, `bridge/attention_check.py`, `./lrc settings`).
2. **`ok` is not commit confirmation.** Read back with `value <Param>` after a
   short settle, and verify visually by render. Three confirmed races are listed
   in `CLAUDE.md`.
3. **Verify masks by rendering.** `settings` reports mask values as 0 always.
4. **Snapshot before multi-step edits.** `snapshot-apply` is the safe rollback;
   chained `undo` can over-rewind past the whole edit.
5. **Use the knowledge base.** `kb/README.md` is the contract, and it works with
   plain file reads: read `kb/INDEX.md`, open the cards whose `genres` or `tags`
   match the task, and write lessons into `kb/learned/` following
   `kb/learned/TEMPLATE.md`. If you can run Python, `bridge/kb.py` does it
   deterministically (`recall`, `read`, `index`, `new`, `bump`, `absorb`,
   `validate`, `stats`). The KB ships empty; filling it is the job.

## If you speak MCP instead of shell

The package ships an MCP server (`lightroom-bridge`) exposing the same
operations as 46 tools, with previews returned as inline images and seven `kb_*`
tools for the knowledge base. Setup is in `README.md`. It shares one daemon with
the CLI, so both can be live at once. Point `LIGHTROOM_BRIDGE_KB` at a `kb/`
directory if you installed the package without a source checkout.

## Modifying the plug-in

Adobe's Lightroom Classic SDK is not in this repo (it is not redistributable).
Download it from Adobe. `API Reference/modules/LrDevelopController.html` is the
authoritative parameter list, so grep it before concluding that a parameter does
not exist, and read the Programmers Guide and Controller SDK Guide PDFs before
touching the Lua. The sandbox has no `setfenv`, and you cannot yield across a
plain `pcall`; the full list of environment gotchas is at the bottom of
`docs/COMMAND-REFERENCE.md`.
