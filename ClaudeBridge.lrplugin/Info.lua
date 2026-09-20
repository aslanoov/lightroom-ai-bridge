--[[----------------------------------------------------------------------------

Claude Bridge for Adobe Lightroom Classic
Plug-in manifest (Info.lua)

This plug-in opens two local TCP sockets that an external process (the "lrc"
bridge CLI used by Claude) connects to in order to drive the Develop module:
crop, tone, colour grading, masking, before/after previews and more.

It is built entirely on the public Lightroom Classic SDK (15.3) namespaces
LrSocket, LrApplicationView, LrDevelopController, LrSelection, LrUndo,
LrPhoto, LrExportSession.

------------------------------------------------------------------------------]]

return {

	LrSdkVersion = 15.3,
	LrSdkMinimumVersion = 11.0, -- masking API requires 11.0+

	LrToolkitIdentifier = 'com.claude.lrbridge',
	LrPluginName = 'Claude Bridge',

	-- Main script: starts the socket server and runs until shutdown.
	LrInitPlugin = 'Bridge.lua',
	-- Start automatically when Lightroom launches.
	LrForceInitPlugin = true,

	-- Lifecycle hooks.
	LrShutdownApp = 'Shutdown.lua',
	LrShutdownPlugin = 'Shutdown.lua',
	LrDisablePlugin = 'Stop.lua',

	-- File > Plug-in Extras menu items (at least one is required by the SDK).
	LrExportMenuItems = {
		{ title = 'Claude Bridge: Show Status', file = 'Status.lua' },
		{ title = 'Claude Bridge: Start / Restart', file = 'Bridge.lua' },
		{ title = 'Claude Bridge: Stop', file = 'Stop.lua' },
	},

	VERSION = { major = 1, minor = 2, revision = 0, build = 1 },

}
