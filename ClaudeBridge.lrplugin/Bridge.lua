--[[----------------------------------------------------------------------------

Claude Bridge for Adobe Lightroom Classic  --  Bridge.lua (main script)

Opens two local TCP sockets:
  * RECV_PORT  (mode "receive") : the external bridge sends command lines here.
  * SEND_PORT  (mode "send")    : this plug-in sends JSON responses back here.

Wire protocol
  Inbound  (bridge -> Lightroom) : one Lua table literal per line, e.g.
       {id=7,cmd="set",params={Exposure=0.5,Contrast=12}}
     It is parsed with loadstring in an empty sandbox environment (it can only
     build a data table, never call code).
  Outbound (Lightroom -> bridge) : one JSON object per line, e.g.
       {"id":7,"ok":true,"result":{"applied":{"Exposure":0.5}}}

Everything runs in a single async task / function context so the Develop,
catalog and export APIs are always called from a valid context.

------------------------------------------------------------------------------]]

local LrTasks            = import 'LrTasks'
local LrFunctionContext  = import 'LrFunctionContext'
local LrApplication      = import 'LrApplication'
local LrApplicationView  = import 'LrApplicationView'
local LrDevelopController = import 'LrDevelopController'
local LrSelection        = import 'LrSelection'
local LrSocket           = import 'LrSocket'
local LrPathUtils        = import 'LrPathUtils'
local LrFileUtils        = import 'LrFileUtils'
local LrDialogs          = import 'LrDialogs'
local LrUndo             = import 'LrUndo'
local LrExportSession    = import 'LrExportSession'
local LrDate             = import 'LrDate'

--==============================================================================
-- Configuration
--==============================================================================

local RECV_PORT = 49463 -- commands  : bridge -> Lightroom
local SEND_PORT = 49464 -- responses : Lightroom -> bridge

local HOME        = LrPathUtils.getStandardFilePath('home')
local BRIDGE_DIR  = LrPathUtils.child(HOME, '.claude-lrc-bridge')
local PREVIEW_DIR = LrPathUtils.child(BRIDGE_DIR, 'previews')
local HANDSHAKE   = LrPathUtils.child(BRIDGE_DIR, 'bridge.json')
local LOG_PATH    = LrPathUtils.child(BRIDGE_DIR, 'bridge.log')
local GEN_PATH    = LrPathUtils.child(BRIDGE_DIR, 'instance.gen')

local PLUGIN_VERSION = '1.1.0'

--==============================================================================
-- Minimal JSON encoder (Lua 5.1).  Used only for outbound responses; the
-- consumer is Python's json module so the output must be strict JSON.
--==============================================================================

local jsonEncode

