# Claude Code skills

Two skills ship with this repo. A skill is a Markdown file that teaches Claude
Code how to do a job properly: when to reach for the bridge, which command to
use, and every trap that a real session has already hit.

| Skill | Invoke | What it does |
|---|---|---|
| `lightroom-bridge` | `/lightroom-bridge`, or just ask | The everyday workflow: connect, look, edit, mask, rate, cull. Claude also triggers it on intent alone ("open up the shadows", "which of these is best"). |
| `lightroom-bridge-pro` | `/lightroom-bridge-pro` | The full professional edit of the selected photo, start to finish, no questions asked: brief, foundation, geometry, tone, color, cleanup, local light-shaping, grade, detail, inspector-grade QC. |

## Install

```bash
./skills/install.sh              # symlinks both into ~/.claude/skills
./skills/install.sh --copy       # copy instead (no link back to the repo)
./skills/install.sh --project /path/to/project   # install into one project only
```

Symlinking is the better default: `git pull` then updates your skills too.

## How they find the CLI

In this order:

1. an `lrc` console script on `PATH` (you ran `pip install -e .` or `uvx`),
2. the `LIGHTROOM_BRIDGE_HOME` environment variable,
3. `~/lightroom-ai-bridge`.

If your checkout lives somewhere else and you did not install the package, add
this to `~/.zshrc`:

```bash
export LIGHTROOM_BRIDGE_HOME="/path/to/your/lightroom-ai-bridge"
```

## Editing them

These files are meant to be edited. They are your operating manual for the
agent: if Claude keeps making the same mistake, the fix usually belongs in the
skill (a rule that applies every session) or in `kb/` (photographic judgment
that grows with your work). Keep tool mechanics in the skill and taste in the KB.
