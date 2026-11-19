"""Evidence-bound geometry plans, display-space crops and guarded Lightroom writes.

The four rotation orientations are supported. Mirrored orientations fail closed.
Image analysis dependencies are lazy: ordinary bridge commands stay stdlib-only.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import shutil
import time
import uuid as uuidlib

from . import _core

SCHEMA = 1
EDGES = ('CropLeft', 'CropTop', 'CropRight', 'CropBottom')
CONTROLS = ('angle', 'vertical', 'horizontal')
GEOMETRY_KEYS = set(EDGES) | {'CropAngle', 'PerspectiveVertical', 'PerspectiveHorizontal'}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, data):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as f:
        json.dump(data, f, indent=2, allow_nan=False)
        f.write('\n')
    return str(path)


def number(value, name, low=-math.inf, high=math.inf):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError(f'{name} must be a finite number')
    if not low <= value <= high:
        raise ValueError(f'{name} must be in [{low}, {high}]')
    return float(value)


def orientation(settings):
    code = settings.get('orientation')
    if code not in ('AB', 'BC', 'CD', 'DA'):
        raise ValueError(f'Unsupported/mirrored orientation {code!r}; use Lightroom UI and render verification')
    return code


def display_controls(settings):
    code = orientation(settings)
    v, h = settings.get('PerspectiveVertical', 0), settings.get('PerspectiveHorizontal', 0)
    dv, dh = {'AB': (v, h), 'BC': (h, -v), 'CD': (-v, -h), 'DA': (-h, v)}[code]
    return {'angle': -settings.get('CropAngle', 0), 'vertical': dv, 'horizontal': dh}


def raw_controls(settings, controls):
    if not controls or set(controls) - set(CONTROLS):
        raise ValueError('controls require angle, vertical and/or horizontal absolute display values')
    current = display_controls(settings)
    for key, value in controls.items():
        current[key] = number(value, key, -45 if key == 'angle' else -100, 45 if key == 'angle' else 100)
    v, h = current['vertical'], current['horizontal']
    rv, rh = {'AB': (v, h), 'BC': (-h, v), 'CD': (-v, -h), 'DA': (h, -v)}[orientation(settings)]
    out = {}
    if 'angle' in controls:
        out['CropAngle'] = -current['angle']
    if {'vertical', 'horizontal'} & set(controls):
        out.update(PerspectiveVertical=rv, PerspectiveHorizontal=rh)
    return out


def crop_frame(settings, sensor):
    """Opposite rotated corners in sensor pixels, and rectified side lengths."""
    w, h = sensor
    l, t, r, b = (settings.get(k, d) for k, d in zip(EDGES, (0, 0, 1, 1)))
    theta = math.radians(settings.get('CropAngle', 0))
    c, s = math.cos(theta), math.sin(theta)
    dx, dy = (r-l)*w, (b-t)*h
    u, v = dx*c+dy*s, dy*c-dx*s
    if min(u, v) <= 1:
        raise ValueError('Crop model has nonpositive side length')
    return (l*w, t*h), (u, v), (c, s)


def predicted_dimensions(settings, sensor):
    _, (u, v), _ = crop_frame(settings, sensor)
    return (v, u) if orientation(settings) in ('BC', 'DA') else (u, v)


def sensor_dimensions(state):
    """Resolve metadata's orientation convention by matching actual crop pixels."""
    w, h = state['sourceWidth'], state['sourceHeight']
    actual = state['croppedDimensions']
    aw, ah = actual['width'], actual['height']
    matches = []
    for candidate in dict.fromkeys(((w, h), (h, w))):
        try:
            pw, ph = predicted_dimensions(state['settings'], candidate)
        except ValueError:
            continue
        if max(abs(pw-aw), abs(ph-ah)) <= 3:
            matches.append(candidate)
    if len(matches) != 1:
        raise ValueError('Crop model does not uniquely match Lightroom pixel dimensions; use UI crop and recapture')
    return matches[0]


