#!/usr/bin/env python3
"""
cull.py -- the full two-stage culling funnel for the Claude Bridge.

This is the DURABLE home for the pipeline (don't regenerate it in /tmp). It builds
on review_folder.py's bridge plumbing. The script does the mechanical work;
the aesthetic rating is done by Claude (or parallel subagents) reading the sheets.

Two-stage funnel (input resolution matters more than the model):

  STAGE 1 - TRIAGE (all photos, cheap):
      In Lightroom open the folder, Cmd+A (Select All), then:
        python3 bridge/cull.py triage
      -> renders every targeted photo at ~800px, tiles labeled contact sheets,
         and writes manifest.json + ratings_template.csv.
      Read sheets/*.jpg (Sonnet subagents are fine here) and rate each cell by its
      short KEY = sheet+cell (e.g. "A3", "B7") -- never transcribe long uuids.
      Put rough stars in a CSV (key,rating[,flag,label]); reject/dedupe bursts.

  STAGE 2 - TOP TIER (the >=4* keepers, full-res):
        python3 bridge/cull.py hires --from-csv ratings.csv --min 4
      -> renders each keeper one-per-file at ~2048px. Re-rate THESE with Opus,
         judging *edited potential* (don't penalize flat/dark RAWs). Only this
         reliably yields true 4*/5* and recovers night/blue-hour heroes.

  WRITEBACK (verified):
        python3 bridge/cull.py writeback final_ratings.csv
      -> applies rating/flag/label to the catalog by uuid, re-selecting and
         reading back each one (select_photo can go stale mid-burst); retries on
         mismatch. Use --dry-run to preview without touching the catalog.

KEY = sheet+cell (deterministically mapped to the photo in manifest.json), so the
CSV can also use a raw uuid or the global index (gidx) as the key.

Outputs default to /tmp/<workdir> (disposable; --out to change). Requires Pillow
for the sheets:  pip3 install --user Pillow
"""

import argparse
import csv
import json
import os
import string
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from review_folder import lrc, targeted_photos  # noqa: E402  (build on existing plumbing)

DEFAULT_OUT = "/tmp/lrcull"


# --------------------------------------------------------------------------- #
# Manifest / key helpers
# --------------------------------------------------------------------------- #

def sheet_id(i):
    """0 -> A, 25 -> Z, 26 -> AA ... (bijective base-26)."""
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = string.ascii_uppercase[r] + s
    return s


def manifest_path(out):
    return os.path.join(out, "manifest.json")


def load_manifest(out):
    p = manifest_path(out)
    if not os.path.exists(p):
        sys.exit("No manifest at %s -- run `cull.py triage --out %s` first." % (p, out))
    with open(p) as f:
        return json.load(f)


def build_lookup(manifest):
    """Map every key form (KEY, uuid, gidx) -> photo record."""
    by = {}
    for r in manifest["photos"]:
        by[r["key"].upper()] = r
        by[str(r["uuid"]).upper()] = r
        by[str(r["gidx"])] = r
    return by


def resolve_key(k, by):
    k = (k or "").strip()
    return by.get(k.upper()) or by.get(k)


def _int_or_none(v):
    v = (v or "").strip()
    if not v:
        return None
    try:
        return int(round(float(v)))
    except ValueError:
        return None


def _safe(name):
    base = os.path.splitext(name)[0]
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in base) or "img"


def _self():
    try:
        return os.path.relpath(__file__)
    except ValueError:
        return __file__


# --------------------------------------------------------------------------- #
# Contact sheets (Pillow)
# --------------------------------------------------------------------------- #

def _load_font(size):
    from PIL import ImageFont
    for fp in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf",
               "/System/Library/Fonts/Supplemental/Arial.ttf",
               "/System/Library/Fonts/Helvetica.ttc"):
        if os.path.exists(fp):
            try:
                return ImageFont.truetype(fp, size)
            except OSError:
                pass
    return ImageFont.load_default()


def _tw(draw, text, font):
    try:
        return int(draw.textlength(text, font=font))
    except Exception:
        try:
            return font.getsize(text)[0]
        except Exception:
            return len(text) * 10


