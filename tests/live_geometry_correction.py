"""Opt-in closed-loop calibration test on a verified virtual copy with masks.
Deliberately adds 1 degree, probes +0.5, solves, applies and measures the result.
Always restores the copy's original snapshot. Seed references must describe the
copy's initial rendered composition and are checked by registration/overlays.
"""
import json
from pathlib import Path
import sys
import time
import numpy as np
from lightroom_bridge import geometry as g, geometry_vision as v
uuid,out,seed_image,seed_refs=sys.argv[1:]
root=Path(out).resolve();root.mkdir(parents=True,exist_ok=False)
initial=g.request('geometry_state',{'uuid':uuid})
if not initial.get('isVirtualCopy'): raise SystemExit('Virtual copy required')
g.save(root/'initial.json',initial)
snapshot='Geometry closed loop '+root.name
g.request('snapshot_create',{'uuid':uuid,'name':snapshot})
try:
    start=g.capture(uuid,str(root/'start'),size=2560)
    cap=g.validate_capture(start['capture'])
    seed=json.loads(Path(seed_refs).read_text())
    if g.digest(seed_image)!=seed['image_sha256']: raise RuntimeError('Stale seed references')
    matrix,support,stats=v.registration(seed_image,start['image'])
    # The copy's initial state must have exactly the known composition, no remap guess.
    probes=np.array([[.1,.1,1],[.9,.1,1],[.9,.9,1],[.1,.9,1]])
    mapped=probes@matrix.T;mapped=mapped[:,:2]/mapped[:,2,None]
    if np.max(np.abs(mapped-probes[:,:2]))>.002:
        raise RuntimeError('Seed image does not match test copy; annotate manually')
    refs={**seed,'image_sha256':cap['image_sha256'],'image_size':cap['image_size']}
    ref_path=g.save(root/'start-refs.json',refs)
    current=cap['display_controls']['angle']
    p=g.plan(start['capture'],root/'defect-plan.json',controls={'angle':current+1},reason='Virtual copy regression: deliberately introduce one degree of roll')
    defect=g.apply(p['plan'],str(root/'defect'))
    if not defect['ok']: raise RuntimeError(defect['result'].get('error'))
    dc=defect['result']['after']['capture']
    dr=v.transfer(start['capture'],dc,ref_path,str(root/'defect-refs.json'))
    da=v.audit(dc,dr['path'],str(root/'defect-audit.json'))
    pp=g.plan(dc,root/'probe-plan.json',controls={'angle':current+1.5},reason='Single-control half-degree response calibration on test copy')
    probe=g.apply(pp['plan'],str(root/'probe'))
    if not probe['ok']: raise RuntimeError(probe['result'].get('error'))
    pc=probe['result']['after']['capture']
    pr=v.transfer(dc,pc,dr['path'],str(root/'probe-refs.json'))
    pa=v.audit(pc,pr['path'],str(root/'probe-audit.json'))
    proposal=v.suggest(da['audit'],[pa['audit']],str(root/'proposal.json'))
    g.request('snapshot_apply',{'uuid':uuid,'name':probe['result']['snapshot']})
    time.sleep(3)
    restored=g.capture(uuid,str(root/'restored-defect'),size=2560)
    plan=g.plan(restored['capture'],root/'correction-plan.json',controls=proposal['controls'],reason='Apply measured bounded correction to the deliberately tilted test copy')
    correction=g.apply(plan['plan'],str(root/'corrected'))
    if not correction['ok']: raise RuntimeError(correction['result'].get('error'))
    cc=correction['result']['after']['capture']
    cr=v.transfer(dc,cc,dr['path'],str(root/'corrected-refs.json'))
    ca=v.audit(cc,cr['path'],str(root/'corrected-audit.json'))
    before=[r['residual_deg'] for r in da['references']]
    after=[r['residual_deg'] for r in ca['references']]
    passed=np.linalg.norm(after)<.15*np.linalg.norm(before)
    result={'passed':bool(passed),'defect_degrees':before,'corrected_degrees':after,
            'proposal':proposal,'guides':[da['overlay'],ca['overlay']],
            'note':'Numeric tool regression, not approval of the photographic edit'}
    g.save(root/'result.json',result)
    print(json.dumps(result),flush=True)
    if not passed: raise RuntimeError('Closed-loop correction did not sufficiently reduce the known tilt')
finally:
    g.request('snapshot_apply',{'uuid':uuid,'name':snapshot})
    actual=g.request('geometry_state',{'uuid':uuid})
    changed=[k for k in initial['settings'].keys()|actual['settings'].keys()
             if not g.settings_equal(initial['settings'].get(k),actual['settings'].get(k))]
    g.save(root/'restored.json',{'state':actual,'changed_keys':changed})
    print(json.dumps({'restored_copy_changed_keys':changed}),flush=True)