def validate_capture(path):
    cap = json.loads(Path(path).read_text())
    if cap.get('schema') != SCHEMA or cap.get('kind') != 'geometry-capture':
        raise ValueError('Expected a geometry capture')
    if digest(cap['image']) != cap['image_sha256']:
        raise ValueError('Capture image changed; recapture before using coordinates')
    from PIL import Image
    with Image.open(cap['image']) as image:
        if list(image.size) != cap['image_size']:
            raise ValueError('Capture image dimensions changed')
    return cap


def request(command, params):
    result = _core.call(command, params)
    if not result.get('ok'):
        raise RuntimeError(result.get('error', f'{command} failed'))
    return result['result']


def capture(uuid, out_dir, size=2560):
    """State and unique render are collected within one serialized plug-in call."""
    from PIL import Image
    if not uuid:
        raise ValueError('Explicit photo uuid required')
    number(size, 'size', 0, 10000)
    dest = Path(out_dir).resolve()
    dest.mkdir(parents=True, exist_ok=False)
    result = request('geometry_capture', {'uuid': uuid, 'size': size, 'timeout': 90})
    target = dest / 'render.jpg'
    shutil.copyfile(result['image'], target)
    with Image.open(target) as image:
        size_px = list(image.size)
    state = result['state']
    dims = state['croppedDimensions']
    # JPEG resizing rounds to pixels. More than two pixels of aspect error is a stale/mismatched render.
    if abs(size_px[0] - size_px[1]*dims['width']/dims['height']) > 2:
        raise ValueError('Rendered aspect does not match Lightroom crop metadata; recapture after settling')
    cap = {'schema': SCHEMA, 'kind': 'geometry-capture', 'state': state,
           'image': str(target), 'image_sha256': digest(target), 'image_size': size_px,
           'created': time.time(), 'review_status': 'unreviewed'}
    try:
        cap['display_controls'] = display_controls(state['settings'])
        cap['sensor_dimensions'] = list(sensor_dimensions(state))
    except ValueError as exc:
        cap['model_warning'] = str(exc)
    path = save(dest / 'capture.json', cap)
    save(dest / 'references-template.json', {'image_size': size_px, 'image_sha256': cap['image_sha256'],
         'references': [], 'unavailable': {}})
    return {'capture': path, 'image': str(target), 'display_controls': cap.get('display_controls'),
            'model_warning': cap.get('model_warning'), 'review_status': 'unreviewed'}


def fit_box(box, size, ratio=None, anchor=(0.5, 0.5)):
    if len(box) != 4 or len(anchor) != 2:
        raise ValueError('box requires [left,top,right,bottom]; anchor requires [x,y]')
    l, t, r, b = [number(x, 'crop edge', 0, 1) for x in box]
    ax, ay = [number(x, 'anchor', 0, 1) for x in anchor]
    if l >= r or t >= b:
        raise ValueError('Crop requires left<right and top<bottom')
    if ratio is not None:
        ratio = number(ratio, 'ratio', 0.1, 10)
        current = (r-l)*size[0]/((b-t)*size[1])
        if current > ratio:
            width = (b-t)*size[1]*ratio/size[0]
            l += ((r-l)-width)*ax
            r = l+width
        else:
            height = (r-l)*size[0]/ratio/size[1]
            t += ((b-t)-height)*ay
            b = t+height
    return [l, t, r, b]


