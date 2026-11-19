# Changelog

All notable changes to Lightroom AI Bridge are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/). How a release is cut:
[docs/RELEASING.md](docs/RELEASING.md).

## [Unreleased]

## [2.1.0] - 2026-11-19

New editing controls, a geometry workflow and verified mask creation. The
plug-in is now 1.5.0. **Upgrade both halves together** as for 2.0.0: reload the
plug-in (Plug-in Manager, then Reload Plug-in, or restart Lightroom; it shows
plug-in version 1.5.0), run `./lrc daemon stop`, and reconnect your MCP client
so it discovers the new tools. The MCP server now has 62 tools.

### Added

- **Geometry workflow.** `lrc geometry capture|plan|apply|audit|transfer|suggest`
  and six matching `geometry_*` MCP tools. Every step is bound to one photo
  UUID and one exact render: `capture` saves the full Develop state and a
  hashed render, `plan` works in display coordinates without writing,
  `apply` refuses a changed photo or state and takes a rollback snapshot and a
  receipt first, `audit` measures named horizontal and vertical references
  with an overlay and never certifies "level" on its own, `transfer` relocates
  references after a change, and `suggest` proposes a bounded correction from
  one-control probes. Walkthrough: `bridge/geometry-audit.md`. Needs the new
  optional `geometry` extra (numpy, Pillow, scikit-image).
- **Point Color.** `lrc point-color list|add|update|select|delete|clear` and the
  `point_color` MCP tool: global or per-mask (`--local`, `--mask-id`),
  including Variance and partial swatch and range updates. `tool` also selects
  `point_color`, `local_point_color` and `depth_refinement`.
- **More mask control.** `mask adjust --id` (MCP: `mask` adjust with
  `mask_id`) targets any mask; `mask duplicate-invert`, `mask toggle-hidden`,
  `mask component select|delete|toggle-hidden|invert` (MCP `mask_tool`) and
  `mask delete-empty` (MCP `delete_empty_masks`). `mask list` also reports the
  selected mask id.
- **Preset strength.** `preset apply --amount 0..200` and opt-in
  `--update-ai` (MCP `apply_preset` `amount`, `update_ai`).
- **AI features as explicit requests.** `enhance denoise|rawDetails|superRes`
  (MCP `enhance`; `call denoise` remains as an alias),
  `reflection state|enable|disable` (MCP `reflection_state`,
  `reflection_removal`), `update-ai` (MCP `update_ai_settings`) and `ai-state`
  (MCP `ai_state`). Responses report requests and panel state, never that
  background processing finished; render to verify.
- **`capabilities`** (CLI and MCP): Lightroom, plug-in and SDK versions, which
  SDK functions are available, and the features that need Lightroom's UI
  (mask Feather/Edge, Assisted Culling scores, native Duplicates, Render to
  DNG).
- **Native culling hand-off.** `review-selection` (MCP `review_selection`) reads
  the photos you selected after Assisted Culling or the Duplicates view, with
  ratings, flags, virtual-copy and folder-stack metadata, and
  `bridge/cull.py triage --native-selection` feeds that selection into the
  culling funnel. Native focus and eye scores are not available through the
  SDK and are not invented.