def build_sheets(recs, out, cols):
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("\nPillow not installed - skipping contact sheets "
              "(pip3 install --user Pillow). Per-photo JPEGs are in", out)
        return []

    big, small = _load_font(34), _load_font(18)
    CW, PAD, LABEL = 460, 10, 44
    BG, FG, KEYC, SHEETBG = (28, 28, 28), (232, 232, 232), (255, 209, 92), (10, 10, 10)

    by_sheet = {}
    for r in recs:
        if r["ok"]:
            by_sheet.setdefault(r["sheet"], []).append(r)

    sheetdir = os.path.join(out, "sheets")
    os.makedirs(sheetdir, exist_ok=True)
    sheets = []
    for sid in sorted(by_sheet, key=lambda s: (len(s), s)):
        group = sorted(by_sheet[sid], key=lambda r: r["cell"])
        cells = []
        for r in group:
            path = os.path.join(out, r["img"])
            if not os.path.exists(path):
                continue
            im = Image.open(path).convert("RGB")
            w, h = im.size
            ch = int(h * CW / w)
            im = im.resize((CW, ch))
            cell = Image.new("RGB", (CW, ch + LABEL), BG)
            cell.paste(im, (0, LABEL))
            d = ImageDraw.Draw(cell)
            d.text((8, 4), r["key"], fill=KEYC, font=big)
            fn = r["filename"]
            d.text((CW - _tw(d, fn, small) - 8, 16), fn, fill=FG, font=small)
            cells.append(cell)
        if not cells:
            continue
        rows = (len(cells) + cols - 1) // cols
        chmax = max(c.size[1] for c in cells)
        SW = cols * CW + (cols + 1) * PAD
        SH = rows * chmax + (rows + 1) * PAD
        sheet = Image.new("RGB", (SW, SH), SHEETBG)
        for i, cell in enumerate(cells):
            rr, cc = divmod(i, cols)
            sheet.paste(cell, (PAD + cc * (CW + PAD), PAD + rr * (chmax + PAD)))
        op = os.path.join(sheetdir, "%s.jpg" % sid)
        sheet.save(op, quality=86)
        sheets.append((sid, op, len(cells)))
    return sheets


# --------------------------------------------------------------------------- #
# Stage 1: triage
# --------------------------------------------------------------------------- #

