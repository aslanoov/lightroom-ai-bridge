"""Opt-in integration test. ONLY runs against an explicitly named virtual copy.
Usage: python tests/live_geometry_validation.py VIRTUAL_COPY_UUID OUTPUT_DIR
It never selects or edits any other photo. Requires copy selected for SDK readback.
"""
import json
from pathlib import Path
import sys
import time
import numpy as np
from PIL import Image
from skimage.metrics import structural_similarity
from lightroom_bridge import geometry as g

uuid,out=sys.argv[1:3]
without_masks='--without-masks' in sys.argv[3:]
root=Path(out).resolve();root.mkdir(parents=True,exist_ok=False)
# Verify copy status before any write (metadata endpoint includes isVirtualCopy).
meta=g.request('geometry_state',{'uuid':uuid})
if not meta.get('isVirtualCopy'):
    raise SystemExit('Refusing: target is not an explicitly verified virtual copy')
initial=g.request('geometry_state',{'uuid':uuid})
g.save(root/'initial.json',initial)
name='Geometry test baseline '+root.name
g.request('snapshot_create',{'uuid':uuid,'name':name})
records=[]
try:
    if without_masks:
        active=g.request('status',{})['active']['uuid']
        if active != uuid:
            raise RuntimeError('Select the test virtual copy before mask isolation')
        g.request('mask_reset',{})
        time.sleep(3)
    for i in range(4):
        if i:
            g.request('rotate',{'uuid':uuid,'dir':'right'})
            time.sleep(3)
        baseline=g.capture(uuid,str(root/f'orientation-{i}'),size=1600)
        state=g.validate_capture(baseline['capture'])['state']
        code=state['settings']['orientation']
        # Each rotation has its own rollback; snapshot orientation semantics are not assumed.
        snap=name+' '+code
        g.request('snapshot_create',{'uuid':uuid,'name':snap})
        for sign in (1,-1):
            if sign==-1:
                g.request('snapshot_apply',{'uuid':uuid,'name':snap})
                time.sleep(3)
                baseline=g.capture(uuid,str(root/f'{code}-restored'),size=1600)
            stem=f'{code}-{sign}'
            controls={'angle':sign*1.5,'vertical':8,'horizontal':-6}
            p=g.plan(baseline['capture'],root/(stem+'-transform.json'),controls=controls,
                     reason='Virtual copy integration calibration of display axes and angle signs')
            result=g.apply(p['plan'],str(root/(stem+'-transform')))
            if not result['ok']: raise RuntimeError({k:result['result'].get(k) for k in ('error','receipt')})
            after=result['result']['after']
            sdk={k:g.request('get_value',{'param':param}) for k,param in
                 [('angle','straightenAngle'),('vertical','PerspectiveVertical'),('horizontal','PerspectiveHorizontal')]}
            values={k:x['value'] for k,x in sdk.items()}
            if any(abs(values[k]-controls[k])>.01 for k in values):
                raise RuntimeError(f'SDK display mapping mismatch: {code} {controls} vs {values}')
            ratio=.8 if code in ('DA','BC') else 1.25
            p2=g.plan(after['capture'],root/(stem+'-crop.json'),box=[.08,.11,.84,.89],ratio=ratio,
                      anchor=[.25,.7],reason='Virtual copy off-centre crop regression')
            cropped=g.apply(p2['plan'],str(root/(stem+'-crop')))
            if not cropped['ok']: raise RuntimeError({k:cropped['result'].get(k) for k in ('error','receipt')})
            im=Image.open(p2['preview']).convert('L');im.thumbnail((900,900))
            actual=Image.open(cropped['result']['after']['image']).convert('L').resize(im.size,Image.Resampling.LANCZOS)
            ssim=float(structural_similarity(np.asarray(im),np.asarray(actual),data_range=255))
            record={'orientation':code,'angle':controls['angle'],'sdk':values,'crop_ssim':ssim,
                    'native_dimensions':g.validate_capture(cropped['result']['after']['capture'])['state']['croppedDimensions'],
                    'transform_receipt':result['result']['receipt'],'crop_receipt':cropped['result']['receipt']}
            records.append(record)
            (root/'results.json').write_text(json.dumps(records,indent=2)+'\n')
            print(json.dumps(record),flush=True)
            if ssim<.94: raise RuntimeError('Rendered crop does not match display-space crop preview')
        g.request('snapshot_apply',{'uuid':uuid,'name':snap})
        time.sleep(3)
finally:
    # Restore the copy's initial edit. Snapshot restores develop settings; verify orientation separately.
    g.request('snapshot_apply',{'uuid':uuid,'name':name})
    state=g.request('geometry_state',{'uuid':uuid})
    for _ in range(4):
        if state['settings']['orientation']==initial['settings']['orientation']: break
        g.request('rotate',{'uuid':uuid,'dir':'right'})
        state=g.request('geometry_state',{'uuid':uuid})
    g.save(root/'restored.json',state)
    changed=[k for k in initial['settings'].keys()|state['settings'].keys()
             if not g.settings_equal(initial['settings'].get(k),state['settings'].get(k))]
    print(json.dumps({'restored_copy_changed_keys':changed}),flush=True)