- `bridge/measure_tilt.py` gains `--references` (named reference lines bound to
  the render's hash) and `--json-out`. The line fitter now lives in the
  package; the script is a compatibility entry point.
- Tests for the geometry math, reference measurement, Point Color, mask
  targeting, Reflection Removal, AI state and CLI argument forwarding.

### Changed

- Plug-in **1.5.0**. Its declared minimum is now Lightroom Classic **15.3**
  (`LrSdkMinimumVersion`, previously 11.0), matching the tested API baseline
  and what the documentation already required.
- Develop edits wait until the Develop module is ready, and absolute numeric
  slider writes are read back and retried within a bounded window. Relative
  nudges and toggles are never replayed automatically.
- `bridge/find_plumb.py`: positive angles now mean clockwise (the top tips
  right), matching `geometry audit` and `measure_tilt.py`; 2.0.0 reported the
  opposite sign. It no longer prints a `VERDICT` line: its tracks are unnamed
  candidates, and level is decided by the named-reference audit.
- Preview housekeeping also prunes old `geometry-*` capture folders in
  `~/.claude-lrc-bridge/previews`, under the same age and size rules as renders.
- The `./lrc` wrapper prefers the checkout's `.venv` Python when one exists,
  so the geometry extra and the MCP dependencies are found.

### Fixed

- Mask creation is verified: `mask create` waits up to 10 seconds for
  Lightroom to list a new mask id and returns an error if none appears,
  instead of reporting success for a mask that was never created (subtypes
  that need a click in the UI, such as `rangeMask luminance` and
  `aiSelection objects`). This replaces 2.0.0's known limitation of
  `mask list` reading empty for about two seconds after an AI mask create.
- `mask list` returns an empty list when Lightroom 15.6 reports no masks (or
  nothing yet while an AI mask computes), and a failed mask read-back is now an
  error instead of a silent empty result.
- In a live Lightroom Classic 15.6 test (JPEG, subject and sky masks), AI mask
  values passed to `mask create` landed at full strength on the first call.

### Known limitations

- Large RAW files have not been re-tested for AI mask strength on create.
  Keep the render check, and re-push once with `mask adjust` if the effect is
  missing or weak.
- Reflection Removal's visual result is not verified: in a 15.6 test its first
  enabled export hit the 90 second timeout. Enhance on supported RAW files was
  not part of that live check.
- No documented SDK 15.3 endpoints exist for mask Feather/Edge, Assisted
  Culling scores, native Duplicates or Render to DNG; use Lightroom's UI.

## [2.0.0] - 2026-10-30

A security and reliability release. **Upgrade both halves together:** reload
the plug-in (Plug-in Manager, then Reload Plug-in, or restart Lightroom; it now
shows plug-in version 1.3.0) and run `./lrc daemon stop` so the next command
starts the new daemon. The wire protocol changed, so a 1.x plug-in and a 2.0
CLI refuse each other with a message that says exactly this.

### Security

- Commands sent to the plug-in can no longer run code. Release 1.0.0 parsed
  each command with `loadstring`; Lightroom's plug-in environment has no
  `setfenv`, so the documented "empty sandbox" did not exist and any local
  process that reached port 49463 could make Lightroom evaluate an expression.
  Commands are now JSON, read by a strict data-only decoder.
- Every command must carry a secret the plug-in generates each time it starts
  (256 bits from `/dev/urandom`, stored in `~/.claude-lrc-bridge/auth.token`,
  folder kept owner-only). Other local users, and software that cannot read
  your files, such as a web page posting to `localhost:49463`, are refused
  before anything runs.

### Fixed

- The bridge no longer goes silent after its first client disconnects.
  Restarting the daemon, a crashed client or a second tool used to leave the
  plug-in's response port unable to accept anyone else, so every later command
  timed out until the plug-in was restarted. The plug-in now rebinds that port
  when a client leaves, and drops commands whose replies could not be
  delivered instead of running them unanswered. `./lrc daemon stop` is safe.
- A busy or stuck plug-in now fails in about 5 to 12 seconds instead of 45 to
  120: the daemon pings before trusting a fresh, idle or timed-out link,
  reconnects once after giving the plug-in time to rebind, and never resends a
  command that timed out.
- With Lightroom closed, commands fail immediately instead of after 8 seconds,
  while a plug-in reload in progress is still waited out.

- `ping` reports the plug-in's real version. 1.0.0 shipped plug-in 1.2.0 but
  reported 1.1.0 from a hard-coded copy; the version now comes from the
  plug-in manifest, the same number Plug-in Manager shows.

### Changed

- Lead the README, package and MCP server descriptions with Lightroom MCP and
  Model Context Protocol so photographers can find the Lightroom Classic
  integration. The project, package, plug-in and registry names stay the same.
- Put the stdio MCP setup first in the README and user guide, show how the MCP
  server connects to the Lightroom plug-in, and clarify macOS and Lightroom
  Classic support. The 46 MCP tools include the seven knowledge-base tools.
- Include Lightroom Classic MCP in release titles and introductions. These
  entries are documentation and discoverability changes only.

### Added

- Errors carry a `diagnosis`: which process holds ports 49463/49464 (for
  example another Lightroom controller), whether the plug-in is closed, too old,
  busy or stuck, and the fix. `./lrc daemon status` reports it together with
  the daemon version, protocol and link health.
- Housekeeping: the daemon prunes `~/.claude-lrc-bridge/previews` (renders older
  than 24 hours, and the oldest beyond 1 GB), and both logs rotate at 5 MB. The
  plug-in logs a short summary per command instead of the whole command line.
- Tests in `tests/` for the decoder, the token check, liveness probes,
  reconnects, diagnosis and housekeeping, runnable without Lightroom
  (`uv run --extra test python -m unittest discover -s tests`).

### Known limitations

- Pre-existing behavior on Lightroom Classic 15.6, unchanged by this release:
  right after an AI mask create, `mask list` can read empty for about two
  seconds while Lightroom computes the selection, and on RAW files the values
  passed to `mask create` may land only after one `mask adjust` re-push. Check
  `mask list` again after a short wait and verify the mask by render.
- Not yet published to PyPI; install from source (see the user guide).

## [1.0.0] - 2026-09-26

First public release.

### Added

- **Lightroom plug-in** (shown in Lightroom as "Claude Bridge", internal plug-in
  version 1.2.0): exposes Lightroom Classic's Develop module over two localhost
  sockets, built only on Adobe's public SDK. Inbound commands are parsed as data
  in an empty sandbox, never executed as code.
- **`lrc` command line tool** with 40+ commands: tone, white balance, crop and
  straighten, AI and geometric masks, presets (list, apply, save as a real
  Develop preset), snapshots, undo and redo, ratings, flags and labels with
  verified per-photo writeback, AI Denoise and Remove People on supported
  formats, and full-fidelity `render` previews for before/after comparison.
  Pure Python 3 standard library.
- **MCP server** (`lightroom-bridge`) exposing the same operations as 46 tools
  for any MCP client, with previews returned as inline images and seven `kb_*`
  tools for the knowledge base. Shares one background daemon with the CLI.
- **Claude Code skills**: `lightroom-bridge` for everyday editing and culling,
  and `lightroom-bridge-pro` for a full professional edit of the selected photo
  with no questions asked. Installer: `skills/install.sh`.
- **Knowledge base scaffold** (`kb/`): the recall, apply, learn loop with its
  card format, consolidation and revision rules, and `bridge/kb.py` tooling.
  Ships empty by design, so each photographer's taste profile is built from
  their own corrections.
- **Helper scripts** in `bridge/`: contact-sheet folder review, the two-stage
  culling funnel with verified writeback, fast bulk metadata writeback,
  verified per-photo batch apply, full-JSON settings apply, tilt and plumb
  measurement, and an attention-hierarchy check.
- **Documentation**: a step-by-step user guide, a full command reference, and
  agent cheat-sheets (`CLAUDE.md`, `AGENTS.md`).

### Known limitations

- macOS only. Requires Lightroom Classic 15.3 or later.
- Geometric masks (radial, linear) cannot be positioned through Lightroom's SDK;
  they land at Lightroom's default spot. AI masks are unaffected.
- Mask values cannot be read back through the SDK; masks are verified by render.
- Not yet published to PyPI; install from source (see the user guide).

[Unreleased]: https://github.com/aslanoov/lightroom-ai-bridge/compare/v2.1.0...HEAD
[2.1.0]: https://github.com/aslanoov/lightroom-ai-bridge/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/aslanoov/lightroom-ai-bridge/compare/v1.0.0...v2.0.0
[1.0.0]: https://github.com/aslanoov/lightroom-ai-bridge/releases/tag/v1.0.0
