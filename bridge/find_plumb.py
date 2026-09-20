#!/usr/bin/env python3
"""find_plumb.py -- measure roll from ISOLATED VERTICAL STRUCTURES, not from the
whole frame's average edge direction.

Why this exists: the whole-frame structure-tensor estimator in measure_tilt.py
averages every near-vertical edge, so on a landscape with heavy foliage it can
report -0.64 deg for the frame while the one tall mast in it leans +1.44 deg - a
lean the eye catches immediately. Such a mast's
CENTRELINE (midpoint between its two edges, row by row) fits with a 0.25px
residual because a tapered mast is symmetric even though its edges are not
parallel. That is the measurement that matches what the eye calls "leaning".

Method: for every row, find gradient edge pairs (light->dark then dark->light)
separated by 3..MAXW px; link them down the frame into tracks; fit x vs y per
track; keep tracks that are long, straight (sub-pixel residual) and near-plumb.
Report the median of the surviving tracks plus their spread, so a frame with no
usable structure returns nothing instead of a confident wrong number.

    python3 bridge/find_plumb.py render.jpg [--min-rows 80] [--max-resid 0.6]

Positive angle = the structure leans right going DOWN, i.e. its TOP tips LEFT.
Correcting it needs `lrc crop --angle -<value>` -- but always confirm with a
two-candidate sweep, because the sign convention differs between references.
"""
import argparse
import numpy as np
from PIL import Image


def edge_pairs(row, thr, maxw):
    d = np.diff(row)
    pairs = []
    lefts = np.where(d < -thr)[0]
    rights = np.where(d > thr)[0]
    if lefts.size == 0 or rights.size == 0:
        return pairs
    for l in lefts:
        r = rights[np.searchsorted(rights, l)] if np.searchsorted(rights, l) < rights.size else None
        if r is None:
            continue
        wdt = r - l
        if 3 <= wdt <= maxw:
            pairs.append(((l + r) / 2.0, wdt))
    return pairs


def tracks(gray, thr=4.0, maxw=90, tol=2.5, min_rows=80):
    h, w = gray.shape
    live, done = [], []
    for y in range(h):
        ps = edge_pairs(gray[y], thr, maxw)
        used = set()
        for t in live:
            best, bi = None, None
            for i, (cx, wd) in enumerate(ps):
                if i in used:
                    continue
                dx = abs(cx - t["x"])
                if dx < tol and (best is None or dx < best):
                    best, bi = dx, i
            if bi is not None:
                used.add(bi)
                t["xs"].append(ps[bi][0]); t["ys"].append(y); t["x"] = ps[bi][0]; t["miss"] = 0
            else:
                t["miss"] += 1
        for i, (cx, wd) in enumerate(ps):
            if i not in used:
                live.append({"x": cx, "xs": [cx], "ys": [y], "miss": 0})
        nxt = []
        for t in live:
            if t["miss"] > 6:
                if len(t["ys"]) >= min_rows:
                    done.append(t)
            else:
                nxt.append(t)
        live = nxt
    done += [t for t in live if len(t["ys"]) >= min_rows]
    return done


def fit_tracks(gray, min_rows=80, max_resid=0.6, band=6.0):
    out = []
    for t in tracks(gray, min_rows=min_rows):
        ys = np.array(t["ys"], float); xs = np.array(t["xs"], float)
        for _ in range(3):
            p = np.polyfit(ys, xs, 1)
            r = xs - np.polyval(p, ys)
            s = r.std()
            k = np.abs(r) < 2.5 * max(s, 0.3)
            if k.all() or k.sum() < min_rows:
                break
            ys, xs = ys[k], xs[k]
        if len(ys) < min_rows:
            continue
        p = np.polyfit(ys, xs, 1)
        resid = float(np.std(xs - np.polyval(p, ys)))
        ang = float(np.degrees(np.arctan(p[0])))
        if resid <= max_resid and abs(ang) <= band:
            out.append({"angle": round(ang, 3), "rows": len(ys), "resid": round(resid, 2),
                        "x": round(float(xs.mean())), "y0": int(ys.min()), "y1": int(ys.max())})
    out.sort(key=lambda d: -d["rows"])
    return out


def verdict(fits, width, min_tracks=3, half_tol=0.45, max_resid=0.6):
    """Roll only when structures on BOTH SIDES of the frame lean the same way.

    Track agreement alone is NOT enough: converging verticals from a wide lens
    produce tightly-agreeing tracks too (A7400073 returned -3.94 deg across 7
    tracks at std 0.13, and it has no roll at all - it is pure keystone). Under
    ROLL every vertical in the frame rotates together, so the left-half median
    and the right-half median match; under KEYSTONE they differ in sign or
    magnitude. That split is the discriminator (kb: measure_tilt LEFT/RIGHT).

    Validated on a cityscape frame: left tracks (a TV tower, x~572) +1.43,
    right track (a Flame Tower edge, x~1566) +1.49 -> ROLL +1.44, which matched
    the hand measurement and the eye. Returns (angle, n, spread) or None.
    """
    good = [f for f in fits if f["resid"] <= max_resid and f["rows"] >= 80]
    if len(good) < min_tracks:
        return None
    L = [f["angle"] for f in good if f["x"] < width * 0.5]
    R = [f["angle"] for f in good if f["x"] >= width * 0.5]
    if not L or not R:
        return None
    ml, mr = float(np.median(L)), float(np.median(R))
    if abs(ml - mr) > half_tol:
        return None                      # keystone, or no coherent roll
    angs = np.array([f["angle"] for f in good])
    med = float(np.median([ml, mr]))
    near = angs[np.abs(angs - med) <= 0.6]
    if len(near) < min_tracks:
        return None
    return round(float(np.median(near)), 3), len(near), round(float(abs(ml - mr)), 3)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--min-rows", type=int, default=80)
    ap.add_argument("--max-resid", type=float, default=0.6)
    a = ap.parse_args()
    g = np.asarray(Image.open(a.image).convert("L")).astype(float)
    fits = fit_tracks(g, a.min_rows, a.max_resid)
    for f in fits[:12]:
        print(f)
    print("VERDICT:", verdict(fits, g.shape[1]))
