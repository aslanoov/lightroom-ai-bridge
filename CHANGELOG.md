# Changelog

All notable changes to Lightroom AI Bridge are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/). How a release is cut:
[docs/RELEASING.md](docs/RELEASING.md).

## [Unreleased]

## [2.0.0] - 2026-10-16

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

[Unreleased]: https://github.com/aslanoov/lightroom-ai-bridge/compare/v2.0.0...HEAD
[2.0.0]: https://github.com/aslanoov/lightroom-ai-bridge/compare/v1.0.0...v2.0.0
[1.0.0]: https://github.com/aslanoov/lightroom-ai-bridge/releases/tag/v1.0.0
