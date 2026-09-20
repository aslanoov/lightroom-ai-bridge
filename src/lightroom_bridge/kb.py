#!/usr/bin/env python3
"""lightroom_bridge.kb -- the photography knowledge base in ``kb/``.

Plain-markdown cards with YAML-subset frontmatter (contract: ``kb/README.md``).
This module is the single source of truth for KB logic; it backs both the
``kb.py`` CLI (``bridge/kb.py``) and the MCP server's ``kb_*`` tools.

Stdlib-only and Python 3.9-safe, so the CLI keeps working with no dependencies.

Locating the KB: ``LIGHTROOM_BRIDGE_KB`` env var wins; otherwise a ``kb/``
directory is searched for next to the package's repo root, then the CWD. The KB
ships with the source checkout, not the wheel, so an installed copy may have
none -- callers get a clear error rather than silently empty results.
"""

from __future__ import annotations

import datetime
import os
import re
from pathlib import Path

TYPES = {"rubric", "technique", "recipe", "style", "lesson", "reference"}
SCOPES = {"general", "lightroom"}
CONFIDENCES = {"seed", "tested", "proven"}
REQUIRED = ["id", "title", "type", "scope", "genres", "tags", "source",
            "confidence", "evidence", "created", "updated"]
# Cards in these folders only load for an edit task (see recall()).
EDIT_DIRS = {"editing", "styles", "lightroom"}
REWRITE_AFTER = 4      # absorbed lessons before a card earns a deep rewrite
STALE_SEED_DAYS = 180  # seed cards untested this long need re-verification

SKIP_NAMES = {"README.md", "INDEX.md", "TEMPLATE.md"}


class KBNotFound(RuntimeError):
    """Raised when no kb/ directory can be located."""


def kb_root(explicit: "str | os.PathLike | None" = None) -> Path:
    """Resolve the kb/ directory. Raises KBNotFound with actionable guidance."""
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    env = os.environ.get("LIGHTROOM_BRIDGE_KB")
    if env:
        candidates.append(Path(env))
    # src/lightroom_bridge/kb.py -> repo root is two parents up.
    candidates.append(Path(__file__).resolve().parent.parent.parent / "kb")
    candidates.append(Path.cwd() / "kb")
    for c in candidates:
        if c.is_dir():
            return c
    raise KBNotFound(
        "No kb/ directory found. Set LIGHTROOM_BRIDGE_KB to the knowledge-base "
        "path, or run from a source checkout that contains kb/."
    )