def raw_trim(settings, sensor, box):
    """Trim the CURRENT RENDER, preserving its existing angle/transform."""
    code = orientation(settings)
    l, t, r, b = box
    ul, vt, ur, vb = {'AB': (l,t,r,b), 'BC': (t,1-r,b,1-l),
                      'CD': (1-r,1-b,1-l,1-t), 'DA': (1-b,l,1-t,r)}[code]
    (x, y), (u, v), (c, s) = crop_frame(settings, sensor)
    w, h = sensor
    out = dict(zip(EDGES, ((x+c*u*ul-s*v*vt)/w, (y+s*u*ul+c*v*vt)/h,
                           (x+c*u*ur-s*v*vb)/w, (y+s*u*ur+c*v*vb)/h)))
    for k, val in out.items():
        number(val, k, 0, 1)
    if out['CropLeft'] >= out['CropRight'] or out['CropTop'] >= out['CropBottom']:
        raise ValueError('Rotated trim cannot be represented safely; use Lightroom UI')
    return out


def plan(capture_path, out, controls=None, box=None, ratio=None, anchor=(.5,.5), protected=None, reason=''):
    if not str(reason).strip():
        raise ValueError('Name the visible problem and intended improvement in reason')
    cap = validate_capture(capture_path)
    settings = cap['state']['settings']
    crop_requested = box is not None or ratio is not None
    if bool(controls) == crop_requested:
        raise ValueError('Choose transform controls OR a current-render crop; render between these stages')
    if settings.get('PerspectiveUpright', 0) != 0:
        raise ValueError('Automated/Guided Upright is active; use Lightroom UI or deliberately turn it off and recapture')
    if any(settings.get(k, default) != default for k, default in
           [('PerspectiveRotate',0),('PerspectiveAspect',0),('PerspectiveScale',100),('PerspectiveX',0),('PerspectiveY',0)]):
        raise ValueError('Additional transform controls are active; current crop model is not validated for them')
    sensor = sensor_dimensions(cap['state'])
    raw = raw_controls(settings, controls) if controls else {}
    result = {'schema': SCHEMA, 'kind': 'geometry-plan', 'capture': str(Path(capture_path).resolve()),
              'capture_sha256': digest(capture_path), 'reason': reason, 'uuid': cap['state']['uuid'],
              'catalog': cap['state']['catalog'], 'requires_visual_review': True}
    if crop_requested:
        # Use native dimensions, avoiding resized JPEG rounding in the ratio calculation.
        box = fit_box(box or [0,0,1,1], predicted_dimensions(settings, sensor), ratio, anchor)
        for feature in protected or []:
            if not feature.get('label') or len(feature.get('point', [])) != 2:
                raise ValueError('Protected features need label and current-render normalized point')
            x, y = [number(v, 'protected point', 0, 1) for v in feature['point']]
            margin = number(feature.get('margin', .02), 'protected margin', 0, .49)
            if not (box[0]+margin*(box[2]-box[0]) <= x <= box[2]-margin*(box[2]-box[0]) and
                    box[1]+margin*(box[3]-box[1]) <= y <= box[3]-margin*(box[3]-box[1])):
                raise ValueError(f"Crop cuts or crowds protected feature: {feature['label']}")
        raw = raw_trim(settings, sensor, box)
        result.update(display_box=box, protected=protected or [], ratio=ratio)
        from PIL import Image
        im = Image.open(cap['image'])
        preview = Path(out).resolve().with_suffix('.preview.jpg')
        if preview.exists():
            raise FileExistsError(preview)
        im.crop(tuple(round(v*n) for v,n in zip(box, im.size*2))).save(preview, quality=94)
        result['preview'] = str(preview)
    next_settings = {**settings, **raw}
    dims = predicted_dimensions(next_settings, sensor)
    result.update(settings=raw, display_controls=display_controls(next_settings),
                  predicted_dimensions=list(dims), predicted_ratio=dims[0]/dims[1])
    result['plan'] = str(Path(out).resolve())
    save(out, result)
    return result


