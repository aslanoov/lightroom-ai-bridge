"""Regression checks with Lua 5.1 and SDK doubles; no Lightroom/catalog writes.

Run: .venv/bin/python -m unittest discover -s tests -v
Install test dependencies: uv pip install -e '.[test]'
"""
import asyncio
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from lupa.lua51 import LuaRuntime, LuaError
from lightroom_bridge import cli, _core, mcp_server

ROOT = Path(__file__).resolve().parents[1]

FIXTURE = r'''
now = 0
calls = {}
swatches = {{SrcHue=2, SrcSat=0.6, SrcLum=0.4, HueShift=0.1, Variance=0,
    HueRange={LowerNone=0,LowerFull=0.2,UpperFull=0.8,UpperNone=1}}}
selectedIndex = 1
selectedMask = 'mask-1'
masks = {{ID='mask-1',Tools={{ID='component-1'}}}}
reflection = {checkboxState=false, amount=100, quality='standard',enabled=true,isSupported=true}
enhanceState = {denoiseState=false,denoiseEnabled=true,rawDetailsState=false,
    rawDetailsEnabled=true,superResState=false,superResEnabled=true}
D = {}
function D.getValue(k) return swatches end
function D.getSelectedPointColorSwatchIndex(localScope) return selectedIndex end
function D.selectPointColorSwatch(i, localScope) selectedIndex=i end
function D.updateSelectedPointColorSwatch(s, localScope)
    calls.updated = (calls.updated or 0)+1; swatches[selectedIndex]=s; return true
end
function D.addPointColorSwatch(s, localScope) swatches[#swatches+1]=s; selectedIndex=#swatches; return true end
function D.deletePointColorSwatch(all, i, localScope)
    if all then swatches={} else table.remove(swatches,i) end; return true
end
function D.getSelectedMask() return selectedMask end
function D.selectMask(id) calls.maskSelect=(calls.maskSelect or 0)+1; selectedMask=id end
selectedTool = 'masking'
function D.getSelectedTool() return selectedTool end
function D.selectTool(tool) selectedTool=tool end
function D.duplicateAndInvertMask(id) calls.duplicate=id; return true end
function D.toggleHideMask(id) calls.toggle=(calls.toggle or 0)+1 end
function D.selectMaskTool(id) calls.component=id end
function D.getEnhancePanelState() return enhanceState end
function D.setEnhance(feature,enabled,amount) calls.enhance=feature; enhanceState[feature..'State']=enabled end
function D.changeDenoiseAmount(amount) calls.denoise=amount end
function D.getReflectionRemovalPanelState() return reflection end
function D.toggleReflectionRemoval(amount,quality,callback)
    calls.reflect=(calls.reflect or 0)+1; reflection.checkboxState=not reflection.checkboxState
    reflection.amount=amount; reflection.quality=quality
    if completeCallback then callback() end
end
function D.changeReflectionRemovalAmount(amount) reflection.amount=amount end
function D.changeReflectionRemovalQuality(quality,callback)
    reflection.quality=quality; if completeCallback then callback() end
end
function D.hasFilterWithName(name) return false end
cat = {}
function cat:updateAISettings(photos) calls.update=#photos end
function cat:deleteAllEmptyMasks(photos) calls.empty=#photos end
ctx = {
    ensureDevelop=function() calls.develop=(calls.develop or 0)+1 end,
    ensureMaskingTool=function() end,
    getMasks=function() return masks end,
    catalog=function() return cat end,
    targetPhotos=function(scope) if scope~='active' and scope~='selected' then error('scope') end; return {{}} end,
    withWrite=function(name,f) return f() end,
    version='1.3.0',
}
function import(name)
    if name=='LrDevelopController' then return D end
    if name=='LrTasks' then return {pcall=pcall,sleep=function(n) now=now+n end} end
    if name=='LrDate' then return {currentTime=function() return now end} end
    if name=='LrApplication' then return {versionString=function() return '15.6' end} end
    error(name)
end
H={}
'''


class FeaturesTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(FIXTURE)
        source = (ROOT / 'ClaudeBridge.lrplugin/Bridge.lua').read_text()
        module = source.split('-- BEGIN SDK FEATURE MODULE\nlocal sdkFeatures = (function()\n', 1)[1].split('\nend)()\n-- END SDK FEATURE MODULE', 1)[0]
        features = self.lua.execute(module)
        features.register(self.lua.globals().H, self.lua.globals().ctx)
        self.g = self.lua.globals()

    def call(self, command, **params):
        return self.g.H[command](self.lua.table_from(params, recursive=True))

    def test_partial_point_color_preserves_other_fields(self):
        result = self.call('point_color', operation='update', index=1, swatch={'Variance': 0.5})
        swatch = result.swatches[1]
        self.assertEqual(swatch.Variance, 0.5)
        self.assertEqual(swatch.HueShift, 0.1)
        self.assertEqual(swatch.HueRange.LowerFull, 0.2)

    def test_nested_partial_range_preserves_other_limits(self):
        self.call('point_color', operation='update', index=1, swatch={'HueRange': {'LowerFull': 0.3}})
        self.assertEqual(self.g.swatches[1].HueRange.UpperFull, 0.8)

    def test_invalid_swatch_never_writes(self):
        for swatch in ({'SrcHue': 9}, {'Variance': float('nan')}, {'typo': 0.2},
                       {'HueRange': {'LowerFull': 0.9}}):
            with self.assertRaises(LuaError):
                self.call('point_color', operation='update', index=1, swatch=swatch)
        self.assertIsNone(self.g.calls.updated)

    def test_unknown_mask_cannot_receive_local_point_color(self):
        with self.assertRaises(LuaError):
            self.call('point_color', operation='update', isLocal=True, maskId='wrong', swatch={'Variance': 0.5})
        self.assertIsNone(self.g.calls.updated)

    def test_mask_visibility_is_toggled_once(self):
        self.call('mask_toggle_hidden', id='mask-1')
        self.assertEqual(self.g.calls.toggle, 1)
        self.assertIsNone(self.g.calls.maskSelect)

    def test_empty_local_point_color_is_an_empty_list(self):
        self.lua.execute('function D.getValue(k) return nil end')
        result = self.call('point_color', operation='list', isLocal=True)
        self.assertEqual(result.count, 0)

    def test_phantom_point_color_add_is_not_success(self):
        self.lua.execute('function D.addPointColorSwatch(s,l) return true end')
        with self.assertRaisesRegex(LuaError, 'count did not settle'):
            self.call('point_color', operation='add', swatch={'SrcHue':2,'SrcSat':0.5,'SrcLum':0.5})

    def test_reflection_opens_required_remove_tool(self):
        self.call('reflection_state')
        self.assertEqual(self.g.selectedTool, 'dust')

    def test_ai_state_restores_tool_and_reports_partial_coverage(self):
        result = self.call('ai_state')
        self.assertEqual(self.g.selectedTool, 'masking')
        self.assertFalse(result.completeCoverage)

    def test_component_membership_checked(self):
        with self.assertRaises(LuaError):
            self.call('mask_tool', operation='select', maskId='mask-1', toolId='wrong')
        self.assertIsNone(self.g.calls.component)
        self.call('mask_tool', operation='select', maskId='mask-1', toolId='component-1')
        self.assertEqual(self.g.calls.component, 'component-1')

    def test_reflection_timeout_is_explicit_and_not_retried(self):
        result = self.call('reflection_removal', enable=True, timeout=1)
        self.assertTrue(result.timedOut)
        self.assertEqual(result.completion, 'pending')
        self.assertEqual(self.g.calls.reflect, 1)

    def test_reflection_set_enabled_does_not_toggle_off(self):
        self.g.reflection.checkboxState = True
        result = self.call('reflection_removal', enable=True, amount=80)
        self.assertTrue(result.state.checkboxState)
        self.assertIsNone(self.g.calls.reflect)
        self.assertEqual(result.state.amount, 80)

    def test_reflection_callback_reports_completion(self):
        self.g.completeCallback = True
        result = self.call('reflection_removal', enable=True)
        self.assertFalse(result.timedOut)
        self.assertEqual(result.completion, 'callback')

    def test_unavailable_enhance_can_still_be_disabled(self):
        self.g.enhanceState.denoiseState = True
        self.g.enhanceState.denoiseEnabled = False
        self.call('enhance', feature='denoise', enable=False)
        self.assertFalse(self.g.enhanceState.denoiseState)

    def test_ai_update_does_not_claim_background_completion(self):
        result = self.call('update_ai_settings', targets='active')
        self.assertEqual(result.completion, 'not_verified')
        self.assertEqual(self.g.calls.update, 1)

    def test_capabilities_does_not_switch_modules(self):
        result = self.call('capabilities')
        self.assertEqual(result.sdk, 15.3)
        self.assertIsNone(self.g.calls.develop)
        self.assertIn('No documented', result.uiOnly.nativeDuplicateDetection)