def parse_frontmatter(path: Path):
    """Parse one card's frontmatter. Returns (meta_dict, error_or_None)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        return None, str(e)
    if not text.startswith("---"):
        return None, "no frontmatter"
    end = text.find("\n---", 3)
    if end == -1:
        return None, "unterminated frontmatter"
    meta = {}
    for line in text[3:end].strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if val.startswith("[") and val.endswith("]"):
            items = [v.strip().strip("'\"") for v in val[1:-1].split(",")]
            meta[key] = [v for v in items if v]
        else:
            meta[key] = val.strip("'\"")
    return meta, None


def all_cards(root: "Path | None" = None):
    """Yield (path, meta, error) for every card (templates/indexes skipped)."""
    root = root or kb_root()
    for path in sorted(root.rglob("*.md")):
        if path.name in SKIP_NAMES:
            continue
        meta, err = parse_frontmatter(path)
        yield path, meta, err


def recall(genres="all", task="edit", root: "Path | None" = None):
    """Return the cards an agent should load for this task.

    core/ always loads; genres/ loads on a genre match; editing|styles|lightroom
    load only for an edit task; unmerged lessons always load.
    Returns a list of dicts: {path, id, title, type, confidence, body}.
    """
    root = root or kb_root()
    want = {g.strip().lower() for g in str(genres or "all").split(",") if g.strip()}
    hits = []
    for path, meta, err in all_cards(root):
        if err or not meta:
            continue
        top = path.relative_to(root).parts[0]
        card_genres = {g.lower() for g in meta.get("genres", [])}
        genre_match = "all" in card_genres or bool(card_genres & want) or "all" in want
        if top == "core":
            keep = True
        elif top == "genres":
            keep = genre_match
        elif top in EDIT_DIRS:
            keep = task == "edit" and genre_match
        elif top == "learned":
            keep = str(meta.get("merged", "false")).lower() == "false"
        else:
            keep = genre_match
        if keep:
            hits.append({
                "path": str(path.relative_to(root)),
                "id": meta.get("id", ""),
                "title": meta.get("title", ""),
                "type": meta.get("type", ""),
                "confidence": meta.get("confidence", ""),
                "body": path.read_text(encoding="utf-8"),
            })
    return hits


def read_card(card_id: str, root: "Path | None" = None):
    """Return one card by its id, or None."""
    root = root or kb_root()
    for path, meta, err in all_cards(root):
        if err or not meta or meta.get("id") != card_id:
            continue
        return {
            "path": str(path.relative_to(root)),
            "id": card_id,
            "title": meta.get("title", ""),
            "body": path.read_text(encoding="utf-8"),
        }
    return None


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40]


def new_lesson(title, situation="", what_happened="", rule="", merge_target="",
               genres="all", tags="uncategorized", root: "Path | None" = None):
    """Write a lesson card into kb/learned/. Returns its path (relative)."""
    root = root or kb_root()
    today = datetime.date.today().isoformat()
    slug = _slug(title)
    path = root / "learned" / ("%s-%s.md" % (today, slug))
    if path.exists():
        raise FileExistsError("lesson already exists: %s" % path)
    body = (
        "---\n"
        "id: lesson-%s-%s\n" % (today, slug) +
        'title: "%s"\n' % title.replace('"', "'") +
        "type: lesson\nscope: general\n"
        "genres: [%s]\n" % genres +
        "tags: [%s]\n" % tags +
        "source: session\nconfidence: seed\nevidence: 1\n"
        "lightroom_min: n/a\n"
        "created: %s\nupdated: %s\nmerged: false\n" % (today, today) +
        "---\n\n# %s\n\n" % title +
        "## Situation\n%s\n\n## What happened\n%s\n\n"
        "## Rule to carry forward\n%s\n\n## Merge target\n%s\n"
        % (situation, what_happened, rule, merge_target)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return str(path.relative_to(root))


def _rewrite_field(text, key, value, insert_after=None):
    if re.search(r"(?m)^%s: " % key, text):
        return re.sub(r"(?m)^%s: .*$" % key, "%s: %s" % (key, value), text, count=1)
    if insert_after:
        return re.sub(r"(?m)^(%s: .*)$" % insert_after,
                      r"\1" + "\n%s: %s" % (key, value), text, count=1)
    return text


def bump(card_id, to=None, root: "Path | None" = None):
    """Increment a card's evidence (optionally promote confidence)."""
    root = root or kb_root()
    for path, meta, err in all_cards(root):
        if err or not meta or meta.get("id") != card_id:
            continue
        text = path.read_text(encoding="utf-8")
        ev = int(meta.get("evidence", 0) or 0) + 1
        today = datetime.date.today().isoformat()
        text = _rewrite_field(text, "evidence", ev)
        text = _rewrite_field(text, "updated", today)
        if to:
            if to not in CONFIDENCES:
                raise ValueError("confidence must be one of %s" % sorted(CONFIDENCES))
            text = _rewrite_field(text, "confidence", to)
        path.write_text(text, encoding="utf-8")
        return {"id": card_id, "evidence": ev,
                "confidence": to or meta.get("confidence")}
    raise KeyError("no card with id %r" % card_id)


def absorb(card_id, reset=False, root: "Path | None" = None):
    """Count a lesson merged into a card; reset=True after a deep rewrite."""
    root = root or kb_root()
    for path, meta, err in all_cards(root):
        if err or not meta or meta.get("id") != card_id:
            continue
        text = path.read_text(encoding="utf-8")
        n = 0 if reset else int(meta.get("absorbed", 0) or 0) + 1
        today = datetime.date.today().isoformat()
        text = _rewrite_field(text, "absorbed", n, insert_after="evidence")
        text = _rewrite_field(text, "updated", today)
        path.write_text(text, encoding="utf-8")
        return {"id": card_id, "absorbed": n,
                "rewrite_due": (not reset) and n >= REWRITE_AFTER}
    raise KeyError("no card with id %r" % card_id)


