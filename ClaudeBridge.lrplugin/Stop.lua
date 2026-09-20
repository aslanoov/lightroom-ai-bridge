--[[----------------------------------------------------------------------------

Claude Bridge  --  Stop.lua

Runs when the user disables the plug-in or chooses
"File > Plug-in Extras > Claude Bridge: Stop".
Signals the main run loop in Bridge.lua to exit and close its sockets.

------------------------------------------------------------------------------]]

_G.LRC_RUNNING = false
