local this=debug.getinfo(1,'S').source:sub(2)
local folder=this:match('^(.*)[/\\][^/\\]+$')
local ok,err=xpcall(function() dofile(folder..'/reaper_bridge.lua') end,debug.traceback)
if not ok then
  local f=io.open(folder..'/../runtime/bootstrap-error.txt','wb')
  if f then f:write(tostring(err)); f:close() end
end
