#!/usr/bin/env python3
"""Audit rendered geometry using named references and optional texture diagnostics.

Texture averages cannot identify gravity, a horizon, or architectural intent.
They NEVER certify level or diagnose keystone. Use --references for measured
points on visually identified structures, --grid to inspect the actual guides,
and --json-out to preserve evidence. Re-select the same features after edits.
Positive angles mean clockwise: vertical tops right, horizontal right ends down.
Lightroom's parameter axes/signs still require a reversible rendered trial.

Reference JSON requires image_size, image_sha256, references (label, axis,
reason, points in normalized [x,y]), and optional unavailable axis reasons.
See bridge/geometry-audit.md for an example and the delivery protocol.
"""
import argparse
import hashlib
import json
from pathlib import Path
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


def fit_reference(ref, size):
    """Fit supplied physical-pixel coordinates, not square-normalized angles.

This measures the annotated line, not whether it is a valid world reference.
Two endpoints have zero fit residual by definition, not zero uncertainty.
"""
    if ref.get("axis") not in ("vertical", "horizontal"):
        raise ValueError("reference axis must be vertical or horizontal")
    if not str(ref.get("label", "")).strip() or not str(ref.get("reason", "")).strip():
        raise ValueError("each reference needs a label and a reason it is credible")
    points = np.asarray(ref.get("points", []), dtype=float)
    if points.ndim != 2 or points.shape[1] != 2 or len(points) < 2:
        raise ValueError("each reference needs at least two [x,y] points")
    if not np.isfinite(points).all() or (points < 0).any() or (points > 1).any():
        raise ValueError("reference points must be finite fractions in [0,1]")
    px = points * np.asarray(size)
    center = px.mean(axis=0)
    _, singular, vectors = np.linalg.svd(px - center, full_matrices=False)
    if singular[0] < 1:
        raise ValueError("reference points have no measurable span")
    direction = vectors[0]
    theta = np.degrees(np.arctan2(direction[1], direction[0]))
    baseline = 90 if ref["axis"] == "vertical" else 0
    angle = float((theta - baseline + 90) % 180 - 90)
    along = (px - center) @ direction
    ends = np.array([center + along.min()*direction, center + along.max()*direction])
    normal = np.array([-direction[1], direction[0]])
    rms = float(np.sqrt(np.mean(((px-center) @ normal)**2)))
    span = float(np.ptp(along))
    warnings = []
    if len(points) == 2:
        warnings.append("two endpoints: fit residual is not an accuracy estimate")
    if span < 100:
        warnings.append("short reference: small placement errors can dominate angle")
    if abs(angle) > 30:
        warnings.append("far from requested axis: verify feature and axis choice")
    return {**ref, "angle_deg_clockwise": angle, "span_px": span,
            "rms_px": rms, "point_count": len(points), "warnings": warnings,
            "fitted_endpoints_px": ends.tolist()}


def load_references(path, size, digest):
    data = json.loads(Path(path).read_text())
    if data.get("image_size") != list(size) or data.get("image_sha256") != digest:
        raise ValueError("reference file does not match this render; re-check coordinates and image hash")
    refs = [fit_reference(ref, size) for ref in data.get("references", [])]
    unavailable = data.get("unavailable", {})
    if not isinstance(unavailable, dict):
        raise ValueError("unavailable must map axis names to reasons")
    for axis, reason in unavailable.items():
        if axis not in ("vertical", "horizontal") or not isinstance(reason, str) or not reason.strip():
            raise ValueError("unavailable axes need a non-empty explanation")
        if any(r["axis"] == axis for r in refs):
            raise ValueError("an axis cannot have references and be unavailable")
    return refs, unavailable