def validate(root: "Path | None" = None):
    """Check every card's frontmatter. Returns (problems, card_count)."""
    root = root or kb_root()
    problems, ids = [], {}
    for path, meta, err in all_cards(root):
        name = str(path.relative_to(root))
        if err or not meta:
            problems.append("%s: %s" % (name, err or "unparseable"))
            continue
        for key in REQUIRED:
            if key not in meta:
                problems.append("%s: missing %r" % (name, key))
        if meta.get("type") not in TYPES:
            problems.append("%s: bad type %r" % (name, meta.get("type")))
        if meta.get("scope") not in SCOPES:
            problems.append("%s: bad scope %r" % (name, meta.get("scope")))
        if meta.get("confidence") not in CONFIDENCES:
            problems.append("%s: bad confidence %r" % (name, meta.get("confidence")))
        if not meta.get("genres"):
            problems.append("%s: empty genres" % name)
        cid = meta.get("id")
        if cid in ids:
            problems.append("%s: duplicate id %r (also %s)" % (name, cid, ids[cid]))
        ids[cid] = name
        if meta.get("scope") == "lightroom" and meta.get("lightroom_min") in (None, "n/a"):
            problems.append("%s: scope lightroom needs lightroom_min" % name)
    return problems, len(ids)


def stats(root: "Path | None" = None):
    """Counts plus the self-improvement flags (consolidation / rewrite / stale)."""
    root = root or kb_root()
    by = {"type": {}, "confidence": {}, "source": {}}
    unmerged = total = 0
    rewrite_due, stale_seeds = [], []
    today = datetime.date.today()
    for _path, meta, err in all_cards(root):
        if err or not meta:
            continue
        total += 1
        for k in by:
            v = meta.get(k, "?")
            by[k][v] = by[k].get(v, 0) + 1
        if str(meta.get("merged", "")).lower() == "false":
            unmerged += 1
        absorbed = int(meta.get("absorbed", 0) or 0)
        if absorbed >= REWRITE_AFTER:
            rewrite_due.append({"id": meta.get("id", "?"), "absorbed": absorbed})
        if (meta.get("type") != "lesson"
                and meta.get("confidence") == "seed"
                and int(meta.get("evidence", 0) or 0) == 0):
            try:
                age = (today - datetime.date.fromisoformat(meta.get("updated", ""))).days
            except ValueError:
                age = -1
            if age >= STALE_SEED_DAYS:
                stale_seeds.append({"id": meta.get("id", "?"), "days": age})
    return {
        "cards": total,
        "by": by,
        "unmerged_lessons": unmerged,
        "consolidation_due": unmerged >= 10,
        "rewrite_due": rewrite_due,
        "stale_seeds": stale_seeds,
    }


def build_index(root: "Path | None" = None):
    """Regenerate kb/INDEX.md. Returns its path."""
    root = root or kb_root()
    lines = ["# KB index", "",
             "One line per card. Regenerate with `python3 bridge/kb.py index`.",
             "Read this first, then open the cards your task needs "
             "(see kb/README.md).", ""]
    by_dir = {}
    for path, meta, err in all_cards(root):
        if err or not meta:
            continue
        by_dir.setdefault(path.relative_to(root).parts[0], []).append((path, meta))
    for d in sorted(by_dir):
        lines.append("## %s/" % d)
        for path, meta in by_dir[d]:
            flags = "%s/%s ev:%s" % (meta.get("confidence", "?"),
                                     meta.get("type", "?"),
                                     meta.get("evidence", "?"))
            if int(meta.get("absorbed", 0) or 0) > 0:
                flags += " abs:%s" % meta["absorbed"]
            if str(meta.get("merged", "")).lower() == "false":
                flags += " UNMERGED"
            lines.append("- `%s` [%s] (%s) - %s"
                         % (path.relative_to(root), ",".join(meta.get("genres", [])),
                            flags, meta.get("title", "?")))
        lines.append("")
    out = root / "INDEX.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return str(out)