def settings_equal(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(settings_equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(settings_equal(x,y) for x,y in zip(a,b))
    if type(a) in (int,float) and type(b) in (int,float):
        return abs(a-b) < 1e-7
    return type(a) == type(b) and a == b


def apply(plan_path, out_dir):
    p = json.loads(Path(plan_path).read_text())
    if p.get('schema') != SCHEMA or p.get('kind') != 'geometry-plan':
        raise ValueError('Expected a geometry plan')
    if digest(p['capture']) != p['capture_sha256']:
        raise ValueError('Capture record changed; re-plan')
    cap = validate_capture(p['capture'])
    state = cap['state']
    if (p['uuid'], p['catalog']) != (state['uuid'], state['catalog']):
        raise ValueError('Plan target does not match capture')
    if Path(out_dir).exists():
        raise FileExistsError('Use a fresh output directory before applying')
    snapshot = 'Geometry rollback ' + uuidlib.uuid4().hex
    record = {'uuid': p['uuid'], 'snapshot': snapshot, 'plan': str(Path(plan_path).resolve()),
              'status': 'write_outcome_unknown', 'level_certified': False}
    # Persist recovery info BEFORE the mutation, including transport failures.
    receipt = Path(plan_path).resolve().parent / (snapshot.replace(' ', '-') + '.json')
    save(receipt, record)
    try:
        response = request('geometry_apply', {'uuid': p['uuid'], 'catalog': p['catalog'],
             'expected': state['settings'], 'settings': p['settings'], 'snapshot': snapshot})
        record['write'] = response
        if not response['readbackVerified']:
            raise RuntimeError('Geometry readback failed; inspect candidate and rollback snapshot')
        # Serialize a fresh native export, after Lightroom has had time to settle.
        time.sleep(3)
        after_result = capture(p['uuid'], out_dir, size=0)
        after = validate_capture(after_result['capture'])
        record['after'] = after_result
        before_settings, after_settings = state['settings'], after['state']['settings']
        if after['state']['catalog'] != p['catalog'] or any(
                not isinstance(after_settings.get(k), (int,float)) or abs(after_settings[k]-value)>1e-5
                for k,value in p['settings'].items()):
            raise RuntimeError('Candidate changed before final capture; inspect current state before retrying')
        # Derived crop/Upright cache keys can change when geometry is recalculated.
        ignore = GEOMETRY_KEYS | {'HasCrop', 'CropConstrainToWarp', 'CropWidth', 'CropHeight'}
        changed = [k for k in before_settings.keys() | after_settings.keys()
                   if not settings_equal(before_settings.get(k), after_settings.get(k))]
        collateral = [k for k in changed if k not in ignore and not k.startswith('Upright')]
        record.update(changed_keys=changed, nongeometry_changes=collateral)
        if collateral:
            raise RuntimeError('Unexpected nongeometry change; inspect receipt and rollback snapshot')
        actual = after['state']['croppedDimensions']
        expected = p['predicted_dimensions']
        if max(abs(actual['width']-expected[0]), abs(actual['height']-expected[1])) > 3:
            raise RuntimeError('Crop model disagrees with native output; candidate is NOT verified')
        record['status'] = 'rendered_visual_review_required'
    except Exception as exc:
        record.update(error=str(exc), status='failed_or_unknown_inspect_before_retry')
    receipt.write_text(json.dumps(record, indent=2, allow_nan=False)+'\n')
    return {'ok': 'error' not in record, 'result': {**record, 'receipt': str(receipt)}}


def run(operation, **kwargs):
    """Consistent CLI/MCP envelope, optional dependencies with actionable errors."""
    try:
        if operation in ('audit', 'transfer', 'suggest'):
            from . import geometry_vision
            result = getattr(geometry_vision, operation)(**kwargs)
        else:
            result = {'capture': capture, 'plan': plan, 'apply': apply}[operation](**kwargs)
        return result if operation == 'apply' else {'ok': True, 'result': result}
    except ImportError as exc:
        return {'ok': False, 'error': f'Geometry dependencies missing ({exc}); install lightroom-bridge[geometry] in this runtime'}
    except Exception as exc:
        return {'ok': False, 'error': str(exc)}