def axis_review(refs, unavailable):
    return {axis: {"status": "measured_review_required" if any(r["axis"] == axis for r in refs)
                  else "unavailable" if axis in unavailable else "unresolved",
                  "reason": unavailable.get(axis, "Inspect named references and final composition")}
            for axis in ("vertical", "horizontal")}


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
    p.add_argument("--references", help="JSON of named reference points for this exact render")
    p.add_argument("--json-out", help="save measurements, image identity and axis review status")
    args = p.parse_args()
    if not 0 < args.band < 90:
        p.error("--band must be between 0 and 90 degrees")

    calibrate()
    img = Image.open(args.image).convert("RGB")
    arr = np.asarray(img)
    digest = hashlib.sha256(Path(args.image).read_bytes()).hexdigest()
    refs, unavailable = [], {}
    if args.references:
        try:
            refs, unavailable = load_references(args.references, img.size, digest)
        except (ValueError, TypeError, KeyError) as exc:
            p.error(str(exc))

    full, left, right = measure(arr, "whole-frame verticals", args.band)

    H, W = arr.shape[:2]
    for spec in args.region:
        try:
            l, r, t, b = (float(x) for x in spec.split(","))
            if not (0 <= l < r <= 1 and 0 <= t < b <= 1):
                raise ValueError()
        except ValueError:
            p.error("--region requires 0 <= L < R <= 1 and 0 <= T < B <= 1")
        sub = arr[int(t * H):int(b * H), int(l * W):int(r * W)]
        if min(sub.shape[:2]) < 3:
            p.error("--region is too small to measure")
        measure(sub, f"region {spec} verticals", args.band)
        if args.horizontals:
            measure(np.rot90(sub), f"region {spec} horizontals", args.band)

    if args.horizontals:
        rot = np.asarray(img.transpose(Image.Transpose.ROTATE_90))
        measure(rot, "whole-frame horizontals (rotated)", args.band)

    print("Texture diagnostics only: no roll/keystone verdict and no level certification.")
    for ref in refs:
        print(f"reference {ref['label']} ({ref['axis']}): "
              f"{ref['angle_deg_clockwise']:+.2f}deg CW; span={ref['span_px']:.0f}px; "
              f"fit RMS={ref['rms_px']:.2f}px; {ref['reason']}")
    review = axis_review(refs, unavailable)
    for axis, status in review.items():
        print(f"{axis}: {status['status']} - {status['reason']}")
    print("Inspect the annotated features, proportions and borders. Never reuse reference "
          "coordinates after a crop/transform without matching the actual features.")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps({"image": str(Path(args.image).resolve()),
            "image_size": list(img.size), "image_sha256": digest,
            "references": refs, "axes": review,
            "status": "visual_review_required", "level_certified": False}, indent=2) + "\n")

    if args.grid:
        ov = img.convert("RGB").copy()
        dr = ImageDraw.Draw(ov)
        for fx in np.arange(0.05, 1.0, 0.05):
            dr.line([(int(fx * W), 0), (int(fx * W), H)], fill=(255, 0, 0), width=1)
        for fy in np.arange(0.1, 1.0, 0.1):
            dr.line([(0, int(fy * H)), (W, int(fy * H))], fill=(255, 255, 0), width=1)
        for ref in refs:
            ends = [tuple(p) for p in ref["fitted_endpoints_px"]]
            color = (0, 255, 255) if ref["axis"] == "vertical" else (255, 80, 255)
            dr.line(ends, fill=color, width=3)
            cx, cy = np.mean(ends, axis=0)
            if ref["axis"] == "vertical":
                dr.line([(cx, min(p[1] for p in ends)), (cx, max(p[1] for p in ends))], fill="white", width=1)
            else:
                dr.line([(min(p[0] for p in ends), cy), (max(p[0] for p in ends), cy)], fill="white", width=1)
            for x, y in np.asarray(ref["points"]) * [W, H]:
                dr.ellipse((x-4,y-4,x+4,y+4), fill=color)
            dr.text((cx+6,cy), f"{ref['label']} {ref['angle_deg_clockwise']:+.2f}deg",
                    fill=color, stroke_width=2, stroke_fill="black")
        ov.save(args.grid, quality=90)
        print(f"grid overlay -> {args.grid}")


if __name__ == "__main__":
    main()
