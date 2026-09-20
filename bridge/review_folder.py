#!/usr/bin/env python3
"""
review_folder.py -- render the currently-targeted Lightroom photos and build
labeled contact sheets, so an agent can scan a whole folder efficiently.

Workflow this automates (the "cull a folder" recipe):
  1. In Lightroom, open the folder and press Cmd+A (Select All).
  2. Run:  python3 bridge/review_folder.py
  3. Read the printed contact sheets (sheets/sheet*.jpg) to pick the best.
  4. Flag picks by UUID with flag_picks() below, or:
       ./lrc select --uuid <uuid> && ./lrc flag pick

Each photo is rendered by UUID (read-only; does not change the catalog), so it
works on every targeted photo without disturbing develop settings.

Requires Pillow for the contact sheets:  pip3 install --user Pillow
(individual per-photo JPEGs are still produced even without Pillow.)
"""

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LRC = os.path.join(HERE, "lrc.py")


def lrc(*args):
    """Call the bridge CLI and return the parsed JSON response."""
    r = subprocess.run([sys.executable, LRC, "--raw", *args],
                       capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except ValueError:
        return {"ok": False, "error": (r.stdout + r.stderr).strip()}


def targeted_photos():
    d = lrc("list-selected")
    if not d.get("ok"):
        sys.exit("Cannot reach Lightroom: %s\nIs Lightroom open with the Claude "
                 "Bridge plug-in running? (./lrc ping)" % d.get("error"))
    return d["result"]["photos"]


def render_all(photos, outdir, size):
    os.makedirs(outdir, exist_ok=True)
    rows = []
    for i, p in enumerate(photos, 1):
        n = "%02d" % i
        dst = os.path.join(outdir, n + ".jpg")
        r = lrc("render", "--uuid", p["uuid"], "--out", dst, "--size", str(size))
        ok = r.get("ok")
        rows.append((n, p["uuid"], p.get("filename", ""), ok))
        sys.stdout.write(("%s " % n) if ok else ("X%s " % n))
        sys.stdout.flush()
    print()
    with open(os.path.join(outdir, "list.tsv"), "w") as f:
        for n, uuid, fn, ok in rows:
            f.write("%s\t%s\t%s\t%s\n" % (n, uuid, fn, "ok" if ok else "FAIL"))
    return rows


def build_sheets(outdir, cols, per):
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("\nPillow not installed - skipping contact sheets.")
        print("Install with:  pip3 install --user Pillow")
        print("Per-photo JPEGs are in:", outdir)
        return []

    names = {}
    for line in open(os.path.join(outdir, "list.tsv")):
        c = line.rstrip("\n").split("\t")
        if len(c) >= 3:
            names[c[0]] = c[2]

    font = None
    for fp in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf",
               "/System/Library/Fonts/Supplemental/Arial.ttf",
               "/System/Library/Fonts/Helvetica.ttc"):
        if os.path.exists(fp):
            try:
                font = ImageFont.truetype(fp, 18)
                break
            except OSError:
                pass
    if font is None:
        font = ImageFont.load_default()

    CW, PAD, LABEL = 460, 8, 26
    BG, FG, SHEETBG = (30, 30, 30), (240, 240, 240), (12, 12, 12)
    cells = []
    for n in sorted(names):
        path = os.path.join(outdir, n + ".jpg")
        if not os.path.exists(path):
            continue
        im = Image.open(path).convert("RGB")
        w, h = im.size
        ch = int(h * CW / w)
        im = im.resize((CW, ch))
        cell = Image.new("RGB", (CW, ch + LABEL), BG)
        cell.paste(im, (0, LABEL))
        ImageDraw.Draw(cell).text((5, 4), "%s  %s" % (n, names[n]), fill=FG, font=font)
        cells.append((n, cell))

    sheetdir = os.path.join(outdir, "sheets")
    os.makedirs(sheetdir, exist_ok=True)
    sheets = []
    for s in range(0, len(cells), per):
        g = cells[s:s + per]
        rows = (len(g) + cols - 1) // cols
        chmax = max(c.size[1] for _, c in g)
        SW = cols * CW + (cols + 1) * PAD
        SH = rows * chmax + (rows + 1) * PAD
        sheet = Image.new("RGB", (SW, SH), SHEETBG)
        for i, (n, cell) in enumerate(g):
            r, c = divmod(i, cols)
            sheet.paste(cell, (PAD + c * (CW + PAD), PAD + r * (chmax + PAD)))
        k = s // per + 1
        op = os.path.join(sheetdir, "sheet%d.jpg" % k)
        sheet.save(op, quality=86)
        sheets.append((op, g[0][0], g[-1][0]))
    return sheets


def flag_picks(indices, outdir):
    """Flag the given contact-sheet indices (e.g. [3,12,40]) as Picks."""
    uuids, names = {}, {}
    for line in open(os.path.join(outdir, "list.tsv")):
        c = line.rstrip("\n").split("\t")
        if len(c) >= 3:
            uuids[int(c[0])] = c[1]
            names[int(c[0])] = c[2]
    for i in indices:
        lrc("select", "--uuid", uuids[i])
        d = lrc("flag", "pick")
        print("  #%02d %s -> %s" % (i, names[i], "ok" if d.get("ok") else d.get("error")))


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--size", type=int, default=800, help="render long-edge px (default 800)")
    ap.add_argument("--cols", type=int, default=4, help="contact-sheet columns (default 4)")
    ap.add_argument("--per", type=int, default=16, help="cells per sheet (default 16)")
    ap.add_argument("--out", default="/tmp/lrreview", help="output dir (default /tmp/lrreview)")
    ap.add_argument("--flag", help="comma-separated indices to flag as Picks, then exit "
                                   "(uses an existing %(default)s/list.tsv)")
    args = ap.parse_args(argv)

    if args.flag:
        flag_picks([int(x) for x in args.flag.split(",") if x.strip()], args.out)
        return 0

    photos = targeted_photos()
    print("Targeted photos: %d  (if that's just 1, press Cmd+A in the grid first)" % len(photos))
    if not photos:
        return 1
    print("Rendering at %dpx into %s ..." % (args.size, args.out))
    render_all(photos, args.out, args.size)
    sheets = build_sheets(args.out, args.cols, args.per)
    print("\nContact sheets (Read these):")
    for op, a, b in sheets:
        print("  %s   (#%s..#%s)" % (op, a, b))
    print("\nIndex -> filename map: %s/list.tsv" % args.out)
    print("To flag picks:  python3 %s --flag 3,12,40" % os.path.relpath(__file__))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
