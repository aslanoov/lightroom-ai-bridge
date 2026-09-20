#!/usr/bin/env python3
"""
writeback_fast.py -- bulk rating/flag/label/title writeback in ONE bridge call
per photo, with the plug-in's own read-back as verification.

Why this exists (vs `cull.py writeback`):
  cull.py does select -> rating -> flag -> label -> select(read-back) = up to 5
  round-trips per photo. On an 800-frame cull that is ~4000 calls. The plug-in's
  `set_metadata` applies rating + flag + label + title + caption in a SINGLE
  write transaction and returns the read-back, so one call per photo is both
  faster and safer (no select/stale-selection window at all).

CSV columns (header required):  key[,rating][,flag][,label][,title]
  key    KEY (sheet+cell, e.g. AF10), uuid, or gidx -- resolved via manifest.json
  rating 0-5           flag  pick|reject|none        label red|yellow|green|blue|purple|none
  title  free text (written only with --titles; never overwrites a non-empty title)

Usage:
  python3 bridge/writeback_fast.py final.csv --out /tmp/lrcull-xyz
  python3 bridge/writeback_fast.py final.csv --out /tmp/lrcull-xyz --dry-run
  python3 bridge/writeback_fast.py final.csv --out /tmp/lrcull-xyz --titles

GOTCHA baked in: the CLI coerces a bare `none`/`null`/`nil` VALUE to JSON null,
which `set_metadata` then skips -- so "none" can never CLEAR a flag/label through
`call set_metadata`. This script routes every clear through the dedicated
`lrc flag none` / `lrc label none` subcommands, which do work.
"""

import argparse
import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from cull import build_lookup, load_manifest, resolve_key, _int_or_none  # noqa: E402
from review_folder import lrc  # noqa: E402

FLAG_PICK = {"pick": 1, "reject": -1, "none": 0}


def _res(resp):
    return ((resp or {}).get("result") or {}) if isinstance(resp, dict) else {}


def write_one(uuid, rating=None, flag=None, label=None, title=None, tries=3):
    """One set_metadata call; verify from its own read-back. Returns (ok, tries, why)."""
    why = ""
    for attempt in range(1, tries + 1):
        params = ["uuid=%s" % uuid]
        # Clears cannot ride along (bare 'none' -> JSON null -> silently skipped).
        clears = []
        if rating is not None:
            params.append("rating=%d" % rating)
        if flag is not None:
            (clears if flag == "none" else params).append(
                "flag none" if flag == "none" else "flag=%s" % flag)
        if label is not None:
            (clears if label == "none" else params).append(
                "label none" if label == "none" else "label=%s" % label)
        if title is not None:
            params.append("title=%s" % title)

        r = _res(lrc("call", "set_metadata", *params)) if len(params) > 1 else {}
        for c in clears:                       # dedicated subcommands DO clear
            r = _res(lrc(c.split()[0], c.split()[1], "--uuid", uuid))

        ok = r.get("uuid") == uuid
        if rating is not None:
            ok = ok and (_int_or_none(str(r.get("rating"))) or 0) == rating
        if flag is not None:
            ok = ok and r.get("pickStatus") == FLAG_PICK[flag]
        if label is not None:
            want = "" if label == "none" else label.lower()
            ok = ok and (r.get("colorLabel") or "").lower() == want
        if title is not None:
            ok = ok and (r.get("title") or "") == title
        if ok:
            return True, attempt, ""
        why = "read-back uuid=%s rating=%s pick=%s label=%s title=%r" % (
            r.get("uuid"), r.get("rating"), r.get("pickStatus"),
            r.get("colorLabel"), r.get("title"))
    return False, tries, why


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--out", default="/tmp/lrcull")
    ap.add_argument("--tries", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--titles", action="store_true",
                    help="also write the title column (skips photos that already have one)")
    a = ap.parse_args(argv)

    by = build_lookup(load_manifest(a.out))
    rows, unresolved = [], []
    with open(a.csv) as f:
        for row in csv.DictReader(f):
            rec = resolve_key(row.get("key", ""), by)
            if not rec:
                unresolved.append(row.get("key", "")); continue
            rows.append((rec, row))
    if unresolved:
        print("UNRESOLVED KEYS (%d): %s" % (len(unresolved), ", ".join(unresolved[:20])))

    print("%d rows -> catalog%s" % (len(rows), "  [DRY RUN]" if a.dry_run else ""))
    okc = fail = 0
    failures = []
    for i, (rec, row) in enumerate(rows, 1):
        rating = _int_or_none(row.get("rating"))
        flag = (row.get("flag") or "").strip().lower() or None
        label = (row.get("label") or "").strip().lower() or None
        title = (row.get("title") or "").strip() if a.titles else None
        title = title or None
        if a.dry_run:
            print("  . %-6s %-28s r=%s flag=%s label=%s title=%s"
                  % (rec["key"], rec["filename"], rating, flag, label, title))
            continue
        if title:  # never clobber an existing title
            cur = _res(lrc("call", "set_metadata", "uuid=%s" % rec["uuid"],
                           "rating=%d" % (rating if rating is not None else 0)))
            if (cur.get("title") or "").strip():
                title = None
        ok, tries, why = write_one(rec["uuid"], rating, flag, label, title, a.tries)
        if ok:
            okc += 1
        else:
            fail += 1
            failures.append((rec["key"], rec["filename"], why))
            print("  x %-6s %-28s %s" % (rec["key"], rec["filename"], why))
        if i % 50 == 0:
            print("  ... %d/%d  (ok %d, fail %d)" % (i, len(rows), okc, fail))
    if not a.dry_run:
        print("\nDONE: %d ok, %d failed" % (okc, fail))
        for k, fn, why in failures:
            print("  FAILED %-6s %-28s %s" % (k, fn, why))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