class ClientTests(unittest.TestCase):
    def dispatch(self, *args):
        with patch.object(cli, 'call', return_value={'ok': True}) as call:
            cli.dispatch_cli(cli.build_parser().parse_args(args))
            return call.call_args.args

    def test_cli_preserves_point_color_zero_and_local_target(self):
        command, params = self.dispatch('point-color', 'update', 'Variance=0', '--index', '1', '--local', '--mask-id', 'm')
        self.assertEqual(command, 'point_color')
        self.assertEqual(params['swatch'], {'Variance': 0})
        self.assertTrue(params['isLocal'])
        self.assertEqual(params['maskId'], 'm')

    def test_cli_preset_amount_zero_and_ai_are_forwarded(self):
        command, params = self.dispatch('preset', 'apply', '--uuid', 'p', '--amount', '0', '--update-ai')
        self.assertEqual(command, 'preset_apply')
        self.assertEqual(params['amount'], 0)
        self.assertTrue(params['updateAISettings'])

    def test_cli_nested_ranges(self):
        _, params = self.dispatch('point-color', 'update', '--json', '{"HueRange":{"LowerFull":0.2}}')
        self.assertEqual(params['swatch']['HueRange']['LowerFull'], 0.2)

    def test_cli_targets_specific_mask(self):
        command, params = self.dispatch('mask', 'adjust', 'local_Exposure=0.2', '--id', 'm')
        self.assertEqual(command, 'mask_adjust')
        self.assertEqual(params['id'], 'm')

    def test_cli_component_visibility_uses_plugin_spelling(self):
        command, params = self.dispatch('mask', 'component', 'toggle-hidden', 't', '--mask-id', 'm')
        self.assertEqual(command, 'mask_tool')
        self.assertEqual(params, {'operation':'toggle_hidden', 'toolId':'t', 'maskId':'m'})

    def test_transport_waits_for_long_callbacks(self):
        self.assertEqual(_core._lr_timeout({'cmd': 'reflection_removal', 'params': {'timeout': 600}}), 610)
        self.assertGreaterEqual(_core._lr_timeout({'cmd': 'remove_people'}), 70)
        self.assertNotIn('point_color', _core._RESEND_SAFE)
        self.assertNotIn('mask_toggle_hidden', _core._RESEND_SAFE)
        self.assertNotIn('reflection_removal', _core._RESEND_SAFE)

    def test_mcp_schema_exposes_new_controls(self):
        async def inspect():
            tools = await mcp_server.mcp.list_tools()
            return {tool.name: tool.parameters for tool in tools}
        tools = asyncio.run(inspect())
        self.assertIn('point_color', tools)
        self.assertIn('capabilities', tools)
        self.assertIn('amount', tools['apply_preset']['properties'])
        self.assertIn('update_ai', tools['apply_preset']['properties'])

    def test_lua_syntax_and_manifest_version(self):
        runtime = LuaRuntime(unpack_returned_tuples=True)
        for path in (ROOT / 'ClaudeBridge.lrplugin').glob('*.lua'):
            ok, error = runtime.eval('function(s) local f,e=loadstring(s); return f~=nil,e end')(path.read_text())
            self.assertTrue(ok, f'{path}: {error}')
        info = runtime.execute((ROOT / 'ClaudeBridge.lrplugin/Info.lua').read_text())
        self.assertEqual(info.LrSdkVersion, 15.3)
        self.assertEqual(info.LrSdkMinimumVersion, 15.3)


