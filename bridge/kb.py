#!/usr/bin/env python3
"""kb.py -- CLI for the photography knowledge base in kb/.

The KB logic lives in the importable ``lightroom_bridge.kb`` module (under
``src/``); this file is the command-line front end and keeps
``python3 bridge/kb.py …`` working from a source checkout without installing
anything. The same logic backs the MCP server's ``kb_*`` tools, so the CLI and
MCP never drift apart.

Commands:
  index                       rebuild kb/INDEX.md from card frontmatter
  recall --genres a,b --task cull|edit [--full]
                              print the cards an agent should load
  new --title T [--genres g] [--tags t]
                              scaffold a lesson card in kb/learned/
  bump <card-id> [--to tested|proven]
                              increment a card's evidence / promote confidence
  absorb <card-id> [--reset]  count a lesson merged into a card at
                              consolidation; --reset after a deep rewrite
  validate                    check every card's frontmatter
  stats                       counts + consolidation/rewrite/stale-seed flags

Stdlib-only, Python 3.9+.
"""

import argparse
import os
import sys

# Put the repo's src/ on the path so `lightroom_bridge` imports without install.
_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if os.path.isdir(os.path.join(_SRC, "lightroom_bridge")) and _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from lightroom_bridge import kb as _kb  # noqa: E402


def cmd_index(_args):
    print("wrote %s" % _kb.build_index())


def cmd_recall(args):
    hits = _kb.recall(genres=args.genres or "all", task=args.task)
    for h in hits:
        print("%s\t%s" % (h["path"], h["title"]))
        if args.full:
            print(h["body"])
            print("\n" + "=" * 72 + "\n")
    if not hits:
        print("no cards matched", file=sys.stderr)


def cmd_new(args):
    path = _kb.new_lesson(title=args.title,
                          situation=args.situation or "",
                          what_happened=args.what_happened or "",
                          rule=args.rule or "",
                          merge_target=args.merge_target or "",
                          genres=args.genres or "all",
                          tags=args.tags or "uncategorized")
    filled = args.situation and args.what_happened and args.rule
    _kb.build_index()
    print("created %s%s" % (os.path.join(str(_kb.kb_root()), path),
                            "" if filled else " - fill in the empty sections"))


def cmd_read(args):
    card = _kb.read_card(args.card_id)
    if not card:
        sys.exit("no card with id %r" % args.card_id)
    print(card["body"])


def cmd_bump(args):
    r = _kb.bump(args.card_id, to=args.to)
    _kb.build_index()
    print("%s → evidence %d, confidence %s"
          % (r["id"], r["evidence"], r["confidence"]))
    if not args.to and r["confidence"] == "seed" and r["evidence"] >= 3:
        print("hint: 3+ successes - consider --to tested")


def cmd_absorb(args):
    r = _kb.absorb(args.card_id, reset=args.reset)
    _kb.build_index()
    print("%s → absorbed %d%s"
          % (r["id"], r["absorbed"],
             " (rewrite counter reset)" if args.reset else ""))
    if r["rewrite_due"]:
        print("hint: %d+ absorbed lessons - deep revision due (kb/README.md §5)"
              % _kb.REWRITE_AFTER)


def cmd_validate(_args):
    problems, count = _kb.validate()
    for p in problems:
        print("FAIL %s" % p)
    print("%d cards, %d problems" % (count, len(problems)))
    sys.exit(1 if problems else 0)


def cmd_stats(_args):
    s = _kb.stats()
    print("%d cards" % s["cards"])
    for k in s["by"]:
        print("  %s: %s" % (k, ", ".join("%s=%d" % kv
                                         for kv in sorted(s["by"][k].items()))))
    print("  unmerged lessons: %d" % s["unmerged_lessons"])
    if s["consolidation_due"]:
        print("CONSOLIDATION DUE: run the light pass (kb/README.md §4)")
    for r in s["rewrite_due"]:
        print("REWRITE DUE: %s (absorbed %d lessons - deep revision, kb/README.md §5)"
              % (r["id"], r["absorbed"]))
    for r in s["stale_seeds"]:
        print("STALE SEED: %s (untested for %d days - re-verify or refresh research)"
              % (r["id"], r["days"]))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("index")
    p = sub.add_parser("recall")
    p.add_argument("--genres", help="comma-separated, e.g. travel,street")
    p.add_argument("--task", choices=["cull", "edit"], default="edit")
    p.add_argument("--full", action="store_true", help="print card contents too")
    p = sub.add_parser("new")
    p.add_argument("--title", required=True)
    p.add_argument("--genres")
    p.add_argument("--tags")
    p.add_argument("--situation", help="one-shot fill: when does the lesson apply")
    p.add_argument("--what-happened", dest="what_happened",
                   help="one-shot fill: observation incl. the actual numbers")
    p.add_argument("--rule", help="one-shot fill: imperative, checkable rule")
    p.add_argument("--merge-target", dest="merge_target",
                   help="card id this lesson should fold into at consolidation")
    p = sub.add_parser("read")
    p.add_argument("card_id")
    p = sub.add_parser("bump")
    p.add_argument("card_id")
    p.add_argument("--to", choices=["tested", "proven"])
    p = sub.add_parser("absorb")
    p.add_argument("card_id")
    p.add_argument("--reset", action="store_true",
                   help="zero the counter after a deep rewrite")
    sub.add_parser("validate")
    sub.add_parser("stats")
    args = ap.parse_args()
    try:
        globals()["cmd_%s" % args.cmd](args)
    except (_kb.KBNotFound, KeyError, ValueError, FileExistsError) as e:
        sys.exit(str(e).strip("'\""))


if __name__ == "__main__":
    main()
