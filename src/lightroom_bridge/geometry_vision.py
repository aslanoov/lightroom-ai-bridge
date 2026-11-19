"""Named line audits, conservative feature transfer, and bounded local proposals."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from . import geometry as g
from ._geometry_lines import load_references, axis_review, fit_reference


def audit(capture_path, references, out, review=None):
    cap = g.validate_capture(capture_path)
    refs, unavailable = load_references(references, cap['image_size'], cap['image_sha256'])
    if len({r['label'] for r in refs}) != len(refs):
        raise ValueError('Reference labels must be unique')
    for ref in refs:
        target = g.number(ref.get('target_angle', 0), 'reference target', -45, 45)
        if target and not str(ref.get('target_reason', '')).strip():
            raise ValueError('A nonlevel target needs an explicit photographic reason')
        ref['residual_deg'] = ref['angle_deg_clockwise']-target
        # Endpoint placement is a floor, never derive precision from zero TLS residual.
        placement = g.number(ref.get('placement_error_px', 2), 'placement uncertainty', .5, 100)
        ref['angle_uncertainty_deg'] = math.degrees(math.atan2(2*max(placement, ref['rms_px']), ref['span_px']))
        ref['endpoint_drift_px'] = abs(math.tan(math.radians(ref['residual_deg']))*ref['span_px'])
    axes = axis_review(refs, unavailable)
    result = {'schema': g.SCHEMA, 'kind': 'geometry-audit', 'capture': str(Path(capture_path).resolve()),
              'capture_sha256': g.digest(capture_path), 'image_sha256': cap['image_sha256'],
              'references': refs, 'axes': axes, 'status': 'visual_review_required',
              'level_certified': False}
    overlay = Path(out).resolve().with_suffix('.guides.jpg')
    if overlay.exists():
        raise FileExistsError(overlay)
    im = Image.open(cap['image']).convert('RGB')
    dr = ImageDraw.Draw(im)
    w,h = im.size
    for x in np.linspace(0,w,11):
        dr.line([(x,0),(x,h)], fill=(80,80,80), width=1)
    for y in np.linspace(0,h,11):
        dr.line([(0,y),(w,y)], fill=(80,80,80), width=1)
    for ref in refs:
        pts = [tuple(p) for p in ref['fitted_endpoints_px']]
        color = 'cyan' if ref['axis'] == 'vertical' else 'magenta'
        dr.line(pts, fill=color, width=3)
        cx,cy = np.mean(pts,axis=0)
        if ref['axis'] == 'vertical':
            dr.line([(cx,min(p[1] for p in pts)),(cx,max(p[1] for p in pts))], fill='white', width=1)
        else:
            dr.line([(min(p[0] for p in pts),cy),(max(p[0] for p in pts),cy)], fill='white', width=1)
        for x,y in np.asarray(ref['points'])*[w,h]:
            dr.ellipse((x-4,y-4,x+4,y+4),fill=color)
        dr.text((min(cx+8,w-220),cy),f"{ref['label']}: {ref['angle_deg_clockwise']:+.2f} deg",fill=color,
                stroke_fill='black',stroke_width=2)
    im.save(overlay,quality=93)
    result['overlay'] = str(overlay)
    if review is not None:
        required = ('reference_placement_checked','proportions_checked','borders_checked','composition_checked')
        if not all(review.get(k) is True for k in required) or not str(review.get('reason','')).strip():
            raise ValueError('Review requires checked reference placement, proportions, borders, composition and a reason')
        if review.get('decision') not in ('accept','revise','unverifiable'):
            raise ValueError('Review decision must be accept, revise or unverifiable')
        if review['decision'] == 'accept' and any(a['status']=='unresolved' for a in axes.values()):
            raise ValueError('Cannot accept unresolved axes: measure or document why unavailable')
        result['review'] = review
        result['status'] = 'reviewed_' + review['decision']
    result['audit'] = g.save(out,result)
    return result


def registration(source, target):
    """Normalized homography; reject insufficient, clustered or inconsistent matches."""
    from scipy.spatial import ConvexHull
    from skimage.feature import SIFT, match_descriptors
    from skimage.measure import ransac
    from skimage.transform import ProjectiveTransform
    def features(path):
        im = Image.open(path).convert('L')
        im.thumbnail((1000,1000))
        detector = SIFT()
        try:
            detector.detect_and_extract(np.asarray(im,dtype=float)/255)
        except RuntimeError as exc:
            raise ValueError('Insufficient visual features; annotate target manually') from exc
        return detector, np.asarray(im.size)
    a,asz = features(source)
    b,bsz = features(target)
    matches = match_descriptors(a.descriptors,b.descriptors,max_ratio=.65,cross_check=True)
    if len(matches) < 40:
        raise ValueError('Insufficient feature matches; annotate target manually')
    src = a.keypoints[matches[:,0]][:,::-1]
    dst = b.keypoints[matches[:,1]][:,::-1]
    model, inliers = ransac((src,dst),ProjectiveTransform,min_samples=4,residual_threshold=1.5,
                            max_trials=1000,rng=0)
    if model is None or inliers.sum()<40 or inliers.mean()<.45:
        raise ValueError('Unreliable registration; annotate target manually')
    apts,bpts = src[inliers]/asz,dst[inliers]/bsz
    coverage = [float(ConvexHull(p).volume) for p in (apts,bpts)]
    residuals = model.residuals(src[inliers],dst[inliers])
    if min(coverage)<.12 or np.percentile(residuals,90)>1.2:
        raise ValueError('Matches too localized or inaccurate; annotate target manually')
    matrix = np.diag([* (1/bsz),1]) @ model.params @ np.diag([*asz,1])
    if not np.isfinite(matrix).all() or np.linalg.cond(matrix)>1e4:
        raise ValueError('Degenerate registration')
    return matrix, apts, {'inliers':int(inliers.sum()),'match_count':len(matches),
        'coverage':coverage,'median_error_preview_px':float(np.median(residuals)),
        'p90_error_preview_px':float(np.percentile(residuals,90)), 'preview_sizes':[asz.tolist(),bsz.tolist()]}


def transfer(source_capture, target_capture, references, out):
    a,b = g.validate_capture(source_capture),g.validate_capture(target_capture)
    sa,sb = a['state'],b['state']
    if (sa['uuid'],sa['catalog']) != (sb['uuid'],sb['catalog']):
        raise ValueError('Reference transfer requires the same photo UUID and catalog')
    # Lens/profile/tonal/mask changes invalidate the calibrated projective model.
    ignored = g.GEOMETRY_KEYS | {'HasCrop','CropWidth','CropHeight','CropConstrainToWarp'}
    changed = [k for k in sa['settings'].keys()|sb['settings'].keys()
               if k not in ignored and not k.startswith('Upright') and
               not g.settings_equal(sa['settings'].get(k),sb['settings'].get(k))]
    if changed:
        raise ValueError(f'Nongeometry settings changed: {changed}; annotate manually')
    refs,unavailable = load_references(references,a['image_size'],a['image_sha256'])
    matrix, support, stats = registration(a['image'],b['image'])
    from scipy.spatial import Delaunay, cKDTree
    hull = Delaunay(support)
    tree = cKDTree(support)
    mapped = []
    for ref in refs:
        pts = np.asarray(ref['points'])
        if (hull.find_simplex(pts)<0).any() or (tree.query(pts)[0]>.12).any():
            raise ValueError(f"No local registration support for {ref['label']}; re-mark it manually")
        homogeneous = np.c_[pts,np.ones(len(pts))] @ matrix.T
        den = homogeneous[:,2]
        if np.min(np.abs(den))<1e-5 or (den>0).any() != (den>0).all():
            raise ValueError('Reference crosses a projective pole')
        points = homogeneous[:,:2]/den[:,None]
        if (points<0).any() or (points>1).any():
            raise ValueError(f"Reference {ref['label']} leaves the crop; choose a visible reference")
        # Carry only semantic annotations. Recompute metrics on destination pixels.
        mapped.append({k:ref[k] for k in ('label','axis','reason','target_angle','target_reason') if k in ref} |
                      {'points':points.tolist(), 'placement_error_px':max(2,stats['p90_error_preview_px']*
                       max(b['image_size'])/max(stats['preview_sizes'][1]))})
    result = {'image_size': b['image_size'], 'image_sha256':b['image_sha256'],
              'references':mapped,'unavailable':unavailable,'registration':{**stats,'matrix':matrix.tolist()},
              'placement_review_required':True,'source_capture':str(Path(source_capture).resolve())}
    result['path'] = g.save(out,result)
    return result


def read_audit(path):
    a = json.loads(Path(path).read_text())
    if a.get('kind') != 'geometry-audit' or g.digest(a['capture']) != a['capture_sha256']:
        raise ValueError('Stale or invalid calibration audit')
    cap = g.validate_capture(a['capture'])
    return a,cap


def bounded_step(jacobian, residual, controls, limits=None):
    j = np.asarray(jacobian,dtype=float)
    residual = np.asarray(residual,dtype=float)
    if not np.isfinite(j).all() or not np.isfinite(residual).all() or j.shape != (len(residual),len(controls)):
        raise ValueError('Invalid calibration matrix')
    limits = limits or [1 if c=='angle' else 15 for c in controls]
    scaled = j*np.asarray(limits)
    if np.linalg.matrix_rank(scaled)<len(controls) or np.linalg.cond(scaled)>50:
        raise ValueError('Calibration is rank deficient or poorly conditioned; add credible references')
    unit_step = np.linalg.lstsq(scaled,-residual,rcond=None)[0]
    factor = max(1,float(np.max(np.abs(unit_step))))
    delta = unit_step/factor*np.asarray(limits)
    if np.linalg.norm(residual+j@delta)>=np.linalg.norm(residual)-1e-6:
        raise ValueError('Calibration predicts no improvement')
    return delta,residual+j@delta


def suggest(base_audit, probe_audits, out):
    base,cap = read_audit(base_audit)
    labels = [r['label'] for r in base['references']]
    if len(labels)!=len(set(labels)) or not labels:
        raise ValueError('Unique named reference measurements required')
    controls, columns = [],[]
    current = g.display_controls(cap['state']['settings'])
    for path in probe_audits:
        probe,pcap = read_audit(path)
        if (pcap['state']['uuid'],pcap['state']['catalog']) != (cap['state']['uuid'],cap['state']['catalog']):
            raise ValueError('Calibration probes must target the same photo and catalog')
        ps,bs = pcap['state']['settings'],cap['state']['settings']
        allowed = {'CropAngle','PerspectiveVertical','PerspectiveHorizontal'}
        if any(not g.settings_equal(ps.get(k),bs.get(k)) for k in ps.keys()|bs.keys()
               if k not in allowed and not k.startswith('Upright')):
            raise ValueError('Probe changed more than a transform control; restart calibration')
        values = g.display_controls(ps)
        varied = [k for k in g.CONTROLS if abs(values[k]-current[k])>1e-5]
        if len(varied)!=1 or varied[0] in controls:
            raise ValueError('Each probe must vary exactly one distinct control from the SAME baseline')
        key = varied[0]
        step = values[key]-current[key]
        if not (.1 <= abs(step) <= (2 if key=='angle' else 15)):
            raise ValueError('Probe too small or too large for local calibration')
        refs = {r['label']:r for r in probe['references']}
        if set(refs)!=set(labels):
            raise ValueError('All probes need the same named references')
        if any(refs[r['label']]['axis']!=r['axis'] or refs[r['label']].get('target_angle',0)!=r.get('target_angle',0)
               for r in base['references']):
            raise ValueError('Probe reference semantics changed')
        columns.append([(refs[r['label']]['angle_deg_clockwise']-r['angle_deg_clockwise'])/step
                        for r in base['references']])
        controls.append(key)
    residual = np.asarray([r['residual_deg'] for r in base['references']])
    uncertainty = np.asarray([r['angle_uncertainty_deg'] for r in base['references']])
    if np.all(np.abs(residual)<=uncertainty):
        raise ValueError('Residual is within placement uncertainty; inspect visually instead of chasing decimals')
    j = np.asarray(columns).T
    delta,prediction = bounded_step(j,residual,controls)
    # Respect absolute control limits by scaling the entire coupled step.
    fraction = 1.
    for k,d in zip(controls,delta):
        bound = 45 if k=='angle' else 100
        if abs(d)>1e-10:
            fraction=min(fraction,((bound if d>0 else -bound)-current[k])/d)
    delta *= max(0,fraction)
    result = {'controls':{k:current[k]+float(d) for k,d in zip(controls,delta)},
              'delta':dict(zip(controls,delta.tolist())), 'jacobian':j.tolist(),
              'predicted_residual_deg':(residual+j@delta).tolist(), 'labels':labels,
              'status':'proposal_only_remeasure_after_apply', 'base_audit':base_audit,
              'probe_audits':probe_audits,'limits':{'angle':1,'vertical':15,'horizontal':15}}
    result['path'] = g.save(out,result)
    return result
