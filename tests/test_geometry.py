"""Geometry model, evidence guards and SDK mutation tests."""
import asyncio
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image
from lightroom_bridge import geometry as g, geometry_vision as v, cli, mcp_server
import test_bridge_upgrade
from lupa.lua51 import LuaError


class ModelTests(unittest.TestCase):
    def state(self, orientation='AB', angle=0):
        return dict(orientation=orientation,CropLeft=.15,CropTop=.2,CropRight=.8,CropBottom=.75,
                    CropAngle=angle,PerspectiveVertical=8,PerspectiveHorizontal=-12)

    def test_display_axis_roundtrip_every_rotation(self):
        for orientation in ('AB','BC','CD','DA'):
            for angle in (-4,0,2.5):
                s=self.state(orientation,angle)
                raw=g.raw_controls(s,g.display_controls(s))
                for k,x in raw.items():
                    self.assertAlmostEqual(x,s[k])
        self.assertEqual(g.display_controls(dict(orientation='DA',CropAngle=2.1,
            PerspectiveHorizontal=40,PerspectiveVertical=-9)),dict(angle=-2.1,vertical=-40,horizontal=-9))

    def test_rotated_trim_retains_pixel_ratio_all_axes_and_angles(self):
        sensor=(6000,4000)
        for orientation in ('AB','BC','CD','DA'):
            for angle in (-7,0,6):
                s=self.state(orientation,angle)
                original=g.predicted_dimensions(s,sensor)
                box=[.12,.08,.87,.93]
                after=g.predicted_dimensions({**s,**g.raw_trim(s,sensor,box)},sensor)
                self.assertAlmostEqual(after[0],original[0]*(box[2]-box[0]),places=7)
                self.assertAlmostEqual(after[1],original[1]*(box[3]-box[1]),places=7)
                full=g.raw_trim(s,sensor,[0,0,1,1])
                for k in g.EDGES:
                    self.assertAlmostEqual(full[k],s[k])

    def test_native_crop_model_round_trip(self):
        sensor=(6000,4000)
        s=dict(orientation='DA',CropLeft=.15,CropRight=.7,CropTop=.2,CropBottom=.85,
               CropAngle=2.1)
        w,h=g.predicted_dimensions(s,sensor)
        state={'settings':s,'sourceWidth':sensor[1],'sourceHeight':sensor[0],
               'croppedDimensions':{'width':round(w),'height':round(h)}}
        self.assertEqual(g.sensor_dimensions(state),sensor)
        state['croppedDimensions']['width']=round(w)//3
        with self.assertRaisesRegex(ValueError,'model'):
            g.sensor_dimensions(state)

    def test_ratio_fit_inscribes_and_obeys_anchor(self):
        box=g.fit_box([.1,.2,.9,.8],(6000,4000),.8,[0,1])
        self.assertAlmostEqual(box[0],.1)
        self.assertAlmostEqual((box[2]-box[0])*6000/((box[3]-box[1])*4000),.8)
        self.assertLessEqual(box[2],.9)

    def test_invalid_and_mirrored_fail_closed(self):
        for val in (float('nan'),float('inf'),True,1000):
            with self.assertRaises(ValueError):
                g.raw_controls(self.state(),{'angle':val})
        with self.assertRaisesRegex(ValueError,'mirrored'):
            g.display_controls(self.state('BA'))
        with self.assertRaises(ValueError):
            g.fit_box([.5,0,.4,1],(2000,3000))

    def test_bounded_solver_and_degenerate_rejection(self):
        j=np.array([[1,.12,0],[1,-.12,.02],[1,0,.10]])
        residual=np.array([6.,-2.,1.])
        delta,pred=v.bounded_step(j,residual,['angle','vertical','horizontal'])
        self.assertTrue(np.all(np.abs(delta)<=[1,15,15]))
        self.assertLess(np.linalg.norm(pred),np.linalg.norm(residual))
        for j in (np.zeros((3,2)),np.ones((3,2))):
            with self.assertRaises(ValueError):
                v.bounded_step(j,[1,2,3],['angle','vertical'])


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        im=self.root/'image.jpg';Image.new('RGB',(1200,800)).save(im)
        self.state={'uuid':'p','catalog':'cat','sourceWidth':1200,'sourceHeight':800,
                    'croppedDimensions':{'width':1200,'height':800},
                    'settings':dict(orientation='AB',CropAngle=0,CropLeft=0,CropRight=1,CropTop=0,CropBottom=1,
                                    PerspectiveVertical=0,PerspectiveHorizontal=0,Exposure2012=.5,MaskGroupBasedCorrections=[])}
        self.cap={'schema':1,'kind':'geometry-capture','state':self.state,
                  'image':str(im),'image_sha256':g.digest(im),'image_size':[1200,800]}
        self.cp=g.save(self.root/'capture.json',self.cap)

    def test_protected_feature_cannot_be_cut(self):
        with self.assertRaisesRegex(ValueError,'protected'):
            g.plan(self.cp,self.root/'plan.json',box=[.2,.2,.8,.8],reason='crop test',
                   protected=[{'label':'hand','point':[.1,.4]}])

    def test_mixed_transform_crop_and_changed_image_rejected(self):
        with self.assertRaisesRegex(ValueError,'between'):
            g.plan(self.cp,self.root/'plan.json',controls={'angle':1},box=[0,0,1,1],reason='test')
        Path(self.cap['image']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'changed'):
            g.validate_capture(self.cp)

    def test_nongeometry_collateral_and_render_failure_are_failures(self):
        p=g.plan(self.cp,self.root/'plan.json',controls={'angle':1},reason='test')
        with patch.object(g,'request',return_value={'readbackVerified':False}),patch.object(g,'capture') as capture:
            result=g.apply(p['plan'],self.root/'after')
        self.assertFalse(result['ok']);capture.assert_not_called()
        self.assertTrue(Path(result['result']['receipt']).exists())
        self.assertIn('snapshot',result['result'])
        with patch.object(g,'request',side_effect=RuntimeError('connection lost')) as request:
            result=g.apply(p['plan'],self.root/'after')
        self.assertEqual(request.call_count,1)
        self.assertFalse(result['ok'])

    def test_missing_axis_cannot_accept_and_uncertainty_is_nonzero(self):
        refs=self.root/'refs.json'
        g.save(refs,{'image_size':[1200,800],'image_sha256':self.cap['image_sha256'],
           'references':[{'label':'pole','reason':'synthetic straight centerline','axis':'vertical',
                          'points':[[.5,.1],[.5,.9]]}]})
        a=v.audit(self.cp,str(refs),self.root/'audit.json')
        self.assertEqual(a['axes']['horizontal']['status'],'unresolved')
        self.assertGreater(a['references'][0]['angle_uncertainty_deg'],0)
        review={k:True for k in ('reference_placement_checked','proportions_checked','borders_checked','composition_checked')}
        with self.assertRaisesRegex(ValueError,'unresolved'):
            v.audit(self.cp,str(refs),self.root/'review.json',review={**review,'decision':'accept','reason':'test'})

    def test_capture_watchdog_outlasted_by_transport_and_writes_not_replayed(self):
        from lightroom_bridge import _core
        self.assertEqual(_core._lr_timeout({'cmd':'geometry_capture','params':{'timeout':180}}),190)
        self.assertNotIn('geometry_apply',_core._RESEND_SAFE)
        self.assertNotIn('geometry_capture',_core._RESEND_SAFE)
        self.assertIn('geometry_state',_core._RESEND_SAFE)

    def test_apply_detects_collateral_and_accepts_lightroom_rounding(self):
        import copy
        p=g.plan(self.cp,self.root/'plan.json',box=[.1234567,.1,.9,.9],reason='test rounding')
        cap=copy.deepcopy(self.cap)
        cap['state']['settings'].update({k:round(x,6) for k,x in p['settings'].items()})
        cap['state']['croppedDimensions']=dict(zip(('width','height'),[round(x) for x in p['predicted_dimensions']]))
        after=self.root/'after.json';g.save(after,cap)
        with patch.object(g,'request',return_value={'readbackVerified':True}),patch.object(g.time,'sleep'), \
             patch.object(g,'capture',return_value={'capture':str(after)}):
            result=g.apply(p['plan'],self.root/'out')
        self.assertTrue(result['ok'],result)
        cap['state']['settings']['MaskGroupBasedCorrections']=[{'unexpected':'mask'}]
        after.write_text(json.dumps(cap))
        with patch.object(g,'request',return_value={'readbackVerified':True}),patch.object(g.time,'sleep'), \
             patch.object(g,'capture',return_value={'capture':str(after)}):
            result=g.apply(p['plan'],self.root/'out')
        self.assertFalse(result['ok'])
        self.assertIn('MaskGroupBasedCorrections',result['result']['nongeometry_changes'])

    def test_registration_recovers_known_projective_warp(self):
        from PIL import ImageDraw
        from skimage.transform import ProjectiveTransform, warp
        rng=np.random.default_rng(19)
        source=Image.new('L',(480,360),128);draw=ImageDraw.Draw(source)
        for _ in range(160):
            x,y=rng.integers([8,8],[440,320]);w,h=rng.integers(5,35,size=2)
            draw.rectangle((int(x),int(y),int(x+w),int(y+h)),fill=int(rng.integers(0,256)))
        source.save(self.root/'source.png')
        transform=ProjectiveTransform(matrix=np.array([[.95,.02,8],[-.01,.97,8],[.00002,-.00001,1]]))
        target=warp(np.asarray(source),transform.inverse,output_shape=(360,480),preserve_range=True)
        Image.fromarray(target.astype('uint8')).save(self.root/'target.png')
        matrix,support,stats=v.registration(self.root/'source.png',self.root/'target.png')
        points=np.array([[.2,.2,1],[.8,.2,1],[.8,.8,1],[.2,.8,1]])
        mapped=points@matrix.T;mapped=mapped[:,:2]/mapped[:,2,None]
        expected=transform(points[:,:2]*[480,360])/[480,360]
        self.assertLess(np.max(np.abs(mapped-expected)),.004)
        self.assertGreater(stats['inliers'],40)

    def test_cli_and_mcp_expose_same_workflow(self):
        args=cli.build_parser().parse_args(['geometry','plan','--json',json.dumps({'capture_path':self.cp,
                'out':str(self.root/'plan.json'),'reason':'test','controls':{'angle':1}})])
        self.assertTrue(cli.dispatch_cli(args)['ok'])
        names=asyncio.run(mcp_server.mcp.list_tools())
        names={x.name for x in names}
        self.assertTrue({'geometry_capture','geometry_plan','geometry_apply','geometry_audit','geometry_transfer','geometry_suggest'}<=names)

    def test_transfer_rejects_foreign_uuid_or_changed_masks_before_matching(self):
        import copy
        for label,change in [('foreign',{'uuid':'other'}),('mask',{'settings':{**self.state['settings'],'MaskGroupBasedCorrections':[{'ID':'changed'}]}})]:
            target=copy.deepcopy(self.cap);target['state'].update(change)
            path=g.save(self.root/(label+'.json'),target)
            with patch.object(v,'registration') as match:
                with self.assertRaises(ValueError):
                    v.transfer(self.cp,path,'unused.json',str(self.root/'transfer.json'))
                match.assert_not_called()

    def test_registration_blank_and_unrelated_fail_closed(self):
        with self.assertRaises(ValueError):
            v.registration(self.cap['image'],self.cap['image'])