def cmd_triage(args):
    photos = targeted_photos()
    print("Targeted photos: %d  (if that's just 1, press Cmd+A in the grid first)"
          % len(photos))
    if not photos:
        return 1
    os.makedirs(args.out, exist_ok=True)
    print("Rendering at %dpx into %s ..." % (args.size, args.out))
    recs = []
    for i, p in enumerate(photos):
        gidx = i + 1
        sid = sheet_id(i // args.per)
        cell = i % args.per + 1
        img = "%03d.jpg" % gidx
        resp = lrc("render", "--uuid", p["uuid"], "--out",
                   os.path.join(args.out, img), "--size", str(args.size))
        ok = bool(resp.get("ok"))
        folder = os.path.basename(os.path.dirname(p.get("path", "") or ""))
        recs.append({"gidx": gidx, "uuid": p["uuid"], "filename": p.get("filename", ""),
                     "folder": folder, "img": img, "sheet": sid, "cell": cell,
                     "key": sid + str(cell), "ok": ok})
        key = sid + str(cell)
        sys.stdout.write((key + " ") if ok else ("X" + key + " "))
        sys.stdout.flush()
    print()

    manifest = {"size": args.size, "cols": args.cols, "per": args.per,
                "count": len(recs), "out": args.out, "photos": recs}
    with open(manifest_path(args.out), "w") as f:
        json.dump(manifest, f, indent=2)
    with open(os.path.join(args.out, "ratings_template.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["key", "rating", "flag", "label"])
        for r in recs:
            if r["ok"]:
                w.writerow([r["key"], "", "", ""])

    sheets = build_sheets(recs, args.out, args.cols)
    print("\nContact sheets (Read these; each cell is labeled by KEY = sheet+cell):")
    for sid, op, n in sheets:
        print("  %-3s %2d cells   %s" % (sid, n, op))
    print("\nManifest:          %s" % manifest_path(args.out))
    print("Ratings template:  %s" % os.path.join(args.out, "ratings_template.csv"))
    print("\nNext: rate cells by KEY into a CSV, then:")
    print("  python3 %s hires --from-csv ratings.csv --min 4 --out %s" % (_self(), args.out))
    print("  python3 %s writeback ratings.csv --out %s" % (_self(), args.out))
    return 0


# --------------------------------------------------------------------------- #
# Stage 2: hi-res keeper renders
# --------------------------------------------------------------------------- #

def cmd_hires(args):
    manifest = load_manifest(args.out)
    by = build_lookup(manifest)
    picked = []
    if args.from_csv:
        with open(args.from_csv) as f:
            for row in csv.DictReader(f):
                rating = _int_or_none(row.get("rating"))
                if rating is None or rating < args.min:
                    continue
                rec = resolve_key(row.get("key", ""), by)
                if rec:
                    picked.append(rec)
    for k in (args.keys or "").split(","):
        if k.strip():
            rec = resolve_key(k, by)
            if rec:
                picked.append(rec)
            else:
                print("  ? %s unresolved" % k.strip())

    seen, uniq = set(), []
    for r in picked:
        if r["uuid"] not in seen:
            seen.add(r["uuid"])
            uniq.append(r)
    if not uniq:
        sys.exit("No keepers resolved. Pass KEYS or --from-csv ratings.csv --min N.")

    hires_dir = os.path.join(args.out, "hires")
    os.makedirs(hires_dir, exist_ok=True)
    print("Hi-res: %d keepers at %dpx -> %s" % (len(uniq), args.size, hires_dir))
    for r in uniq:
        dst = os.path.join(hires_dir, "%03d_%s.jpg" % (r["gidx"], _safe(r["filename"])))
        if args.dry_run:
            print("  . %-5s %s" % (r["key"], dst))
            continue
        resp = lrc("render", "--uuid", r["uuid"], "--out", dst, "--size", str(args.size))
        print(("  v " if resp.get("ok") else "  x ") + "%-5s %s"
              % (r["key"], os.path.basename(dst)))
    if not args.dry_run:
        print("\nRead these and re-rate with Opus (judge EDITED potential), then writeback.")
    return 0


# --------------------------------------------------------------------------- #
# Writeback (verified)
# --------------------------------------------------------------------------- #

_FLAG_PICK = {"pick": 1, "reject": -1, "none": 0}


def _active(resp):
    return ((resp or {}).get("result") or {}).get("active") or {}


def verify_set(uuid, rating=None, flag=None, label=None, tries=3):
    """Select the photo, confirm it's active, set, then read back; retry on drift."""
    want_label = None if label is None else ("" if label.lower() == "none" else label.lower())
    why = ""
    for attempt in range(1, tries + 1):
        a = _active(lrc("select", "--uuid", uuid))
        if a.get("uuid") != uuid:
            why = "select landed on %s" % a.get("uuid")
            continue
        if rating is not None:
            lrc("rating", str(rating))
        if flag is not None:
            lrc("flag", flag)
        if label is not None:
            lrc("label", label)
        a = _active(lrc("select", "--uuid", uuid))  # read-back
        ok = a.get("uuid") == uuid
        if rating is not None:
            ok = ok and _int_or_none(str(a.get("rating"))) == rating
        if flag is not None:
            ok = ok and a.get("pickStatus") == _FLAG_PICK.get(flag)
        if label is not None:
            ok = ok and (a.get("colorLabel") or "").lower() == want_label
        if ok:
            return True, attempt, ""
        why = "read-back mismatch (uuid=%s rating=%s pick=%s label=%s)" % (
            a.get("uuid"), a.get("rating"), a.get("pickStatus"), a.get("colorLabel"))
    return False, tries, why


def cmd_writeback(args):
    manifest = load_manifest(args.out)
    by = build_lookup(manifest)
    with open(args.csv) as f:
        rows = list(csv.DictReader(f))

    ok = fail = skip = 0
    failures = []
    for row in rows:
        row = {(k or "").strip().lower(): v for k, v in row.items()}
        key = (row.get("key") or "").strip()
        if not key:
            continue
        rec = resolve_key(key, by)
        if not rec:
            print("  ? %-5s unresolved" % key)
            skip += 1
            failures.append((key, "unresolved"))
            continue
        rating = _int_or_none(row.get("rating"))
        flag = (row.get("flag") or "").strip().lower() or None
        label = (row.get("label") or "").strip().lower() or None
        if rating is None and flag is None and label is None:
            continue
        if args.dry_run:
            print("  . %-5s %-16s rating=%s flag=%s label=%s"
                  % (key, rec["filename"], rating, flag, label))
            ok += 1
            continue
        good, attempts, why = verify_set(rec["uuid"], rating, flag, label, args.tries)
        if good:
            ok += 1
            print("  v %-5s %-16s rating=%s flag=%s label=%s (try %d)"
                  % (key, rec["filename"], rating, flag, label, attempts))
        else:
            fail += 1
            failures.append((key, why))
            print("  x %-5s %-16s FAILED: %s" % (key, rec["filename"], why))

    verb = "DRY-RUN (no catalog changes)" if args.dry_run else "Writeback"
    print("\n%s: %d applied, %d failed, %d unresolved." % (verb, ok, fail, skip))
    if failures and not args.dry_run:
        print("Failures:")
        for k, w in failures:
            print("  %-5s %s" % (k, w))
    return 0 if fail == 0 else 1


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--out", default=DEFAULT_OUT,
                        help="workdir for renders/sheets/manifest (default %(default)s)")

    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("triage", parents=[common],
                       help="render all targeted photos + labeled contact sheets + manifest")
    t.add_argument("--size", type=int, default=800, help="long-edge px (default 800)")
    t.add_argument("--cols", type=int, default=4, help="sheet columns (default 4)")
    t.add_argument("--per", type=int, default=12, help="cells per sheet (default 12)")
    t.set_defaults(func=cmd_triage)

    h = sub.add_parser("hires", parents=[common],
                       help="full-res renders of keepers (one file each) for Opus re-rating")
    h.add_argument("keys", nargs="?", default="",
                   help="comma-separated KEYs/uuids/gidx (e.g. A3,B7,C1)")
    h.add_argument("--from-csv", dest="from_csv",
                   help="ratings CSV; takes rows with rating >= --min")
    h.add_argument("--min", type=int, default=4, help="min stars for --from-csv (default 4)")
    h.add_argument("--size", type=int, default=2048, help="long-edge px (default 2048)")
    h.add_argument("--dry-run", action="store_true", help="list what would render; don't render")
    h.set_defaults(func=cmd_hires)

    w = sub.add_parser("writeback", parents=[common],
                       help="apply rating/flag/label to the catalog, verified, by key")
    w.add_argument("csv", help="CSV with columns: key,rating[,flag,label]")
    w.add_argument("--tries", type=int, default=3, help="verify+retry attempts (default 3)")
    w.add_argument("--dry-run", action="store_true", help="resolve + preview; no catalog changes")
    w.set_defaults(func=cmd_writeback)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
