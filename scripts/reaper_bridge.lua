-- Local API worker. Started only with the supporter's alternate -cfgfile.
local _, this = reaper.get_action_context()
local root = this:match('^(.*)[/\\]scripts[/\\][^/\\]+$')
assert(root, 'Cannot determine supporter root')
local runtime = root .. '/runtime'
local queue = runtime .. '/queue'
local resource = runtime .. '/reaper-resource'
local function norm(s) return s:gsub('\\','/'):gsub('/$',''):lower() end
assert(norm(reaper.GetResourcePath()) == norm(resource), 'Refusing non-isolated REAPER instance')

local function encode(v, seen)
  local t = type(v)
  if t == 'nil' then return 'null' end
  if t == 'boolean' then return tostring(v) end
  if t == 'number' then
    if v ~= v or v == math.huge or v == -math.huge then return 'null' end
    return tostring(v)
  end
  if t == 'string' then
    return '"' .. v:gsub('[%z\1-\31\\"]', function(c)
      return string.format('\\u%04x', c:byte())
    end) .. '"'
  end
  if t ~= 'table' then return encode(tostring(v)) end
  seen = seen or {}
  assert(not seen[v], 'Circular response table')
  seen[v] = true
  local n, arr = 0, true
  for k in pairs(v) do n=n+1; if type(k)~='number' or k<1 or k%1~=0 then arr=false end end
  if n ~= #v then arr=false end
  local parts={}
  if arr and n>0 then
    for i=1,n do parts[#parts+1]=encode(v[i],seen) end
  else
    for k,val in pairs(v) do parts[#parts+1]=encode(tostring(k))..':'..encode(val,seen) end
  end
  seen[v]=nil
  return (arr and n>0 and '[' or '{') .. table.concat(parts,',') .. (arr and n>0 and ']' or '}')
end
local function write(path, v)
  local f=assert(io.open(path..'.tmp','wb')); f:write(encode(v)); f:close()
  os.remove(path); assert(os.rename(path..'.tmp',path))
end
local function heartbeat()
  local _, project = reaper.EnumProjects(-1,'')
  write(runtime..'/bridge-status.json', {version=reaper.GetAppVersion(),resource_path=reaper.GetResourcePath(),project=project,updated=os.time(),tracks=reaper.CountTracks(0)})
end
local last=0
local stop=false
function REAPER_SUPPORTER_STOP() stop=true end
reaper.atexit(function() os.remove(runtime..'/bridge-status.json') end)
local function loop()
  if reaper.time_precise()-last>1 then heartbeat(); last=reaper.time_precise() end
  local i=0
  while true do
    local name=reaper.EnumerateFiles(queue,i)
    if not name then break end
    local id=name:match('^([a-f0-9]+)%.request%.lua$')
    if id then
      local source=queue..'/'..name
      local running=queue..'/'..id..'.running.lua'
      if os.rename(source,running) then
        local started=reaper.time_precise()
        local ok,result=xpcall(function()
          local f,err=loadfile(running); assert(f,err); return f()
        end,debug.traceback)
        local payload={ok=ok,elapsed_seconds=reaper.time_precise()-started}
        if ok then payload.result=result else payload.error=result end
        local encoded_ok,err=pcall(write,queue..'/'..id..'.result.json',payload)
        if not encoded_ok then write(queue..'/'..id..'.result.json',{ok=false,error=tostring(err)}) end
        os.rename(running,queue..'/'..id..'.done.lua')
        heartbeat()
        break
      end
    end
    i=i+1
  end
  if stop then
    reaper.defer(function() reaper.Main_OnCommand(40004,0) end)
  else reaper.defer(loop) end
end
loop()