class LuaGeometryTests(unittest.TestCase):
    call = test_bridge_upgrade.CoreHandlerTests.call
    def setUp(self):
        test_bridge_upgrade.CoreHandlerTests.setUp(self)
        self.lua.execute('''
        state={orientation='DA',CropLeft=.1,CropRight=.9,CropTop=.1,CropBottom=.9,CropAngle=0,
            Exposure2012=.5,MaskGroupBasedCorrections={{ID='mask'}}}
        function cat:getPath() return 'catalog' end
        function cat:findPhotoByUuid(id) if id=='photo-1' then return photo end end
        function photo:getDevelopSettings() return state end
        function photo:createDevelopSnapshot(name,overwrite)
            calls.snapshot=name; calls.overwrite=overwrite; return not collision
        end
        function photo:applyDevelopSettings(settings)
            if not calls.snapshot then error('snapshot missing') end
            writes=writes+1
            if not noApply then for k,v in pairs(settings) do state[k]=v end end
        end
        ''')
        self.expected=dict(orientation='DA',CropLeft=.1,CropRight=.9,CropTop=.1,CropBottom=.9,CropAngle=0,
                           Exposure2012=.5,MaskGroupBasedCorrections=[{'ID':'mask'}])

    def geom(self,**changes):
        params=dict(uuid='photo-1',catalog='catalog',expected=self.expected,settings={'CropAngle':1},snapshot='rollback')
        params.update(changes)
        return self.call('geometry_apply',**params)

    def test_stale_state_and_wrong_catalog_never_write(self):
        for changes in ({'catalog':'wrong'},{'uuid':'wrong'},{'expected':{**self.expected,'Exposure2012':.6}}):
            with self.assertRaises(LuaError): self.geom(**changes)
        self.assertEqual(self.g.writes,0)
        self.assertIsNone(self.g.calls.snapshot)

    def test_catalog_switch_while_waiting_for_write_gate_never_writes(self):
        self.lua.execute("""
        function cat:withWriteAccessDo(name,fn,options)
            function cat:getPath() return 'switched-catalog' end
            fn();return 'executed'
        end
        """)
        with self.assertRaisesRegex(LuaError,'catalog changed before write'):
            self.geom()
        self.assertEqual(self.g.writes,0)
        self.assertIsNone(self.g.calls.snapshot)

    def test_snapshot_precedes_uuid_bound_write_preserves_masks(self):
        self.g.noSelection=True
        result=self.geom()
        self.assertTrue(result.readbackVerified)
        self.assertEqual(self.g.state.Exposure2012,.5)
        self.assertEqual(self.g.state.MaskGroupBasedCorrections[1].ID,'mask')
        self.assertFalse(self.g.calls.overwrite)

    def test_bad_key_bounds_nan_collision_no_write(self):
        for settings in ({'Exposure2012':1},{'CropAngle':float('nan')},{'CropLeft':.99},{'CropAngle':99}):
            with self.assertRaises(LuaError): self.geom(settings=settings)
        self.g.collision=True
        with self.assertRaisesRegex(LuaError,'snapshot name'): self.geom()
        self.assertEqual(self.g.writes,0)

    def test_capture_rejects_settings_changed_during_export(self):
        self.g.handlers=self.h
        self.lua.execute("""
        function clone(x)
            if type(x)~='table' then return x end
            local out={};for k,v in pairs(x) do out[k]=clone(v) end;return out
        end
        function photo:getDevelopSettings() return clone(state) end
        function handlers.render(p) state.CropAngle=9;return {path='/unused'} end
        """)
        with self.assertRaisesRegex(LuaError,'changed during export'):
            self.call('geometry_capture',uuid='photo-1')

    def test_noop_not_verified_or_retried(self):
        self.g.noApply=True
        result=self.geom()
        self.assertFalse(result.readbackVerified)
        self.assertEqual(self.g.writes,1)


if __name__=='__main__':
    unittest.main()
