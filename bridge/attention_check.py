#!/usr/bin/env python3
"""Attention-hierarchy gate for publish-readiness - the KB's per-region scan, durable.

Implements the hard gate the KB records as a lesson: before calling
an edit "publish/IG ready," confirm the region a human would NAME as the subject
wins (or nearly wins) on brightness, and holds saturation/contrast, against every
other large region. A background element that out-brights the subject is a
competing-emphasis defect (core/composition.md) - fix by crop, local darken, or
targeted HSL, then re-run.

What it does:
- Splits the render into an N×M tile grid; per tile prints mean luma, local
  contrast (std), mean saturation; flags the top-3 luma and top-3 sat tiles.
- Optional --region NAME:L,R,T,B (repeatable) measures named boxes (e.g. the
  subject vs its rivals) for a precise verdict.
- Optional --squint OUT.jpg writes a ~200px thumbnail - the figure-ground squint
  test (does the subject still read at feed-scroll size?).

Tile letters are rows A.., columns 1.. (A1 = top-left).

Usage:
  python3 bridge/attention_check.py render.jpg
  python3 bridge/attention_check.py render.jpg --region subject:.55,1,.05,.6 \
      --region rival-water:0,.5,.7,1 --squint /tmp/squint.jpg
"""
import argparse

import numpy as np
from PIL import Image


def stats(rgb):
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    mx = rgb.max(axis=2)
    mn = rgb.min(axis=2)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1), 0)
    return lum.mean(), lum.std(), sat.mean()


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("image")
    p.add_argument("--grid", default="4x6", metavar="ROWSxCOLS",
                   help="tile grid (default 4x6)")
    p.add_argument("--region", action="append", default=[], metavar="NAME:L,R,T,B",
                   help="named frame-fraction box (repeatable)")
    p.add_argument("--squint", metavar="OUT.jpg",
                   help="write a ~200px thumbnail for the figure-ground squint test")
    args = p.parse_args()

    img = Image.open(args.image).convert("RGB")
    rgb = np.asarray(img).astype(np.float64)
    H, W = rgb.shape[:2]

    rows, cols = (int(x) for x in args.grid.lower().split("x"))
    table = []
    for i in range(rows):
        for j in range(cols):
            tile = rgb[i * H // rows:(i + 1) * H // rows,
                       j * W // cols:(j + 1) * W // cols]
            table.append((f"{chr(65 + i)}{j + 1}", *stats(tile)))
    top_lum = {t[0] for t in sorted(table, key=lambda t: -t[1])[:3]}
    top_sat = {t[0] for t in sorted(table, key=lambda t: -t[3])[:3]}
    print(f"tile grid {rows}x{cols} (A1 top-left): luma / contrast(std) / sat "
          f"[*L = top-3 luma, *S = top-3 sat]")
    for i in range(rows):
        cells = []
        for j in range(cols):
            key, lm, sd, st = table[i * cols + j]
            mark = ("*L" if key in top_lum else "  ") + ("*S" if key in top_sat else "  ")
            cells.append(f"{key} {lm:5.1f}/{sd:4.1f}/{st:.2f}{mark}")
        print("  " + " | ".join(cells))

    if args.region:
        print("named regions:")
        results = {}
        for spec in args.region:
            name, box = spec.split(":")
            l, r, t, b = (float(x) for x in box.split(","))
            lm, sd, st = stats(rgb[int(t * H):int(b * H), int(l * W):int(r * W)])
            results[name] = lm
            print(f"  {name}: luma={lm:.1f} contrast={sd:.1f} sat={st:.2f}")
        if len(results) > 1:
            subject = list(results)[0]
            rivals = {k: v for k, v in results.items() if k != subject}
            worst = max(rivals, key=rivals.get)
            diff = results[subject] - rivals[worst]
            verdict = "PASS" if diff > 0 else "FAIL"
            print(f"  gate ({subject} vs brightest rival {worst}): "
                  f"luma diff {diff:+.1f} -> {verdict}"
                  + ("" if diff > 0 else "  - subject loses on brightness; "
                     "crop, local-darken, or HSL the rival, then re-run"))

    if args.squint:
        th = img.copy()
        th.thumbnail((200, 200))
        th.save(args.squint, quality=90)
        print(f"squint thumbnail -> {args.squint} (does the subject still read?)")


if __name__ == "__main__":
    main()
