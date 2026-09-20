#!/usr/bin/env python3
"""Measure roll (tilt) and keystone on a rendered photo - the KB's structure-tensor recipe.

Implements kb/editing/crop-recompose.md §"Straightening and geometry" as a durable
tool so sessions stop re-deriving it (and its sign bugs) in /tmp:

- Magnitude-weighted double-angle mean over near-vertical edges (deviation from
  plumb, degrees; positive = top leans RIGHT / clockwise).
- Sign calibration on a synthetic bar runs on EVERY invocation and aborts loudly
  if the convention breaks.
- LEFT/RIGHT half split separates roll (same sign both halves; the mean is the
  roll) from keystone (halves leaning toward each other - crop --angle can't fix).
- Optional --region restricts to facade-like areas (fractions of the frame),
  essential when masts/rigging/boats contaminate the whole-frame measurement.
- Optional --horizontals cross-check: same routine on the 90°-rotated image;
  verticals≈0 but horizontals≠0 (or vice versa) indicates keystone, not roll.
- Optional --grid writes a red/yellow guide overlay for eyeball cross-check.

Workflow (the KB's angle-sweep): measure → apply ONE candidate `lrc crop --angle`
→ re-render → re-measure; ~1° applied moves the reading ~1°, and the zero-crossing
is the answer. The --angle sign Lightroom needs is NOT guaranteed to match this
tool's sign (it inverts between vertical and horizontal frames - see
kb/lightroom/lrc-15.md); if a candidate worsens the reading, flip the sign.

Usage:
  python3 bridge/measure_tilt.py render.jpg
  python3 bridge/measure_tilt.py render.jpg --region 0.78,1,0.05,0.55 --region 0.4,0.7,0.3,0.6
  python3 bridge/measure_tilt.py render.jpg --horizontals --grid /tmp/grid.jpg
"""
import argparse
import sys

import numpy as np
from PIL import Image, ImageDraw


def deviation_from_vertical(arr, band=15.0):
    """Mean deviation of near-vertical edges from plumb, in degrees.

    Positive = edges lean clockwise (top to the right). Returns (deg, n_pixels)
    or (None, 0) when there is too little edge signal to trust.
    """
    g = arr.astype(np.float64)
    if g.ndim == 3:
        g = g.mean(axis=2)
    gx = np.zeros_like(g)
    gy = np.zeros_like(g)
    gx[:, 1:-1] = g[:, 2:] - g[:, :-2]
    gy[1:-1, :] = g[2:, :] - g[:-2, :]
    mag = np.hypot(gx, gy)
    thresh = np.percentile(mag, 90)
    sel = mag > thresh
    if sel.sum() < 50:
        return None, 0
    # Edge tangent = gradient direction + 90°; vertical edge → tangent ±90°.
    phe = np.degrees(np.arctan2(gy[sel], gx[sel])) + 90.0
    dev = phe % 180.0 - 90.0  # deviation from plumb, (-90, 90]
    m = mag[sel]
    keep = np.abs(dev) < band
    if keep.sum() < 50:
        return None, 0
    d = np.radians(dev[keep])
    w = m[keep]
    # Double-angle mean: orientation is ±180°-ambiguous; a naive mean cancels.
    ang = 0.5 * np.arctan2(np.average(np.sin(2 * d), weights=w),
                           np.average(np.cos(2 * d), weights=w))
    return float(np.degrees(ang)), int(keep.sum())


def calibrate():
    """Verify the sign convention on synthetics; abort if broken."""
    syn = np.zeros((800, 800), dtype=np.uint8)
    syn[:, 390:410] = 255
    im = Image.fromarray(syn)
    plumb, _ = deviation_from_vertical(np.asarray(im))
    # PIL rotate(-2) leans the bar clockwise (top to the right).
    cw, _ = deviation_from_vertical(np.asarray(im.rotate(-2.0, resample=Image.BILINEAR)))
    ok = plumb is not None and abs(plumb) < 0.05 and cw is not None and 1.5 < cw < 2.5
    print(f"calibration: plumb={plumb:+.3f} (want 0.000)  known-CW-2deg={cw:+.2f} "
          f"(want ~+2) -> {'OK' if ok else 'BROKEN'}")
    if not ok:
        sys.exit("calibration failed - do not trust any number below; fix the tool first")


def fmt(dev, n):
    return f"{dev:+.2f}deg (n={n})" if dev is not None else "no signal"


def measure(arr, label, band):
    w = arr.shape[1]
    full, n = deviation_from_vertical(arr, band)
    left, nl = deviation_from_vertical(arr[:, : w // 2], band)
    right, nr = deviation_from_vertical(arr[:, w // 2:], band)
    print(f"{label}: full={fmt(full, n)}  left-half={fmt(left, nl)}  right-half={fmt(right, nr)}")
    return full, left, right


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("image", help="rendered JPEG to measure (>=1500px recommended)")
    p.add_argument("--region", action="append", default=[], metavar="L,R,T,B",
                   help="restrict to a frame-fraction box (repeatable); use for "
                        "facade-only measurement when masts/boats contaminate")
    p.add_argument("--band", type=float, default=15.0,
                   help="max deviation from plumb an edge may have to count (deg, default 15)")
    p.add_argument("--horizontals", action="store_true",
                   help="also measure horizontal edges (90deg-rotated cross-check)")
    p.add_argument("--grid", metavar="OUT.jpg", help="write a guide-line overlay for visual check")
    args = p.parse_args()

    calibrate()
    img = Image.open(args.image)
    arr = np.asarray(img)

    full, left, right = measure(arr, "whole-frame verticals", args.band)

    H, W = arr.shape[:2]
    for spec in args.region:
        l, r, t, b = (float(x) for x in spec.split(","))
        sub = arr[int(t * H):int(b * H), int(l * W):int(r * W)]
        measure(sub, f"region {spec}", args.band)

    if args.horizontals:
        rot = np.asarray(img.transpose(Image.Transpose.ROTATE_90))
        hf, _, _ = measure(rot, "whole-frame horizontals (rotated)", args.band)
        if full is not None and hf is not None and abs(full - hf) > 1.0:
            print("verdict: verticals and horizontals disagree by "
                  f"{abs(full - hf):.1f}deg -> KEYSTONE component (rotation can't fix it)")

    if full is not None and left is not None and right is not None:
        if left * right < 0 and abs(left - right) > 1.0:
            print(f"verdict: halves lean toward each other -> KEYSTONE; "
                  f"roll is the mean = {(left + right) / 2:+.2f}deg")
        else:
            print(f"verdict: consistent lean -> ROLL {full:+.2f}deg "
                  f"(positive = top leans right)")
        print("action: <0.3deg ignore; 0.3-0.5 judgment call; >=0.5 correct. "
              "Apply ONE candidate `lrc crop --angle`, re-render, RE-MEASURE; "
              "flip the sign if it worsened (LrC's sign flips between "
              "vertical/horizontal frames).")

    if args.grid:
        ov = img.convert("RGB").copy()
        dr = ImageDraw.Draw(ov)
        for fx in np.arange(0.05, 1.0, 0.05):
            dr.line([(int(fx * W), 0), (int(fx * W), H)], fill=(255, 0, 0), width=1)
        for fy in np.arange(0.1, 1.0, 0.1):
            dr.line([(0, int(fy * H)), (W, int(fy * H))], fill=(255, 255, 0), width=1)
        ov.save(args.grid, quality=90)
        print(f"grid overlay -> {args.grid}")


if __name__ == "__main__":
    main()