local _escapes = {
	['"'] = '\\"', ['\\'] = '\\\\', ['\b'] = '\\b', ['\f'] = '\\f',
	['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t',
}

local function jsonString(s)
	s = string.gsub(s, '[%c"\\]', function(c)
		return _escapes[c] or string.format('\\u%04x', string.byte(c))
	end)
	return '"' .. s .. '"'
end

local function jsonNumber(n)
	if n ~= n then return 'null' end                 -- NaN
	if n == math.huge then return '1e999' end
	if n == -math.huge then return '-1e999' end
	if n == math.floor(n) and math.abs(n) < 1e15 then
		return string.format('%d', n)
	end
	return string.format('%.14g', n)
end

local function isArray(t)
	local maxIndex, count = 0, 0
	for k in pairs(t) do
		if type(k) ~= 'number' or k <= 0 or k ~= math.floor(k) then
			return false
		end
		if k > maxIndex then maxIndex = k end
		count = count + 1
	end
	return count == maxIndex, maxIndex
end

jsonEncode = function(v)
	local tp = type(v)
	if tp == 'nil' then
		return 'null'
	elseif tp == 'boolean' then
		return v and 'true' or 'false'
	elseif tp == 'number' then
		return jsonNumber(v)
	elseif tp == 'string' then
		return jsonString(v)
	elseif tp == 'table' then
		local arr, n = isArray(v)
		local parts = {}
		if arr then
			for i = 1, n do parts[i] = jsonEncode(v[i]) end
			return '[' .. table.concat(parts, ',') .. ']'
		else
			local i = 0
			for k, val in pairs(v) do
				i = i + 1
				parts[i] = jsonString(tostring(k)) .. ':' .. jsonEncode(val)
			end
			return '{' .. table.concat(parts, ',') .. '}'
		end
	end
	return 'null'
end

--==============================================================================
-- Small file / log / handshake helpers
--==============================================================================

local function writeBytes(path, data)
	local f, err = io.open(path, 'wb')
	if not f then return false, tostring(err) end
	f:write(data)
	f:close()
	return true
end

local function log(msg)
	pcall(function()
		local f = io.open(LOG_PATH, 'a')
		if f then
			f:write(string.format('%0.3f %s\n', LrDate.currentTime(), tostring(msg)))
			f:close()
		end
	end)
end

-- Cross-environment instance handoff. "Reload Plug-in" gives the new instance
-- a FRESH Lua environment, so the old instance's _G is unreachable and it
-- would keep the ports bound forever. A generation token on disk fixes that:
-- every instance stamps its own token at start, and the serving loop stops as
-- soon as the file no longer holds its token.
local function readGen()
	local f = io.open(GEN_PATH, 'r')
	if not f then return nil end
	local v = f:read('*a')
	f:close()
	return v
end

local function stampGen(token)
	pcall(function()
		LrFileUtils.createAllDirectories(BRIDGE_DIR)
		local f = io.open(GEN_PATH, 'w')
		if f then f:write(token) f:close() end
	end)
end

-- Token of the instance currently serving, readable from command handlers so
-- long-blocking waits (render) can notice a reload and yield the ports early.
local CURRENT_TOKEN = nil

local function readHandshake()
	local ok, s = pcall(function()
		local f = io.open(HANDSHAKE, 'r')
		if not f then return nil end
		local c = f:read('*a')
		f:close()
		return c
	end)
	if ok then return s end
	return nil
end

local function writeHandshake(status, token)
	pcall(function()
		LrFileUtils.createAllDirectories(BRIDGE_DIR)
		local f = io.open(HANDSHAKE, 'w')
		if f then
			f:write(jsonEncode {
				recvPort   = RECV_PORT,
				sendPort   = SEND_PORT,
				status     = status,
				gen        = token,
				lightroom  = LrApplication.versionString(),
				plugin     = PLUGIN_VERSION,
				previewDir = PREVIEW_DIR,
				updated    = LrDate.currentTime(),
			})
			f:close()
		end
	end)
end

--==============================================================================
-- Lightroom helpers
--==============================================================================

local function catalog()
	return LrApplication.activeCatalog()
end

local function photoInfo(photo)
	if not photo then return nil end
	local function raw(key)
		local ok, v = LrTasks.pcall(function() return photo:getRawMetadata(key) end)
		if ok then return v end
	end
	local function fmt(key)
		local ok, v = LrTasks.pcall(function() return photo:getFormattedMetadata(key) end)
		if ok then return v end
	end
	return {
		uuid       = raw('uuid'),
		path       = raw('path'),
		filename   = fmt('fileName'),
		rating     = raw('rating') or 0,
		pickStatus = raw('pickStatus'),
		colorLabel = fmt('label'),
		isVideo    = raw('isVideo') or false,
		dimensions = raw('croppedDimensions'),
	}
end

local function pickPhoto(p)
	local cat = catalog()
	if p and p.uuid then
		local photo = cat:findPhotoByUuid(p.uuid)
		if photo then return photo end
		error('No photo with uuid ' .. tostring(p.uuid))
	end
	local photo = cat:getTargetPhoto()
	if not photo then error('No active photo. Select a photo in Lightroom first.') end
	return photo
end

local function targetPhotos(scope)
	if scope == 'selected' or scope == 'all_selected' then
		-- getTargetPhotos() falls back to the ENTIRE filmstrip when nothing is
		-- selected; require a real selection so a batch edit can't blast every
		-- visible photo.
		if not catalog():getTargetPhoto() then error('No photos selected in Lightroom.') end
		local ps = catalog():getTargetPhotos()
		if #ps == 0 then error('No photos selected in Lightroom.') end
		return ps
	end
	return { pickPhoto() }
end

-- withWriteAccessDo with a timeout does not throw when write access can't be
-- obtained: it returns 'executed', 'queued' or 'aborted'. Treat anything but
-- 'executed' as a failure so a busy catalog can't produce a silent ok.
local function withWrite(name, fn, timeoutSecs)
	local status = catalog():withWriteAccessDo(name, fn, { timeout = timeoutSecs or 30 })
	if status ~= 'executed' then
		error(string.format('catalog write "%s" was %s (Lightroom busy?)', name, tostring(status)))
	end
end

local function ensureDevelop()
	if LrApplicationView.getCurrentModuleName() ~= 'develop' then
		LrApplicationView.switchToModule('develop')
		LrTasks.sleep(0.4)
	end
end

-- Apply a set of friendly Develop parameters to the current photo using the
-- interactive controller (handles process-version mapping automatically).
local function applyDevelopValues(values, withClipping)
	ensureDevelop()
	local applied = {}
	for k, v in pairs(values) do
		if v == '+' then
			LrDevelopController.increment(k)
		elseif v == '-' then
			LrDevelopController.decrement(k)
		elseif v == 'reset' then
			LrDevelopController.resetToDefault(k)
		else
			local num = tonumber(v)
			if num ~= nil then
				LrDevelopController.setValue(k, num, withClipping and true or false)
			else
				LrDevelopController.setValue(k, v)
			end
		end
		local ok, nv = LrTasks.pcall(function() return LrDevelopController.getValue(k) end)
		applied[k] = ok and nv or v
	end
	return applied
end

local function safeGetAllMasks()
	local ok, m = LrTasks.pcall(function() return LrDevelopController.getAllMasks() end)
	if ok then return m end
	return nil
end

-- selectMask / invertMask / deleteMask are documented to require the masking
-- tool to be OPEN (Develop active alone is not enough); make sure it is.
local function ensureMaskingTool()
	ensureDevelop()
	local ok, tool = LrTasks.pcall(function() return LrDevelopController.getSelectedTool() end)
	if not (ok and tool == 'masking') then
		LrDevelopController.selectTool('masking')
		LrTasks.sleep(0.2)
	end
end

local function previewPath(photo, tag, ext)
	local base
	local ok, uuid = LrTasks.pcall(function() return photo:getRawMetadata('uuid') end)
	if ok and uuid then base = uuid else base = tostring(math.floor(LrDate.currentTime())) end
	return LrPathUtils.child(PREVIEW_DIR, base .. '_' .. (tag or 'preview') .. '.' .. (ext or 'jpg'))
end

--==============================================================================
-- Command handlers.  Each returns a Lua value used as the JSON "result".
-- Throwing an error is fine: the dispatcher reports it as {ok=false,error=...}.
--==============================================================================

local H = {}

function H.ping(p)
	local cat = catalog()
	return {
		plugin    = 'Claude Bridge',
		version   = PLUGIN_VERSION,
		lightroom = LrApplication.versionString(),
		module    = LrApplicationView.getCurrentModuleName(),
		catalog   = cat:getPath(),
		active    = photoInfo(cat:getTargetPhoto()),
		recvPort  = RECV_PORT,
		sendPort  = SEND_PORT,
	}
end

function H.status(p)
	local cat = catalog()
	local sel = cat:getTargetPhotos()
	local list = {}
	for i = 1, math.min(#sel, 50) do list[i] = photoInfo(sel[i]) end
	return {
		module        = LrApplicationView.getCurrentModuleName(),
		catalog       = cat:getPath(),
		selectedCount = #sel,
		active        = photoInfo(cat:getTargetPhoto()),
		selected      = list,
	}
end

function H.list_selected(p)
	local sel = catalog():getTargetPhotos()
	local list = {}
	for i = 1, #sel do list[i] = photoInfo(sel[i]) end
	return { count = #sel, photos = list }
end

function H.get_settings(p)
	return pickPhoto(p):getDevelopSettings()
end

-- Capture / EXIF metadata for the target photo (active, or by uuid). Each field
-- is read through a yield-safe pcall so an unsupported key just yields nil (and
-- is omitted from the JSON) instead of failing the whole call.
function H.get_metadata(p)
	local photo = pickPhoto(p)
	local function raw(key)
		local ok, v = LrTasks.pcall(function() return photo:getRawMetadata(key) end)
		if ok then return v end
	end
	local function fmt(key)
		local ok, v = LrTasks.pcall(function() return photo:getFormattedMetadata(key) end)
		if ok then return v end
	end
	return {
		filename          = fmt('fileName'),
		path              = raw('path'),
		fileType          = fmt('fileType'),
		fileSize          = fmt('fileSize'),
		dimensions        = raw('dimensions'),
		croppedDimensions = raw('croppedDimensions'),
		cameraMake        = fmt('cameraMake'),
		cameraModel       = fmt('cameraModel'),
		lens              = fmt('lens'),
		iso               = fmt('isoSpeedRating'),
		shutterSpeed      = fmt('shutterSpeed'),
		aperture          = fmt('aperture'),
		focalLength       = fmt('focalLength'),
		focalLength35mm   = fmt('focalLength35mm'),
		exposureBias      = fmt('exposureBias'),
		exposureProgram   = fmt('exposureProgram'),
		meteringMode      = fmt('meteringMode'),
		flash             = fmt('flash'),
		-- 'whiteBalance' is not a getFormattedMetadata key; read the as-shot /
		-- current WB mode from the photo's develop settings instead.
		whiteBalance      = (function()
			local ok, ds = LrTasks.pcall(function() return photo:getDevelopSettings() end)
			if ok and type(ds) == 'table' then return ds.WhiteBalance end
		end)(),
		dateTimeOriginal  = fmt('dateTimeOriginal'),
		gps               = raw('gps'),
		rating            = raw('rating'),
		colorLabel        = fmt('label'),
		isVideo           = raw('isVideo') or false,
		-- raw numerics alongside the formatted strings, for scripting
		isoRaw            = raw('isoSpeedRating'),
		focalLengthRaw    = raw('focalLength'),
	}
end

function H.set(p)
	local values = p.values or p.params or {}
	if next(values) == nil then error('set: no values provided (e.g. {Exposure=0.5})') end
	return { applied = applyDevelopValues(values, p.clipping) }
end
H.adjust = H.set

function H.set_value(p)
	if not p.param then error('set_value: param required') end
	return { applied = applyDevelopValues({ [p.param] = p.value }, p.clipping) }
end

function H.get_value(p)
	if not p.param then error('get_value: param required') end
	ensureDevelop()
	return { param = p.param, value = LrDevelopController.getValue(p.param) }
end

function H.get_range(p)
	if not p.param then error('get_range: param required') end
	ensureDevelop()
	local mn, mx = LrDevelopController.getRange(p.param)
	return { param = p.param, min = mn, max = mx }
end

function H.apply_settings(p)
	local settings = p.settings or p.values or {}
	if next(settings) == nil then error('apply_settings: no settings provided') end
	local photos = targetPhotos(p.targets or 'active')
	local hist = p.history or 'Claude: apply settings'
	withWrite(hist, function()
		for _, ph in ipairs(photos) do ph:applyDevelopSettings(settings, hist) end
	end, 30)
	local keys = {}
	for k in pairs(settings) do keys[#keys + 1] = k end
	return { count = #photos, keys = keys }
end

function H.crop(p)
	local settings = {}
	local edge = (p.left ~= nil) or (p.top ~= nil) or (p.right ~= nil) or (p.bottom ~= nil)
	if edge then
		settings.CropLeft   = p.left   or 0
		settings.CropTop    = p.top    or 0
		settings.CropRight  = p.right  or 1
		settings.CropBottom = p.bottom or 1
	end
	if p.angle ~= nil then settings.CropAngle = p.angle end
	if next(settings) == nil then
		error('crop: provide left/top/right/bottom (0..1) and/or angle (degrees)')
	end
	local photos = targetPhotos(p.targets or 'active')
	local hist = p.history or 'Claude: crop'
	withWrite(hist, function()
		for _, ph in ipairs(photos) do ph:applyDevelopSettings(settings, hist) end
	end, 30)
	return { crop = settings, count = #photos }
end

function H.crop_reset(p)
	ensureDevelop()
	LrDevelopController.resetCrop()
	return { reset = true }
end

function H.rotate(p)
	local photo = pickPhoto(p)
	local dir = p.dir or p.direction or 'right'
	withWrite('Claude: rotate', function()
		if dir == 'left' then photo:rotateLeft() else photo:rotateRight() end
	end, 15)
	return { rotated = dir }
end

function H.white_balance(p)
	local scope = p.targets or 'active'
	if p.as then
		local photos = targetPhotos(scope)
		withWrite('Claude: white balance', function()
			for _, ph in ipairs(photos) do ph:applyDevelopSettings({ WhiteBalance = p.as }, 'Claude: white balance') end
		end, 15)
		return { whiteBalance = p.as, count = #photos }
	end
	local values = {}
	if p.temp ~= nil then values.Temperature = p.temp end
	if p.tint ~= nil then values.Tint = p.tint end
	if next(values) == nil then error('white_balance: provide temp and/or tint, or as=<preset>') end
	if scope == 'selected' or scope == 'all_selected' then
		-- Batch temp/tint: write the raw keys per photo (the controller only
		-- drives the ACTIVE photo, which used to make targets=selected a silent
		-- no-op). Kelvin Temperature/Tint apply to RAW/DNG files; JPEG/TIFF use
		-- incremental WB keys and should be balanced per photo instead.
		local photos = targetPhotos(scope)
		withWrite('Claude: white balance', function()
			for _, ph in ipairs(photos) do ph:applyDevelopSettings(values, 'Claude: white balance') end
		end, 15)
		return { applied = values, count = #photos }
	end
	return { applied = applyDevelopValues(values) }
end

function H.auto_tone(p)
	ensureDevelop()
	LrDevelopController.setAutoTone()
	return { autoTone = true }
end

function H.auto_wb(p)
	ensureDevelop()
	LrDevelopController.setAutoWhiteBalance()
	return { autoWhiteBalance = true }
end

function H.reset_all(p)
	ensureDevelop()
	LrDevelopController.resetAllDevelopAdjustments()
	return { reset = true }
end

function H.reset_param(p)
	if not p.param then error('reset_param: param required') end
	ensureDevelop()
	LrDevelopController.resetToDefault(p.param)
	return { reset = p.param }
end

function H.select_tool(p)
	if not p.tool then error('select_tool: tool required (loupe|crop|dust|redeye|masking|upright)') end
	ensureDevelop()
	LrDevelopController.selectTool(p.tool)
	return { tool = p.tool }
end

function H.enhance_state(p)
	-- Enhance panel state for the ACTIVE photo (SDK 14.5+): tells whether AI
	-- Denoise is available (denoiseEnabled) / applied (denoiseState) + amount.
	ensureDevelop()
	local ok, st = LrTasks.pcall(function() return LrDevelopController.getEnhancePanelState() end)
	if not ok or type(st) ~= 'table' then
		error('enhance_state: getEnhancePanelState failed: ' .. tostring(st))
	end
	return st
end

function H.denoise(p)
	-- Non-destructive AI Denoise on the ACTIVE photo (SDK 15.3+ setEnhance).
	-- Params: amount 1..100 (default 50), enable (default true; false removes).
	ensureDevelop()
	local amount = tonumber(p.amount) or 50
	if amount < 1 then amount = 1 elseif amount > 100 then amount = 100 end
	local enable = (p.enable ~= false)
	local ok0, st0 = LrTasks.pcall(function() return LrDevelopController.getEnhancePanelState() end)
	if ok0 and type(st0) == 'table' and st0.denoiseEnabled == false then
		error('denoise: not available for this photo (denoiseEnabled=false'
			.. (st0.denoiseInfoText and st0.denoiseInfoText ~= '' and ('; ' .. tostring(st0.denoiseInfoText)) or '')
			.. ')')
	end
	if enable and ok0 and type(st0) == 'table' and st0.denoiseState == true then
		LrDevelopController.changeDenoiseAmount(amount) -- already on: adjust amount only
	else
		LrDevelopController.setEnhance('denoise', enable, amount)
	end
	LrTasks.sleep(0.5)
	local ok1, st1 = LrTasks.pcall(function() return LrDevelopController.getEnhancePanelState() end)
	local r = { requested = { enable = enable, amount = amount } }
	if ok1 and type(st1) == 'table' then r.state = st1 end
	return r
end

function H.remove_people(p)
	-- Detect + Remove "distracting people" on the ACTIVE photo (SDK 14.5+).
	-- Opens the Remove tool, runs detection, applies removal, waits for the
	-- completion callback OR for removal spots to appear (whichever first).
	-- Params: timeout seconds (default 60). Returns spot counts as evidence.
	ensureDevelop()
	local okTool, terr = LrTasks.pcall(function() LrDevelopController.selectTool('dust') end)
	if not okTool then error('remove_people: could not open Remove tool: ' .. tostring(terr)) end
	LrTasks.sleep(0.7)
	local function countSpots(feature)
		local ok, n = LrTasks.pcall(function() return LrDevelopController.countAllSpots(feature) end)
		if ok and type(n) == 'number' then return n end
		return nil
	end
	local before = countSpots('distractingPeopleRemoval')
	local okDet, derr = LrTasks.pcall(function() LrDevelopController.detectDistractingPeople() end)
	if not okDet then error('remove_people: detectDistractingPeople failed: ' .. tostring(derr)) end
	LrTasks.sleep(2)
	local done = false
	local okApply, aerr = LrTasks.pcall(function()
		LrDevelopController.applyRemovalOnDetectedDistractingPeople(function() done = true end)
	end)
	if not okApply then error('remove_people: applyRemoval failed: ' .. tostring(aerr)) end
	local timeout = tonumber(p.timeout) or 60
	local t0 = LrDate.currentTime()
	local after = countSpots('distractingPeopleRemoval')
	while not done and (LrDate.currentTime() - t0) < timeout do
		LrTasks.sleep(1)
		after = countSpots('distractingPeopleRemoval')
		if after ~= nil and before ~= nil and after > before then break end
	end
	return {
		callbackFired = done,
		spotsBefore = before,
		spotsAfter = after,
		manualSpots = countSpots('manualRemove'),
	}
end

--------------------------------------------------------------- Masking ---------

function H.mask_create(p)
	if not p.type then error('mask_create: type required (brush|gradient|radialGradient|rangeMask|aiSelection)') end
	ensureDevelop()
	LrDevelopController.createNewMask(p.type, p.subtype)
	local r = {}
	if p.values and next(p.values) ~= nil then r.adjusted = applyDevelopValues(p.values) end
	r.masks = safeGetAllMasks()
	return r
end

function H.mask_add(p)
	if not p.type then error('mask_add: type required') end
	ensureDevelop()
	LrDevelopController.addToCurrentMask(p.type, p.subtype)
	local r = {}
	if p.values and next(p.values) ~= nil then r.adjusted = applyDevelopValues(p.values) end
	r.masks = safeGetAllMasks()
	return r
end

function H.mask_subtract(p)
	if not p.type then error('mask_subtract: type required') end
	ensureDevelop()
	LrDevelopController.subtractFromCurrentMask(p.type, p.subtype)
	local r = {}
	if p.values and next(p.values) ~= nil then r.adjusted = applyDevelopValues(p.values) end
	r.masks = safeGetAllMasks()
	return r
end

function H.mask_intersect(p)
	if not p.type then error('mask_intersect: type required') end
	ensureDevelop()
	LrDevelopController.intersectWithCurrentMask(p.type, p.subtype)
	local r = {}
	if p.values and next(p.values) ~= nil then r.adjusted = applyDevelopValues(p.values) end
	r.masks = safeGetAllMasks()
	return r
end

function H.mask_adjust(p)
	local values = p.values or {}
	if next(values) == nil then error('mask_adjust: values required (e.g. {local_Exposure=0.5})') end
	ensureDevelop()
	return { adjusted = applyDevelopValues(values) }
end

function H.mask_list(p)
	ensureDevelop()
	return { masks = safeGetAllMasks() }
end

function H.mask_select(p)
	if not p.id then error('mask_select: id required') end
	ensureMaskingTool()
	LrDevelopController.selectMask(p.id)
	return { selected = p.id }
end

function H.mask_invert(p)
	ensureMaskingTool()
	local id = p.id
	if not id then
		-- getSelectedMask() returns the mask ID as a plain STRING (not a table).
		local ok, m = LrTasks.pcall(function() return LrDevelopController.getSelectedMask() end)
		if ok and m then id = (type(m) == 'table' and m.id) or m end
	end
	if not id then error('mask_invert: no mask id given and none selected') end
	local inverted = LrDevelopController.invertMask(id)
	if inverted == false then error('mask_invert: Lightroom reported failure for id ' .. tostring(id)) end
	return { inverted = id }
end

function H.mask_delete(p)
	if not p.id then error('mask_delete: id required') end
	ensureMaskingTool()
	LrDevelopController.deleteMask(p.id)
	return { deleted = p.id, masks = safeGetAllMasks() }
end

function H.mask_reset(p)
	ensureDevelop()
	LrDevelopController.resetMasking()
	return { reset = true }
end

--------------------------------------------------------------- Presets ---------

function H.preset_list(p)
	local out = {}
	local folders = LrApplication.developPresetFolders()
	for _, folder in ipairs(folders) do
		local fname = folder:getName()
		local ok, presets = LrTasks.pcall(function() return folder:getDevelopPresets() end)
		if ok and presets then
			for _, preset in ipairs(presets) do
				out[#out + 1] = { name = preset:getName(), uuid = preset:getUuid(), folder = fname }
			end
		end
	end
	return { count = #out, presets = out }
end

function H.preset_apply(p)
	if not p.name and not p.uuid then error('preset_apply: name or uuid required') end
	local target
	if p.uuid then
		local ok, pr = LrTasks.pcall(function() return LrApplication.developPresetByUuid(p.uuid) end)
		if ok then target = pr end
	end
	if not target and p.name then
		local want = string.lower(p.name)
		for _, folder in ipairs(LrApplication.developPresetFolders()) do
			local ok, presets = LrTasks.pcall(function() return folder:getDevelopPresets() end)
			if ok and presets then
				for _, preset in ipairs(presets) do
					if string.lower(preset:getName()) == want then target = preset; break end
				end
			end
			if target then break end
		end
	end
	if not target then error('preset not found: ' .. tostring(p.name or p.uuid)) end
	local photos = targetPhotos(p.targets or 'active')
	withWrite('Claude: apply preset', function()
		for _, ph in ipairs(photos) do ph:applyDevelopPreset(target) end
	end, 30)
	return { applied = target:getName(), count = #photos }
end

--------------------------------------------------- Selection / navigation -----

function H.select_photo(p)
	local cat = catalog()
	if p.nav then
		if p.nav == 'next' then LrSelection.nextPhoto()
		elseif p.nav == 'previous' or p.nav == 'prev' then LrSelection.previousPhoto()
		elseif p.nav == 'first' then LrSelection.selectFirstPhoto()
		elseif p.nav == 'last' then LrSelection.selectLastPhoto()
		else error('select_photo: unknown nav "' .. tostring(p.nav) .. '"') end
		return { active = photoInfo(cat:getTargetPhoto()) }
	end
	local photo
	if p.uuid then photo = cat:findPhotoByUuid(p.uuid)
	elseif p.path then photo = cat:findPhotoByPath(p.path) end
	if not photo then error('select_photo: photo not found (give uuid, path, or nav)') end
	cat:setSelectedPhotos(photo, { photo })
	return { active = photoInfo(cat:getTargetPhoto()) }
end

function H.set_rating(p)
	local r = tonumber(p.value or p.rating)
	if r == nil then error('set_rating: value required (0-5)') end
	LrSelection.setRating(r)
	return { rating = r }
end

function H.set_flag(p)
	local f = p.value or p.flag
	if f == 'pick' or f == 1 or f == '1' then LrSelection.flagAsPick()
	elseif f == 'reject' or f == -1 or f == '-1' then LrSelection.flagAsReject()
	elseif f == 'none' or f == 0 or f == '0' then LrSelection.removeFlag()
	else error('set_flag: value must be pick|reject|none') end
	return { flag = f }
end

function H.set_label(p)
	local c = p.value or p.color
	if not c then error('set_label: color required (red|yellow|green|blue|purple)') end
	LrSelection.setColorLabel(c)
	return { label = c }
end

function H.switch_module(p)
	local m = p.module or p.name
	if not m then error('switch_module: module required (library|develop|map|book|slideshow|print|web)') end
	LrApplicationView.switchToModule(m)
	return { module = LrApplicationView.getCurrentModuleName() }
end

function H.undo(p)
	local can = LrUndo.canUndo()
	if can then LrUndo.undo() end
	return { undone = can == true, canUndo = LrUndo.canUndo(), canRedo = LrUndo.canRedo() }
end

function H.redo(p)
	local can = LrUndo.canRedo()
	if can then LrUndo.redo() end
	return { redone = can == true, canUndo = LrUndo.canUndo(), canRedo = LrUndo.canRedo() }
end

function H.snapshot_create(p)
	local name = p.name or ('Claude ' .. LrDate.timeToUserFormat(LrDate.currentTime(), '%Y-%m-%d %H:%M:%S'))
	local photo = pickPhoto(p)
	withWrite('Claude: snapshot', function()
		photo:createDevelopSnapshot(name, true)
	end, 15)
	return { snapshot = name }
end

function H.snapshot_list(p)
	local photo = pickPhoto(p)
	local ok, snaps = LrTasks.pcall(function() return photo:getDevelopSnapshots() end)
	if not ok then error('snapshot_list failed: ' .. tostring(snaps)) end
	local out = {}
	for _, s in ipairs(snaps or {}) do
		out[#out + 1] = { snapshotID = s.snapshotID, name = s.name, id_global = s.id_global }
	end
	return { count = #out, snapshots = out }
end

-- Restore a Develop snapshot by snapshotID or name. This is the reliable
-- rollback primitive: unlike undo, it cannot over-rewind past the edit.
function H.snapshot_apply(p)
	local photo = pickPhoto(p)
	local wanted = p.id or p.name
	if not wanted then error('snapshot_apply: give id or name (see snapshot_list)') end
	local id, name
	local ok, snaps = LrTasks.pcall(function() return photo:getDevelopSnapshots() end)
	if ok then
		for _, s in ipairs(snaps or {}) do
			if s.snapshotID == wanted or s.name == wanted then id = s.snapshotID; name = s.name; break end
		end
	end
	if not id then error('snapshot_apply: no snapshot matching "' .. tostring(wanted) .. '"') end
	withWrite('Claude: apply snapshot', function() photo:applyDevelopSnapshot(id) end, 15)
	return { applied = id, name = name }
end

function H.snapshot_delete(p)
	if not p.id then error('snapshot_delete: id required (see snapshot_list)') end
	local photo = pickPhoto(p)
	withWrite('Claude: delete snapshot', function() photo:deleteDevelopSnapshot(p.id) end, 15)
	return { deleted = p.id }
end

-- Set library metadata directly on a photo object (active, or by uuid),
-- bypassing the UI selection entirely. This is the reliable writeback path for
-- rating bursts: LrSelection-based set_rating can hit the WRONG photo when
-- select_photo goes stale mid-burst, while this cannot.
function H.set_metadata(p)
	local photo = pickPhoto(p)
	local FLAGS = { pick = 1, none = 0, reject = -1 }
	local ops = {}
	if p.rating ~= nil then
		local r = tonumber(p.rating)
		if r == nil or r < 0 or r > 5 then error('set_metadata: rating must be 0-5') end
		ops[#ops + 1] = { 'rating', (r > 0) and r or nil }
	end
	if p.flag ~= nil then
		local n = FLAGS[p.flag] or tonumber(p.flag)
		if n ~= 1 and n ~= 0 and n ~= -1 then error('set_metadata: flag must be pick|none|reject') end
		ops[#ops + 1] = { 'pickStatus', n }
	end
	if p.label ~= nil then
		-- Per the SDK, colorNameForLabel accepts red/yellow/green/blue/purple/
		-- none (case-insensitive); 'none' clears. Any OTHER string would set a
		-- literal white-label text, so validate instead of passing through.
		local c = string.lower(tostring(p.label))
		local VALID = { red = true, yellow = true, green = true, blue = true,
		                purple = true, none = true }
		if not VALID[c] then
			error('set_metadata: label must be red|yellow|green|blue|purple|none')
		end
		ops[#ops + 1] = { 'colorNameForLabel', c }
	end
	if p.title ~= nil then ops[#ops + 1] = { 'title', p.title } end
	if p.caption ~= nil then ops[#ops + 1] = { 'caption', p.caption } end
	if #ops == 0 then error('set_metadata: provide rating, flag, label, title and/or caption') end
	withWrite('Claude: set metadata', function()
		for _, kv in ipairs(ops) do photo:setRawMetadata(kv[1], kv[2]) end
	end, 15)
	-- Read back from the same photo object so the caller gets verification
	-- for free (no separate select + read round-trip).
	local function raw(key)
		local ok, v = LrTasks.pcall(function() return photo:getRawMetadata(key) end)
		if ok then return v end
	end
	local function fmt(key)
		local ok, v = LrTasks.pcall(function() return photo:getFormattedMetadata(key) end)
		if ok then return v end
	end
	return {
		uuid       = raw('uuid'),
		filename   = fmt('fileName'),
		rating     = raw('rating') or 0,
		pickStatus = raw('pickStatus'),
		colorLabel = fmt('label'),
		title      = fmt('title'),
		caption    = fmt('caption'),
	}
end

--------------------------------------------------------------- Previews --------

function H.thumb(p)
	local photo = pickPhoto(p)
	local size = tonumber(p.size) or 1600
	local outPath = p.path or previewPath(photo, p.tag or 'thumb', 'jpg')
	LrFileUtils.createAllDirectories(LrPathUtils.parent(outPath))
	local done, saved, errMsg = false, nil, nil
	local request = photo:requestJpegThumbnail(size, size, function(data, err)
		if data then
			local ok, e = writeBytes(outPath, data)
			if ok then saved = outPath else errMsg = e end
		else
			errMsg = err or 'no preview data'
		end
		done = true
	end)
	local waited = 0
	while not done and waited < 20 do
		LrTasks.sleep(0.1)
		waited = waited + 0.1
	end
	request = nil
	if not saved then error('thumb failed: ' .. tostring(errMsg)) end
	return { path = saved, requestedSize = size }
end

function H.render(p)
	local photo = pickPhoto(p)
	local size = tonumber(p.size) or 2048
	local quality = tonumber(p.quality) or 0.85
	LrFileUtils.createAllDirectories(PREVIEW_DIR)
	local settings = {
		LR_export_destinationType       = 'specificFolder',
		LR_export_destinationPathPrefix = PREVIEW_DIR,
		LR_export_useSubfolder          = false,
		LR_collisionHandling            = 'overwrite',
		LR_format                       = 'JPEG',
		LR_jpeg_quality                 = quality,
		LR_jpeg_useLimitSize            = false,
		LR_export_colorSpace            = 'sRGB',
		-- size <= 0 means native resolution (no constraint)
		LR_size_doConstrain             = size > 0,
		LR_size_resolution              = 240,
		LR_size_resolutionUnits         = 'inch',
		LR_outputSharpeningOn           = false,
		LR_minimizeEmbeddedMetadata     = true,
		LR_reimportExportedPhoto        = false,
		LR_renamingTokensOn             = true,
		LR_tokens                       = '{{image_name}}_claude',
		LR_extensionCase                = 'lowercase',
		LR_includeVideoFiles            = false,
		LR_embeddedMetadataOption       = 'all',
	}
	if size > 0 then
		settings.LR_size_maxWidth  = size
		settings.LR_size_maxHeight = size
		settings.LR_size_units     = 'pixels'
	end
	local session = LrExportSession {
		photosToExport = { photo },
		exportSettings = settings,
	}
	-- waitForRender() has no timeout and does not return until the export
	-- finishes; an export can stall indefinitely (e.g. waiting on AI-mask
	-- compute). Run it on its own task and poll a done flag here, so a stalled
	-- render fails with an error instead of freezing the single command loop --
	-- which would also strand the sockets and force a Lightroom restart.
	local limit = tonumber(p.timeout) or 90  -- seconds; keep below the CLI's 120s render timeout
	local done, outPath, failMsg = false, nil, nil
	LrTasks.startAsyncTask(function()
		local ok, err = LrTasks.pcall(function()
			for _, rendition in session:renditions() do
				local rok, pathOrMsg = rendition:waitForRender()
				if rok then outPath = pathOrMsg else failMsg = pathOrMsg end
			end
		end)
		if not ok then failMsg = tostring(err) end
		done = true
	end)
	local waited, lastSup = 0, 0
	while not done and waited < limit do
		LrTasks.sleep(0.1)
		waited = waited + 0.1
		-- A render can block the command loop for minutes; if a plug-in reload
		-- has stamped a new instance token meanwhile, abort so the loop can
		-- release the ports for the new instance (the export itself continues
		-- in the background and is harmless).
		if waited - lastSup >= 0.5 then
			lastSup = waited
			if CURRENT_TOKEN and readGen() ~= CURRENT_TOKEN then
				error('render aborted: bridge superseded by a plug-in reload '
					.. '(the export continues in the background)')
			end
		end
	end
	if not done then
		error(string.format(
			'render timed out after %gs (export stalled -- e.g. an AI mask still '
			.. 'computing; the export was left running in the background)', limit))
	end
	if not outPath then error('render failed: ' .. tostring(failMsg)) end
	return { path = outPath, requestedSize = size, quality = quality }
end

function H.help(p)
	local cmds = {}
	for k in pairs(H) do cmds[#cmds + 1] = k end
	table.sort(cmds)
	return { commands = cmds }
end

--==============================================================================
-- Inbound parsing + dispatch
--==============================================================================

local function parseLine(line)
	local chunk, err = loadstring('return ' .. line)
	if not chunk then return nil, 'parse error: ' .. tostring(err) end
	-- Sandbox the chunk into an empty environment when the host Lua exposes
	-- setfenv (it does in stock 5.1, but Lightroom's plug-in sandbox omits it).
	-- The command source is our own localhost bridge, so this is defense-in-depth.
	if setfenv then pcall(setfenv, chunk, {}) end
	local ok, val = pcall(chunk)
	if not ok then return nil, 'eval error: ' .. tostring(val) end
	if type(val) ~= 'table' then return nil, 'command is not a table' end
	return val
end

local function dispatch(obj)
	local handler = H[obj.cmd]
	if not handler then
		return { id = obj.id, ok = false, error = 'unknown command: ' .. tostring(obj.cmd) }
	end
	-- LrTasks.pcall is a yield-safe pcall: handlers may yield (sleep, module
	-- switch, withWriteAccessDo, export) which a plain pcall would forbid.
	local ok, result = LrTasks.pcall(handler, obj.params or {})
	if ok then
		return { id = obj.id, ok = true, result = result }
	end
	return { id = obj.id, ok = false, error = tostring(result) }
end

local function handleLine(line)
	line = string.gsub(line, '%s+$', '')
	if line == '' then return nil end
	local obj, perr = parseLine(line)
	if not obj then
		return jsonEncode { ok = false, error = perr }
	end
	return jsonEncode(dispatch(obj))
end

--==============================================================================
-- Socket server + main loop
--==============================================================================

local function runServer()
	LrTasks.startAsyncTask(function()

		-- If a previous instance is running IN THIS Lua environment, ask it to
		-- stop via _G and wait for it so the fixed ports are free.
		if _G.LRC_RUNNING then
			_G.LRC_RUNNING = false
			local t0 = LrDate.currentTime()
			while not _G.LRC_SHUTDOWN and (LrDate.currentTime() - t0) < 3 do
				LrTasks.sleep(0.05)
			end
		end

		-- Cross-environment handoff ("Reload Plug-in" swaps _G): stamp a fresh
		-- generation token; a previous instance polls the file and stops when
		-- its token is gone. Wait for it to report 'stopped' before binding --
		-- an in-flight catalog write can hold its loop for up to ~30s.
		math.randomseed((LrDate.currentTime() % 1e5) * 1e6)  -- fresh Lua envs share the default seed
		local myToken = string.format('%s-%.6f', tostring(math.random(1e9)), LrDate.currentTime())
		local hadOld = readGen() ~= nil
		stampGen(myToken)
		if hadOld then
			local t0 = LrDate.currentTime()
			while (LrDate.currentTime() - t0) < 35 do
				if readGen() ~= myToken then
					log('superseded while waiting for handoff; yielding')
					return
				end
				local info = readHandshake()
				if info and string.find(info, '"status":"stopped"', 1, true) then break end
				LrTasks.sleep(0.1)
			end
		end

		_G.LRC_SHUTDOWN = false
		_G.LRC_GEN = (_G.LRC_GEN or 0) + 1
		local myGen = _G.LRC_GEN
		_G.LRC_RUNNING = true

		LrFunctionContext.callWithContext('claude_bridge', function(context)
			LrFileUtils.createAllDirectories(BRIDGE_DIR)
			LrFileUtils.createAllDirectories(PREVIEW_DIR)

			local queue = {}
			local receiver, sender
			local fatal = false
			local startupPhase = true
			local bindFailed = false

			local function stillLive()
				return _G.LRC_RUNNING and myGen == _G.LRC_GEN and not fatal
			end

			-- Re-arm a listening socket after a benign event (client disconnect or
			-- idle timeout) so the next client can connect.
			local function rearm(socket)
				if stillLive() then pcall(function() socket:reconnect() end) end
			end

			-- During startup a bind failure ("failed to open") usually means the
			-- previous instance still holds the ports -- retried below, not
			-- fatal. After startup a non-timeout error is fatal: stop cleanly
			-- instead of reconnecting in a tight loop.
			local function onSockError(which, socket, err)
				if err == 'timeout' then
					rearm(socket)
				elseif startupPhase then
					bindFailed = true
					log(which .. ' bind failed (' .. tostring(err) .. '); will retry')
				else
					log(which .. ' fatal error: ' .. tostring(err))
					fatal = true
					_G.LRC_RUNNING = false
				end
			end

			local function bindSockets()
				receiver = LrSocket.bind {
					functionContext = context,
					plugin  = _PLUGIN,
					address = 'localhost',
					port    = RECV_PORT,
					mode    = 'receive',
					onConnected = function() log('receiver connected') end,
					onMessage = function(socket, message)
						-- LrSocket delivers newline-delimited messages with the
						-- newline already consumed, so enqueue each line as-is.
						if type(message) == 'string' then
							for line in string.gmatch(message, '[^\r\n]+') do
								queue[#queue + 1] = line
							end
						end
					end,
					onClosed = function(socket) rearm(socket) end,
					onError  = function(socket, err) onSockError('receiver', socket, err) end,
				}
				sender = LrSocket.bind {
					functionContext = context,
					plugin  = _PLUGIN,
					address = 'localhost',
					port    = SEND_PORT,
					mode    = 'send',
					onConnected = function() log('sender connected') end,
					onClosed = function(socket) rearm(socket) end,
					onError  = function(socket, err) onSockError('sender', socket, err) end,
				}
			end

			-- Bind with retries: the previous instance may keep the ports for a
			-- while yet (an in-flight render notices the supersede within
			-- ~0.5s, a catalog write can block its loop for up to ~30s).
			local bound = false
			for _ = 1, 30 do
				if readGen() ~= myToken then
					log('superseded during startup; yielding')
					_G.LRC_RUNNING = false
					_G.LRC_SHUTDOWN = true
					return
				end
				bindFailed = false
				bindSockets()
				LrTasks.sleep(0.6)  -- async bind errors arrive via onError
				if not bindFailed then bound = true; break end
				pcall(function() receiver:close() end)
				pcall(function() sender:close() end)
				LrTasks.sleep(1.4)
			end
			startupPhase = false
			if not bound then
				log('could not bind ports after retries; giving up')
				_G.LRC_RUNNING = false
				_G.LRC_SHUTDOWN = true
				return
			end

			CURRENT_TOKEN = myToken
			writeHandshake('running', myToken)
			log(string.format('Claude Bridge %s started (recv %d, send %d)', PLUGIN_VERSION, RECV_PORT, SEND_PORT))
			LrDialogs.showBezel('Claude Bridge running', 2)

			local lastGenCheck = 0
			while _G.LRC_RUNNING and myGen == _G.LRC_GEN do
				-- Stop when a newer instance (possibly in a fresh Lua
				-- environment after "Reload Plug-in") has stamped the gen file.
				local now = LrDate.currentTime()
				if now - lastGenCheck > 0.5 then
					lastGenCheck = now
					local g = readGen()
					if g ~= nil and g ~= myToken then
						log('newer instance stamped ' .. GEN_PATH .. '; stopping')
						break
					end
				end
				if #queue > 0 then
					local line = table.remove(queue, 1)
					log('cmd: ' .. line)
					-- yield-safe: handleLine -> dispatch -> handler may yield
					local ok, resp = LrTasks.pcall(handleLine, line)
					local out = ok and resp or jsonEncode { ok = false, error = 'internal: ' .. tostring(resp) }
					if out then
						local sent = LrTasks.pcall(function() sender:send(out .. '\n') end)
						if not sent then log('send failed') end
					end
				else
					LrTasks.sleep(0.03)
				end
			end

			-- Only write 'stopped' if we still own the handshake record: a
			-- superseding instance may already have written its 'running', and
			-- clobbering it would make the NEXT reload see a stale 'stopped'
			-- sentinel and bind too early.
			local hs = readHandshake()
			if hs and string.find(hs, '"gen":' .. jsonString(myToken), 1, true) then
				writeHandshake('stopped', myToken)
			end
			log('Claude Bridge stopping')
			pcall(function() sender:close() end)
			pcall(function() receiver:close() end)
			_G.LRC_SHUTDOWN = true
			LrDialogs.showBezel('Claude Bridge stopped', 2)
		end)
	end)
end

runServer()
