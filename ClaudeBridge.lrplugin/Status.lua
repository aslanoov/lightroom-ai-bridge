--[[----------------------------------------------------------------------------

Claude Bridge  --  Status.lua

Shows a dialog with the current bridge status: whether the run loop is active,
which ports it listens on, where previews are written, and the active photo.
Reached via "File > Plug-in Extras > Claude Bridge: Show Status".

------------------------------------------------------------------------------]]

local LrTasks       = import 'LrTasks'
local LrDialogs     = import 'LrDialogs'
local LrApplication = import 'LrApplication'
local LrApplicationView = import 'LrApplicationView'
local LrPathUtils   = import 'LrPathUtils'

LrTasks.startAsyncTask(function()
	local home    = LrPathUtils.getStandardFilePath('home')
	local dir     = LrPathUtils.child(home, '.claude-lrc-bridge')
	local running = _G.LRC_RUNNING and 'YES' or 'no'

	local activeName = '(none selected)'
	local ok, photo = pcall(function() return LrApplication.activeCatalog():getTargetPhoto() end)
	if ok and photo then
		local okName, name = pcall(function() return photo:getFormattedMetadata('fileName') end)
		if okName and name then activeName = name end
	end

	local lines = {
		'Claude Bridge for Lightroom Classic',
		'',
		'Run loop active : ' .. running,
		'Command port    : 49463  (bridge -> Lightroom)',
		'Response port   : 49464  (Lightroom -> bridge)',
		'Current module  : ' .. tostring(LrApplicationView.getCurrentModuleName()),
		'Active photo    : ' .. activeName,
		'',
		'Bridge folder   : ' .. dir,
		'Previews folder : ' .. LrPathUtils.child(dir, 'previews'),
		'Handshake file  : ' .. LrPathUtils.child(dir, 'bridge.json'),
		'',
		'If "Run loop active" is "no", choose',
		'"Claude Bridge: Start / Restart" from this menu.',
	}

	LrDialogs.message('Claude Bridge Status', table.concat(lines, '\n'), 'info')
end)
