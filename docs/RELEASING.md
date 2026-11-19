# Releasing Lightroom AI Bridge - Lightroom Classic MCP server

How a version goes from `main` to a published release. Maintainers only; users
want the [user guide](USER-GUIDE.md).

**Where the steps run.** This repository is published from the maintainer's
development tree. The changelog and version edits (steps 1 and 2) are made
there and arrive here as one export; this repository is never edited by hand.
Everything from step 3 on runs here.

## Versioning

**One product version** carries everything: the git tag `vX.Y.Z`, the Python
package (`src/lightroom_bridge/__init__.py`), `server.json`, and the top entry
in [CHANGELOG.md](../CHANGELOG.md) always agree. `scripts/release_check.py`
refuses to pass when they do not.

| Bump | When |
|---|---|
| MAJOR (`2.0.0`) | Something users rely on breaks: a CLI command or flag is removed or changes meaning, an MCP tool is renamed, the plug-in protocol changes so an old plug-in cannot talk to the new CLI, the KB card format changes incompatibly. |
| MINOR (`1.1.0`) | New capability, backwards compatible: new commands, MCP tools, skills, helper scripts, KB tooling. |
| PATCH (`1.0.1`) | Fixes and documentation only. |

**The Lightroom plug-in has its own internal version** (`VERSION` in
`ClaudeBridge.lrplugin/Info.lua`, shown in Lightroom's Plug-in Manager). Bump it
whenever any `.lua` file changes, independently of the product version, and say
in the release notes that the plug-in changed: users must reload it (Plug-in
Manager, then Reload Plug-in, or restart Lightroom).

## Cutting a release

Replace `X.Y.Z` throughout.

**1. Changelog.** In `CHANGELOG.md`, rename `## [Unreleased]` to
`## [X.Y.Z] - YYYY-MM-DD`, add a fresh empty `## [Unreleased]` above it, and
update the two compare links at the bottom. Group entries under Added, Changed,
Fixed, Removed. Write each entry as what the user gets, not what the code did.

**2. Bump versions.**

- `src/lightroom_bridge/__init__.py`: `__version__ = "X.Y.Z"`
- `server.json`: both `"version"` fields
- `ClaudeBridge.lrplugin/Info.lua`: `VERSION`, only if Lua changed

**3. Commit.**

```bash
git add -A
git commit -m "Release X.Y.Z"
```

**4. Run the gate.** It must end with `READY TO TAG`.

```bash
python3 scripts/release_check.py --tag vX.Y.Z
```

**5. Smoke test against a real Lightroom.** The gate cannot reach Lightroom, so
spend five minutes on the real thing, with a throwaway photo selected:

```bash
./lrc ping                                   # plug-in reachable, right photo active
./lrc snapshot "release smoke"
./lrc render --out /tmp/rs-before.jpg --size 800
./lrc set Exposure=0.5 && sleep 3 && ./lrc value Exposure    # reads back 0.5
./lrc mask create aiSelection sky local_Exposure=-1 && ./lrc mask list
./lrc render --out /tmp/rs-after.jpg --size 800             # visibly different
./lrc snapshot-apply "release smoke"         # back to clean
./lrc rating 3 --uuid <uuid> && ./lrc rating 0 --uuid <uuid>
python3 bridge/kb.py stats
```

If the plug-in changed, reload it first and confirm the new version in Plug-in
Manager.

**6. Tag and push.**

```bash
git tag -a vX.Y.Z -m "Lightroom AI Bridge X.Y.Z - Lightroom Classic MCP server"
git push origin main
git push origin vX.Y.Z
```

**7. GitHub release.** Lead with Lightroom MCP so people searching for an AI
integration can recognize what the release contains. Keep Lightroom AI Bridge
as the product name. Start with this introduction, then append the changelog
section, including any upgrade requirements and known limitations:

```bash
cat > /tmp/notes.md <<'EOF'
Lightroom AI Bridge is an open-source Lightroom MCP server (Model Context
Protocol) and CLI for Adobe Lightroom Classic on macOS. Connect Claude Desktop,
Claude Code or another stdio MCP client to edit Develop settings, use masks and
presets, manage ratings and compare rendered previews. Requires Lightroom
Classic 15.3+ and the included plug-in, shown in Lightroom as Claude Bridge.

MCP setup and installation: https://github.com/aslanoov/lightroom-ai-bridge/blob/vX.Y.Z/README.md#lightroom-mcp-setup

EOF
awk '/^## \[X.Y.Z\]/{f=1;next} /^## \[/{f=0} f' CHANGELOG.md >> /tmp/notes.md
gh release create vX.Y.Z --title "Lightroom AI Bridge X.Y.Z - Lightroom Classic MCP server" --notes-file /tmp/notes.md
```

The changelog stays the source of truth for what changed. Update the introduction
if capabilities or requirements change. Do not imply PyPI or MCP Registry
availability until those publications are verified.

Keep the GitHub About description aligned with the README and package metadata:

> Lightroom MCP server (Model Context Protocol) for Adobe Lightroom Classic on
> macOS. Edit photos, use masks and presets, manage ratings and compare previews
> with Claude or other MCP clients. Includes a CLI and Lightroom plug-in.

Include the relevant discovery topics `lightroom-mcp`, `lightroom-classic`,
`lightroom-plugin`, `mcp`, `mcp-server`, `model-context-protocol` and `photo-editing`.
The repository slug, package name `lightroom-bridge`, registry identifier and
plug-in name are stable identities; a positioning update does not rename them.

**8. PyPI** (from the first PyPI release on). Needs a PyPI API token for the
`lightroom-bridge` project.

```bash
rm -rf dist && uv build                      # wheel bundles the plug-in
uvx twine check dist/*
uv publish                                   # reads UV_PUBLISH_TOKEN
uvx --from lightroom-bridge==X.Y.Z lrc --help   # installs and starts from PyPI
```

On the very first PyPI release, also switch the README and user guide from the
`uv run --directory` form to plain `uvx lightroom-bridge`, and remove the "not
yet on PyPI" lines.

**9. MCP Registry** (optional, after PyPI exists). `server.json` already carries
the name `io.github.aslanoov/lightroom-ai-bridge`. Follow the registry's
publishing guide: it verifies PyPI ownership through an `mcp-name:` line in the
package README, and authenticates the `io.github.aslanoov` namespace with
GitHub.

**10. Product page and announcement.** Add the release to the product page with
the GitHub source pointing at
`https://github.com/aslanoov/lightroom-ai-bridge/releases/tag/vX.Y.Z`, refresh
any page copy the release changes, then post the announcements.

## Hotfixes

Same steps with a PATCH bump, straight from `main`. Keep hotfix releases small:
the fix, its changelog line, nothing opportunistic.

## When a release is bad

Never delete or move a published tag: people may already have pulled it. Ship
`X.Y.Z+1` with the fix instead, and add a line at the top of the bad release's
GitHub notes pointing to it. On PyPI, *yank* the bad version (it stays
installable for pinned users, but new installs skip it); never delete it.
