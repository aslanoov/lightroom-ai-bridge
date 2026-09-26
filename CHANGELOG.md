# Changelog

All notable changes to Lightroom AI Bridge are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/). How a release is cut:
[docs/RELEASING.md](docs/RELEASING.md).

## [Unreleased]

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

[Unreleased]: https://github.com/aslanoov/lightroom-ai-bridge/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/aslanoov/lightroom-ai-bridge/releases/tag/v1.0.0
