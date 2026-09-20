--[[----------------------------------------------------------------------------

Claude Bridge  --  Shutdown.lua

Runs when Lightroom Classic quits (LrShutdownApp) or the plug-in is reloaded
(LrShutdownPlugin). Tells the main run loop to exit and waits for it to close
its sockets cleanly so the fixed ports are released.

------------------------------------------------------------------------------]]

local LrTasks = import 'LrTasks'

return {

	LrShutdownFunction = function(doneFunction, progressFunction)
		LrTasks.startAsyncTask(function()
			if _G.LRC_RUNNING then
				local LrDate = import 'LrDate'
				local start = LrDate.currentTime()

				progressFunction(0)
				_G.LRC_RUNNING = false -- tell the run loop to terminate

				while not _G.LRC_SHUTDOWN do
					local percent = math.min(1, math.max(0, (LrDate.currentTime() - start) / 0.5))
					progressFunction(percent)
					LrTasks.sleep(0.1)
				end
			end

			progressFunction(1)
			doneFunction()
		end)
	end,

}