class CoreHandlerTests(unittest.TestCase):
    """Exercise production handlers without starting sockets or touching files."""
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(FIXTURE + r'''
        values={Exposure=0,local_Exposure=0}
        module='library'; writes=0; increments=0
        photo={}
        function photo:getRawMetadata(k) if k=='uuid' then return 'photo-1' end end
        function photo:applyDevelopPreset(p,plugin,amount,ai) calls.preset={amount=amount,ai=ai} end
        function cat:getTargetPhoto() if not noSelection then return photo end end
        function cat:getTargetPhotos() return {photo} end
        function cat:withWriteAccessDo(name,f,opts) f(); return 'executed' end
        function D.getValue(k) if unreadable then return nil end; return values[k] end
        function D.getRange(k) return -5,5 end
        function D.setValue(k,v) writes=writes+1; if writes>1 or not dropFirst then values[k]=v end end
        function D.increment(k) increments=increments+1; values[k]=values[k]+1 end
        function D.getAllMasks() if maskReadError then error('SDK failure') end; return masks end
        function D.createNewMask(t,s)
            calls.create=(calls.create or 0)+1
            if not phantom then masks={{ID='new-mask',Tools={}}} end
        end
        preset={getName=function() return 'Test preset' end}
        local baseImport=import
        function import(name)
            if name=='LrPathUtils' then return {getStandardFilePath=function() return '/unused' end,
                child=function(a,b) return a..'/'..b end} end
            if name=='LrApplication' then return {activeCatalog=function() return cat end,
                developPresetByUuid=function() return preset end,versionString=function() return '15.6' end} end
            if name=='LrApplicationView' then return {
                getCurrentModuleName=function() return module end,
                switchToModule=function(m) module=m end} end
            local ok,value=pcall(baseImport,name); if ok then return value end; return {}
        end
        function require(name) return {VERSION={major=1,minor=3,revision=0}} end
        ''')
        source = (ROOT / 'ClaudeBridge.lrplugin/Bridge.lua').read_text()
        source = source.split('-- Inbound parsing + dispatch', 1)[0]
        self.h = self.lua.execute(source + '\nreturn H')
        self.g = self.lua.globals()

    def call(self, command, **params):
        return self.h[command](self.lua.table_from(params, recursive=True))

    def test_library_first_absolute_write_is_verified_and_retried(self):
        self.g.dropFirst = True
        result = self.call('set_value', param='Exposure', value=0.25)
        self.assertEqual(result.applied.Exposure, 0.25)
        self.assertEqual(self.g.writes, 2)

    def test_unready_develop_never_writes(self):
        self.g.unreadable = True
        with self.assertRaisesRegex(LuaError, 'did not become ready'):
            self.call('set_value', param='Exposure', value=1)
        self.assertEqual(self.g.writes, 0)

    def test_relative_write_is_never_replayed(self):
        self.call('set_value', param='Exposure', value='+')
        self.assertEqual(self.g.increments, 1)

    def test_phantom_mask_creation_fails_without_replay(self):
        self.g.phantom = True
        with self.assertRaisesRegex(LuaError, 'No new mask observed'):
            self.call('mask_create', type='rangeMask', subtype='luminance')
        self.assertEqual(self.g.calls.create, 1)

    def test_mask_create_handles_empty_then_uppercase_sdk_fields(self):
        self.g.masks = None
        result = self.call('mask_create', type='aiSelection', subtype='subject')
        self.assertEqual(result.createdId, 'new-mask')

    def test_mask_sdk_errors_are_not_reported_as_empty(self):
        self.g.maskReadError = True
        with self.assertRaisesRegex(LuaError, 'Mask read-back failed'):
            self.call('mask_list')

    def test_adjust_without_selected_mask_never_writes(self):
        self.g.selectedMask = None
        with self.assertRaisesRegex(LuaError, 'No mask selected'):
            self.call('mask_adjust', values={'local_Exposure':0.2})
        self.assertEqual(self.g.writes, 0)

    def test_preset_amount_zero_and_ai_reach_sdk(self):
        self.call('preset_apply', uuid='preset', amount=0, updateAISettings=True)
        self.assertEqual(self.g.calls.preset.amount, 0)
        self.assertTrue(self.g.calls.preset.ai)

    def test_invalid_scope_and_empty_selection_never_apply_preset(self):
        with self.assertRaisesRegex(LuaError, 'targets must be'):
            self.call('preset_apply', uuid='preset', targets='everything')
        self.g.noSelection = True
        with self.assertRaisesRegex(LuaError, 'No photos selected'):
            self.call('preset_apply', uuid='preset', targets='selected')
        self.assertIsNone(self.g.calls.preset)


if __name__ == '__main__':
    unittest.main()
